"""FR-23 use case: access rules, candidates, ranking data, reasons and quota (T5–T10)."""

import asyncio
import json
import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.assignee_reason_writer import AssigneeReasonWriter
from metanoia_ai.application.ports.group_assignee_repository import (
    GroupMember,
    GroupTaskInfo,
    GroupTaskStatus,
    GroupWork,
)
from metanoia_ai.application.ports.llm_client import LLMError
from metanoia_ai.application.ports.quota_repository import AIFeature, Subscription
from metanoia_ai.application.ports.schedule_repository import (
    PlanningData,
    TaskStatus,
    TaskToSchedule,
)
from metanoia_ai.application.use_cases.suggest_group_assignee import (
    GroupArchivedError,
    GroupTaskClosedError,
    GroupTaskNotFoundError,
    NotGroupLeaderError,
    ReasonSkip,
    SuggestGroupAssignee,
)
from metanoia_ai.domain.assignee_ranking import GroupRole
from metanoia_ai.domain.free_time import CalendarEvent, DailyBreak
from metanoia_ai.domain.models import Language, TimeRange, WorkingHours
from tests.unit.application.fakes import (
    FakeGroupAssigneeRepository,
    FakeLLMClient,
    FakeQuotaRepository,
    FakeScheduleRepository,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 11, 23, 8, tzinfo=VN)  # Monday
LEADER, MEMBER_A, MEMBER_B, OUTSIDER = (UUID(int=n) for n in (1, 2, 3, 9))
GROUP, TASK, CODING = UUID(int=100), UUID(int=200), UUID(int=300)
HOURS = WorkingHours(VN, time(8), time(17), False, 15)


def run(coro):
    return asyncio.run(coro)


def planning(**changes: object) -> PlanningData:
    values: dict[str, object] = {
        "hours": HOURS,
        "tasks": (),
        "k_by_domain": {},
        "productive_scores": {},
        "fixed_tasks": (),
        "calendar_events": (),
        "calendar_synced": True,
        "approved_slots": (),
    }
    values.update(changes)
    return PlanningData(**values)  # type: ignore[arg-type]


def task_info(**changes: object) -> GroupTaskInfo:
    values: dict[str, object] = {
        "task_id": TASK,
        "group_id": GROUP,
        "group_archived": False,
        "caller_role": None,
        "title": "Design the landing page for anh.nguyen@example.com",
        "status": GroupTaskStatus.TODO,
        "deadline": NOW + timedelta(days=2),  # Wednesday 08:00 → 18 h of work
        "estimate_minutes": 300,
        "fixed_time": None,
        "assignee_id": None,
    }
    values.update(changes)
    return GroupTaskInfo(**values)  # type: ignore[arg-type]


def reasons_for(prompt: str) -> str:
    """A valid answer for every member_id the prompt contains."""
    ids = re.findall(r'"member_id": "([0-9a-f-]{36})"', prompt)
    return json.dumps(
        {
            "reasons": [
                {"member_id": i, "reason": "Có nhiều thời gian rảnh."} for i in ids
            ]
        }
    )


class World:
    def __init__(self, *answers: object, calls_left: int | None = 10) -> None:
        self.quota = FakeQuotaRepository(NOW)
        self.schedules = FakeScheduleRepository(self.quota)
        self.groups = FakeGroupAssigneeRepository(self.quota)
        self.llm = FakeLLMClient(*answers)  # type: ignore[arg-type]
        self.groups.tasks[TASK] = task_info()
        for user, role in (
            (LEADER, GroupRole.LEADER),
            (MEMBER_A, GroupRole.MEMBER),
            (MEMBER_B, GroupRole.MEMBER),
        ):
            self.groups.roles[(GROUP, user)] = role
            self.schedules.planning[user] = planning()
        self.groups.members[GROUP] = [
            GroupMember(LEADER, GroupRole.LEADER),
            GroupMember(MEMBER_A, GroupRole.MEMBER),
            GroupMember(MEMBER_B, GroupRole.MEMBER),
        ]
        if calls_left is not None:
            self.subscription = Subscription(UUID(int=500), 99_000, 10, 10 - calls_left)
            self.quota.add_subscription(LEADER, self.subscription, NOW - timedelta(1))

    def use_case(self, **changes: object) -> SuggestGroupAssignee:
        values: dict[str, object] = {
            "initial_k": Decimal("1.3"),
            "window_days": 7,
            "max_candidates": 20,
            "reason_count": 3,
            "clock": lambda: NOW,
        }
        values.update(changes)
        writer = AssigneeReasonWriter(
            self.llm, max_retries=1, input_price_per_1m=0.1, output_price_per_1m=0.4
        )
        return SuggestGroupAssignee(
            self.groups, self.schedules, self.quota, writer, **values
        )  # type: ignore[arg-type]

    def suggest(self, caller: UUID = LEADER, domain: UUID | None = None, **kw):
        return run(self.use_case(**kw).execute(caller, TASK, domain, Language.VI))


