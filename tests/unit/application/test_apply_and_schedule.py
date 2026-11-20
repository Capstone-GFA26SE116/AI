"""FR-08b-7: Apply, then schedule the new tasks (plans/FR-08b-7.md T1-T3)."""

from uuid import uuid4

import pytest

from metanoia_ai.application.use_cases.apply_and_schedule import (
    ApplyAndSchedule,
    ScheduleError,
)
from metanoia_ai.application.use_cases.apply_project_breakdown import (
    AppliedProject,
    ApplyProjectBreakdown,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    PlanFeatureError,
    ProposalResult,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.start_project_chat import (
    ConversationAppliedError,
)
from metanoia_ai.domain.models import Language
from tests.unit.application.test_project_chat import (
    NOW,
    USER,
    World,
    draft_answer,
    run,
)
from tests.unit.application.test_project_chat_continuous import MANUAL, press_plan

PROPOSAL = ProposalResult(None, True, None, "Ok", (), ())


class FakeScheduler:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple] = []

    async def execute(self, user_id, task_ids, language):
        self.calls.append((user_id, list(task_ids), language))
        if self.error:
            raise self.error
        return PROPOSAL


def apply_and_schedule(world, scheduler, language=Language.VI):
    latest = run(world.repo.latest_draft(USER, world.chat_id))
    use_case = ApplyAndSchedule(
        ApplyProjectBreakdown(world.repo, MANUAL, lambda: NOW), scheduler
    )
    return run(use_case.execute(USER, world.chat_id, latest.message_id, language))


# --- T1: apply, then schedule exactly the new tasks ---


def test_schedules_only_the_tasks_just_created():
    world = World()
    press_plan(world, draft_answer())
    scheduler = FakeScheduler()
    result = apply_and_schedule(world, scheduler, Language.EN)
    created = [t.task_id for m in result.applied.milestones for t in m.tasks]
    assert created
    assert scheduler.calls == [(USER, created, Language.EN)]
    assert result.schedule is PROPOSAL and result.schedule_error is None


class NothingNewApply:
    """An Apply whose tasks were all duplicates (edited plan): nothing created."""

    async def execute(self, user_id, conversation_id, message_id, edited=None):
        return AppliedProject(milestones=[], skipped=[])


def test_nothing_new_means_no_schedule_call():
    scheduler = FakeScheduler()
    use_case = ApplyAndSchedule(NothingNewApply(), scheduler)
    result = run(use_case.execute(USER, uuid4(), uuid4(), Language.VI))
    assert scheduler.calls == []
    assert result.schedule is None and result.schedule_error is None


# --- T2: a schedule error keeps the applied work ---


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (QuotaExceededError("x"), ScheduleError.QUOTA_EXCEEDED),
        (NoSubscriptionError("x"), ScheduleError.NO_ACTIVE_PLAN),
        (PlanFeatureError("schedule"), ScheduleError.PLAN_FEATURE_LOCKED),
        (RuntimeError("db down"), ScheduleError.FAILED),
    ],
)
def test_schedule_errors_keep_the_work(error, code):
    world = World()
    press_plan(world, draft_answer())
    result = apply_and_schedule(world, FakeScheduler(error))
    assert result.schedule is None and result.schedule_error is code
    assert result.applied.milestones  # created and kept
    assert any(m.is_applied for m in world.messages())


# --- T3: an Apply error stops before scheduling ---


def test_apply_error_is_raised_and_nothing_is_scheduled():
    world = World()
    press_plan(world, draft_answer())
    apply_and_schedule(world, FakeScheduler())
    scheduler = FakeScheduler()
    with pytest.raises(ConversationAppliedError):  # the same draft twice
        apply_and_schedule(world, scheduler)
    assert scheduler.calls == []
