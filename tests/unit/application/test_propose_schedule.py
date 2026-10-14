import asyncio
import json
import re
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.llm_client import LLMError
from metanoia_ai.application.ports.quota_repository import (
    AIFeature,
    Subscription,
    UsageLogEntry,
)
from metanoia_ai.application.ports.schedule_repository import (
    PlanningData,
    SubTaskToSchedule,
    TaskStatus,
    TaskToSchedule,
)
from metanoia_ai.application.reason_writer import ReasonWriter
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    ProposeSchedule,
    QuotaExceededError,
)
from metanoia_ai.domain.free_time import CalendarEvent
from metanoia_ai.domain.models import (
    ActualTimeSource,
    FocusSessionRecord,
    Language,
    WarningType,
    WorkingHours,
)
from tests.unit.application.fakes import (
    FakeLLMClient,
    FakeQuotaRepository,
    FakeScheduleRepository,
)

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
HOURS = WorkingHours(SAIGON, time(9), time(17), False, 10)
USER = uuid4()
DOMAIN = uuid4()


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """October 2026 in Saigon time. 19 = Monday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=SAIGON)


NOW = at(19, 8)


def good_answer(prompt: str) -> str:
    """A valid LLM answer for every slot id in the prompt."""
    ids = re.findall(r'"slot_id": "([0-9a-f-]{36})"', prompt)
    reasons = [{"slot_id": i, "reason": "Good time for this task."} for i in ids]
    return json.dumps({"reasons": reasons})


def task(
    title="Write report",
    minutes=60,
    deadline=None,
    status=TaskStatus.TODO,
    approved=False,
    subtasks=(),
    sessions=(),
    task_id=None,
) -> TaskToSchedule:
    return TaskToSchedule(
        task_id=task_id or uuid4(),
        title=title,
        domain_id=DOMAIN,
        status=status,
        deadline=deadline or at(23, 17),
        estimated_minutes=minutes,
        has_approved_slot=approved,
        subtasks=tuple(subtasks),
        sessions=tuple(sessions),
    )


def planning(*tasks, events=(), synced=True, k=Decimal("1.3")) -> PlanningData:
    return PlanningData(
        hours=HOURS,
        tasks=tuple(tasks),
        k_by_domain={DOMAIN: k},
        productive_scores={},
        fixed_tasks=(),
        calendar_events=tuple(events),
        calendar_synced=synced,
        approved_slots=(),
    )


class World:
    """Fakes + use case for one test."""

    def __init__(self, *answers, price=100_000, limit=10, used=0) -> None:
        self.quota = FakeQuotaRepository(NOW)
        self.repo = FakeScheduleRepository(self.quota)
        self.llm = FakeLLMClient(*(answers or (good_answer,)))
        self.subscription = Subscription(uuid4(), price, limit, used)
        self.quota.add_subscription(USER, self.subscription, NOW - timedelta(days=1))
        writer = ReasonWriter(
            self.llm, max_retries=2, input_price_per_1m=0.1, output_price_per_1m=0.4
        )
        self.use_case = ProposeSchedule(
            self.repo,
            self.quota,
            writer,
            initial_k=Decimal("1.3"),
            window_days=7,
            min_chunk_minutes=30,
            round_minutes=5,
            top_productive_hours=3,
            quota_daily_max=50,
            clock=lambda: NOW,
        )

    def run(self, data: PlanningData, task_ids=None, language=Language.VI):
        self.repo.planning[USER] = data
        return asyncio.run(self.use_case.execute(USER, task_ids, language))

    @property
    def calls_used(self) -> int:
        return self.quota.calls_used[self.subscription.subscription_id]

    @property
    def log_rows(self):
        return [entry for _, _, entry in self.quota.logs]


def test_t21_normal_run():
    world = World()
    result = world.run(planning(task(minutes=60)))

    assert result.proposal_id is not None
    assert result.is_feasible is True
    assert result.missing_minutes is None
    assert result.explanation == "Xếp được 1/1 task."
    (slot,) = result.slots
    assert (slot.start, slot.end, slot.proposed_minutes) == (
        at(19, 9),
        at(19, 10, 18),
        78,
    )
    assert slot.explanation == "Good time for this task."

    (saved,) = world.repo.pending(USER)
    assert saved.proposal_id == result.proposal_id
    assert saved.slots[0].k_bias_used == Decimal("1.3")
    assert world.calls_used == 1
    (log,) = world.log_rows
    assert (log.succeeded, log.quota_charged, log.feature.value) == (
        True,
        True,
        "schedule",
    )


def test_free_plan_uses_the_estimate_and_no_k():
    world = World(price=0)
    result = world.run(planning(task(minutes=60)))
    assert result.slots[0].proposed_minutes == 60
    assert world.repo.pending(USER)[0].slots[0].k_bias_used is None
    assert "1.3" not in world.llm.prompts[0]  # facts carry a percent, not K
    assert '"extra_applied": false' in world.llm.prompts[0]


def test_missing_domain_profile_uses_initial_k():
    world = World()
    data = replace(planning(task(minutes=60)), k_by_domain={})
    assert world.run(data).slots[0].proposed_minutes == 78


@pytest.mark.parametrize(
    ("limit", "used", "charged_today"),
    [(5, 5, 0), (None, 0, 50)],
    ids=["plan limit", "daily limit"],
)
def test_t22_out_of_quota(limit, used, charged_today):
    world = World(limit=limit, used=used)
    for _ in range(charged_today):
        asyncio.run(world.quota.log_usage(_charged_row(world)))
    world.repo.planning[USER] = planning(task())
    with pytest.raises(QuotaExceededError):
        asyncio.run(world.use_case.execute(USER, None, Language.VI))
    assert world.repo.planning_calls == []  # nothing read, nothing planned
    assert world.repo.proposals == {}
    assert world.llm.prompts == []


def _charged_row(world: World):
    return UsageLogEntry(
        world.subscription.subscription_id,
        AIFeature.CHAT,
        "m",
        1,
        1,
        Decimal(0),
        1,
        True,
        True,
    )


def test_daily_limit_counts_only_the_last_24_hours():
    world = World(limit=None)
    world.quota.now = NOW - timedelta(hours=25)
    for _ in range(50):
        asyncio.run(world.quota.log_usage(_charged_row(world)))
    world.quota.now = NOW
    assert world.run(planning(task())).proposal_id is not None


def test_llm_fails_three_times_slots_saved_without_reasons():
    world = World(LLMError("down", model="m"), "bad", "bad")
    result = world.run(planning(task()))
    assert result.slots[0].explanation is None
    assert world.repo.pending(USER)[0].slots[0].explanation is None
    assert world.calls_used == 0
    assert [(r.succeeded, r.quota_charged) for r in world.log_rows] == [
        (False, False)
    ] * 3


def test_llm_bad_once_then_good_charges_once():
    world = World("bad", good_answer)
    world.run(planning(task()))
    assert world.calls_used == 1
    assert [(r.succeeded, r.quota_charged) for r in world.log_rows] == [
        (False, False),
        (True, True),
    ]


def test_t26_new_run_supersedes_the_pending_proposal():
    world = World(good_answer, good_answer)
    first = world.run(planning(task()))
    second = world.run(planning(task()))
    assert [p.proposal_id for p in world.repo.pending(USER)] == [second.proposal_id]
    assert world.repo.proposals[first.proposal_id][1] == "Superseded"
    assert world.repo.slot_status[first.slots[0].slot_id] == "Cancelled"


def test_t27_task_ids_limit_the_tasks():
    world = World()
    wanted, other = task(title="A"), task(title="B")
    result = world.run(planning(wanted, other), task_ids=[wanted.task_id])
    assert {s.task_id for s in result.slots} == {wanted.task_id}


def test_t28_error_while_saving_saves_nothing():
    world = World()
    world.repo.fail_on_save = True
    with pytest.raises(RuntimeError):
        world.run(planning(task()))
    assert world.repo.proposals == {}
    assert world.calls_used == 0
    assert world.log_rows == []


def test_t29_english():
    world = World()
    result = world.run(planning(task()), language=Language.EN)
    assert result.explanation == "Scheduled 1 of 1 tasks."
    assert "Write in: English" in world.llm.prompts[0]


def test_t39_scheduled_task_is_not_scheduled_again():
    world = World()
    result = world.run(planning(task(status=TaskStatus.SCHEDULED), task(approved=True)))
    assert result.proposal_id is None
    assert result.slots == ()


def test_t40_unknown_task_ids_are_ignored():
    world = World()
    mine = task()
    result = world.run(planning(mine), task_ids=[mine.task_id, uuid4(), uuid4()])
    assert [s.task_id for s in result.slots] == [mine.task_id]
    assert result.warnings == ()


def test_t41_nothing_to_schedule():
    world = World(good_answer, good_answer)
    old = world.run(planning(task()))
    used_before = world.calls_used
    logs_before = len(world.log_rows)

    result = world.run(planning(task(status=TaskStatus.SCHEDULED)))

    assert result.proposal_id is None
    assert result.is_feasible is True
    assert result.explanation == "Không có việc nào cần xếp."
    assert (result.slots, result.warnings) == ((), ())
    assert len(world.llm.prompts) == 1  # only the first run called the LLM
    assert world.calls_used == used_before
    assert len(world.log_rows) == logs_before
    assert world.repo.pending(USER)[0].proposal_id == old.proposal_id  # untouched


def test_t42_user_without_a_plan():
    world = World()
    world.quota.subscriptions.clear()
    with pytest.raises(NoSubscriptionError):
        world.run(planning(task()))
    assert world.repo.planning_calls == []
    assert world.repo.proposals == {}


def test_t43_two_active_plans_use_the_newest():
    world = World(limit=0)  # old plan: no calls left
    newest = Subscription(uuid4(), 200_000, 100, 0)
    world.quota.add_subscription(USER, newest, NOW - timedelta(hours=1))
    world.run(planning(task()))
    assert world.quota.calls_used[newest.subscription_id] == 1
    assert world.log_rows[0].subscription_id == newest.subscription_id


def test_t44_last_call_taken_by_another_request():
    world = World()
    world.quota.lose_next_charge = True
    with pytest.raises(QuotaExceededError):
        world.run(planning(task()))
    assert world.repo.proposals == {}
    assert world.calls_used == 0
    (log,) = world.log_rows  # the LLM call is still logged, not charged
    assert (log.succeeded, log.quota_charged) == (True, False)


def test_t45_calendar_not_synced_still_blocks_events_and_warns():
    world = World()
    meeting = CalendarEvent(at(19, 9), at(19, 12), False, False, False)
    result = world.run(planning(task(), events=[meeting], synced=False))
    assert result.slots[0].start == at(19, 12)
    assert [w.warning.type for w in result.warnings] == [
        WarningType.CALENDAR_NOT_SYNCED
    ]
    assert result.is_feasible is True
    assert "Google" in result.warnings[0].message


def test_not_enough_time_is_not_feasible():
    world = World()
    late = task(minutes=600, deadline=at(19, 17))  # 780' needed, 480' free
    fine = task(minutes=30, deadline=at(20, 17))
    result = world.run(planning(late, fine))
    assert result.is_feasible is False
    assert result.missing_minutes == 300
    assert result.explanation == "Xếp được 1/2 task. 1 task không kịp deadline."
    (warning,) = result.warnings
    assert (
        warning.message == "Cần 780 phút, nhưng trước deadline chỉ còn trống 480 phút."
    )
    assert world.repo.pending(USER)[0].missing_minutes == 300


def test_overdue_task_counts_all_its_minutes_as_missing():
    world = World()
    result = world.run(planning(task(minutes=60, deadline=at(18, 17))))
    assert result.is_feasible is False
    assert result.missing_minutes == 78
    assert result.slots == ()
    assert world.llm.prompts == []  # no slots → no LLM call
    assert world.calls_used == 0  # no LLM call → no charge


def test_running_timer_blocks_time_and_task_is_not_scheduled():
    world = World()
    running = task(
        title="Running",
        minutes=120,
        sessions=[
            FocusSessionRecord(
                NOW - timedelta(minutes=10), None, ActualTimeSource.TIMER
            )
        ],
    )
    other = task(title="Other", minutes=60)
    result = world.run(planning(running, other))
    # Running: max(120 − 10, 30) × 1.3 = 143' busy from 08:00 → free from 10:25.
    assert [s.task_id for s in result.slots] == [other.task_id]
    assert result.slots[0].start == at(19, 10, 25)


def test_subtasks_become_separate_slots_in_order():
    world = World()
    first, second, done = uuid4(), uuid4(), uuid4()
    parent = task(
        subtasks=[
            SubTaskToSchedule(second, "Part 2", 30, 2, True),
            SubTaskToSchedule(first, "Part 1", 30, 1, True),
            SubTaskToSchedule(done, "Done part", 30, 3, False),
        ]
    )
    result = world.run(planning(parent))
    assert [s.subtask_id for s in result.slots] == [first, second]
    assert "Write report: Part 1" in world.llm.prompts[0]


def sent_facts(prompt: str) -> dict[str, dict]:
    """The facts the LLM got, by task title."""
    slots = json.loads(prompt.split("Slots:", 1)[1])
    return {s["task_title"]: s for s in slots}


def test_why_facts_reach_the_llm():
    # Google event 09:00-10:00. Urgent (due Tue) is placed first, at 10:00;
    # Later (due Wed) comes after it.
    world = World()
    meeting = CalendarEvent(at(19, 9), at(19, 10), False, False, False)
    urgent = task(title="Urgent", minutes=60, deadline=at(20, 17))
    later = task(title="Later", minutes=60, deadline=at(21, 17))
    world.run(planning(later, urgent, events=[meeting]))

    facts = sent_facts(world.llm.prompts[0])
    assert facts["Urgent"]["earlier_blocked_by"] == "calendar_event"
    assert facts["Urgent"]["placement"] == "earliest_free"
    assert (facts["Urgent"]["priority_rank"], facts["Urgent"]["priority_total"]) == (
        1,
        2,
    )
    assert (
        facts["Urgent"]["estimate_minutes"],
        facts["Urgent"]["planned_minutes"],
    ) == (60, 78)
    assert facts["Urgent"]["spare_hours"] == 29  # Mon 11:18 → Tue 17:00 = 29h42'
    assert facts["Later"]["earlier_blocked_by"] == "other_task"
    assert facts["Later"]["priority_rank"] == 2


def test_second_subtask_is_not_blocked_by_its_own_first_part():
    world = World()
    first, second = uuid4(), uuid4()
    parent = task(
        subtasks=[
            SubTaskToSchedule(first, "Part 1", 30, 1, True),
            SubTaskToSchedule(second, "Part 2", 30, 2, True),
        ]
    )
    world.run(planning(parent))
    facts = sent_facts(world.llm.prompts[0])
    assert facts["Write report: Part 2"]["earlier_blocked_by"] == "none"
    assert facts["Write report: Part 2"]["estimate_minutes"] == 30


def test_slot_ids_in_result_match_saved_slots():
    world = World()
    result = world.run(planning(task(), task()))
    saved = world.repo.pending(USER)[0]
    assert {s.slot_id for s in result.slots} == {s.slot_id for s in saved.slots}
    assert all(isinstance(s.slot_id, UUID) for s in result.slots)