# --- T5: access ---


def test_outsider_and_missing_task_get_not_found():
    world = World()
    with pytest.raises(GroupTaskNotFoundError):
        world.suggest(OUTSIDER)
    world.groups.tasks.clear()
    with pytest.raises(GroupTaskNotFoundError):
        world.suggest(LEADER)


def test_member_who_is_not_leader_is_refused():
    with pytest.raises(NotGroupLeaderError):
        World().suggest(MEMBER_A)


def test_archived_group_and_closed_task_are_refused():
    world = World()
    world.groups.tasks[TASK] = task_info(group_archived=True)
    with pytest.raises(GroupArchivedError):
        world.suggest()
    for status in (GroupTaskStatus.DONE, GroupTaskStatus.SKIPPED):
        world.groups.tasks[TASK] = task_info(status=status)
        with pytest.raises(GroupTaskClosedError):
            world.suggest()


# --- T6: candidates ---


def test_candidates_are_limited_and_an_empty_group_needs_no_llm():
    world = World(reasons_for)
    result = world.suggest(max_candidates=2)
    assert {s.user_id for s in result.suggestions} == {LEADER, MEMBER_A}

    world.groups.members[GROUP] = []
    empty = world.suggest()
    assert empty.suggestions == () and empty.reasons_skipped is None
    assert len(world.llm.prompts) == 1  # only the first request called the LLM


# --- ranking data ---


def test_busy_member_goes_last_and_free_time_drives_the_order():
    world = World(reasons_for)
    fixed = TimeRange(NOW + timedelta(hours=2), NOW + timedelta(hours=3))
    world.groups.tasks[TASK] = task_info(fixed_time=fixed)
    # Leader: a Google event at the fixed time → busy.
    world.schedules.planning[LEADER] = planning(
        calendar_events=(CalendarEvent(fixed.start, fixed.end, False, False, False),)
    )
    # Member A: 4 h of group work → less free time than Member B.
    world.groups.work[MEMBER_A] = GroupWork((), 240)

    result = world.suggest()
    order = [s.user_id for s in result.suggestions]
    assert order == [MEMBER_B, MEMBER_A, LEADER]
    leader = result.suggestions[2]
    assert leader.busy_at_fixed_time and leader.rank == 3
    a = result.suggestions[1]
    assert a.group_work_minutes == 240
    assert a.free_minutes == 18 * 60 - 240  # 2 work days minus A's group work
    assert world.groups.work_calls[0][3] == TASK  # the asked task is left out


def test_window_is_the_deadline_or_window_days():
    world = World(reasons_for)
    world.suggest()
    assert world.schedules.planning_calls[0][2] == NOW + timedelta(days=2)
    world.groups.tasks[TASK] = task_info(deadline=None)
    world.suggest()
    assert world.schedules.planning_calls[-1][2] == NOW + timedelta(days=7)
    world.groups.tasks[TASK] = task_info(deadline=NOW - timedelta(days=1))  # overdue
    world.suggest()
    assert world.schedules.planning_calls[-1][2] == NOW + timedelta(days=7)


def test_k_of_the_chosen_category_decides_fits():
    world = World(reasons_for)
    world.groups.tasks[TASK] = task_info(estimate_minutes=500)  # 18 h free
    world.schedules.planning[MEMBER_A] = planning(k_by_domain={CODING: Decimal("2.5")})
    result = world.suggest(domain=CODING)
    fits = {s.user_id: s.fits for s in result.suggestions}
    assert fits[MEMBER_A] is False  # 500 × 2.5 = 1250 > 1080
    assert fits[MEMBER_B] is True  # no K yet → 1.3 → 650
    no_domain = world.suggest()
    assert {s.fits for s in no_domain.suggestions} == {True}


def test_no_estimate_gives_unknown_fit():
    world = World(reasons_for)
    world.groups.tasks[TASK] = task_info(estimate_minutes=None)
    assert {s.fits for s in world.suggest().suggestions} == {None}


def test_unscheduled_personal_work_and_lunch_reduce_free_time():
    world = World(reasons_for)
    personal = TaskToSchedule(
        task_id=UUID(int=900),
        title="Private client work",
        domain_id=CODING,
        status=TaskStatus.TODO,
        deadline=NOW + timedelta(days=1),
        estimated_minutes=120,
        has_approved_slot=False,
        subtasks=(),
        sessions=(),
    )
    world.schedules.planning[MEMBER_A] = planning(tasks=(personal,))
    lunch = DailyBreak(time(12), time(13))
    result = world.suggest(breaks=(lunch,))
    free = {s.user_id: s.free_minutes for s in result.suggestions}
    assert free[MEMBER_B] == 18 * 60 - 2 * 60  # two lunches
    assert free[MEMBER_A] == free[MEMBER_B] - 120


def test_current_assignee_is_marked():
    world = World(reasons_for)
    world.groups.tasks[TASK] = task_info(assignee_id=MEMBER_B)
    marked = [s.user_id for s in world.suggest().suggestions if s.is_current_assignee]
    assert marked == [MEMBER_B]


# --- T7-T9: reasons and quota ---


def test_reasons_for_the_top_members_charge_one_call():
    world = World(reasons_for)
    result = world.suggest(reason_count=2)
    with_reason = [s for s in result.suggestions if s.reason]
    assert [s.rank for s in with_reason] == [1, 2]
    assert result.reasons_skipped is None
    assert world.quota.calls_used[world.subscription.subscription_id] == 1
    ((owner, _, log),) = world.quota.logs
    assert owner == LEADER and log.feature is AIFeature.SCHEDULE
    assert log.succeeded and log.quota_charged


def test_bad_answers_keep_the_ranking_without_reasons_and_without_charge():
    world = World("not json", LLMError("down", model="m"))
    result = world.suggest()
    assert len(result.suggestions) == 3
    assert all(s.reason is None for s in result.suggestions)
    assert result.reasons_skipped is ReasonSkip.LLM_FAILED
    assert world.quota.calls_used[world.subscription.subscription_id] == 0
    assert [log.quota_charged for _, _, log in world.quota.logs] == [False, False]


def test_no_calls_left_or_no_plan_skips_the_llm():
    for world in (
        World(reasons_for, calls_left=0),
        World(reasons_for, calls_left=None),
    ):
        result = world.suggest()
        assert len(result.suggestions) == 3
        assert result.reasons_skipped is ReasonSkip.QUOTA
        assert world.llm.prompts == [] and world.quota.logs == []


def test_losing_the_last_call_drops_the_reasons():
    world = World(reasons_for, calls_left=1)
    world.quota.lose_next_charge = True
    result = world.suggest()
    assert result.reasons_skipped is ReasonSkip.QUOTA
    assert all(s.reason is None for s in result.suggestions)
    assert [log.quota_charged for _, _, log in world.quota.logs] == [False]


# --- T10: privacy ---


def test_prompt_has_no_names_emails_k_or_needed_minutes():
    world = World(reasons_for)
    world.schedules.planning[MEMBER_A] = planning(k_by_domain={CODING: Decimal("1.87")})
    world.suggest(domain=CODING)
    prompt = world.llm.prompts[0]
    assert "@example.com" not in prompt  # masked task title
    assert "1.87" not in prompt and "needed" not in prompt
    assert str(OUTSIDER) not in prompt
