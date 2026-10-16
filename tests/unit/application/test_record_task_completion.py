import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from metanoia_ai.application.ports.learning_repository import (
    DomainProfile,
    KBiasLogEntry,
    TaskAlreadyLearnedError,
    TaskForLearning,
)
from metanoia_ai.application.use_cases.record_task_completion import (
    LearningResult,
    RecordTaskCompletion,
    TaskNotFoundError,
)
from metanoia_ai.domain.models import (
    ActualTimeSource,
    ConfidenceTier,
    EstimateSource,
    FocusSessionRecord,
    SkipReason,
    TaskType,
)
from tests.unit.application.fakes import FakeLearningRepository

USER = uuid4()
OTHER_USER = uuid4()
WRITING = uuid4()
TUESDAY_1PM = datetime(2026, 10, 13, 13, 0, tzinfo=UTC)

TIMER = ActualTimeSource.TIMER
SELF = ActualTimeSource.SELF_REPORTED


def session(
    minutes: float | None,
    source: ActualTimeSource = TIMER,
    confirmed: bool = True,
    day: int = 0,
) -> FocusSessionRecord:
    start = TUESDAY_1PM + timedelta(days=day)
    end = None if minutes is None else start + timedelta(minutes=minutes)
    return FocusSessionRecord(start, end, source, confirmed)


# The example from the plan: Tuesday 50' with the timer, Wednesday 40' self-reported.
PLAN_SESSIONS = (session(50), session(40, SELF, day=1))


def make_task(
    sessions=PLAN_SESSIONS,
    estimated: int | None = 60,
    task_type: TaskType = TaskType.FLEXIBLE,
    is_done: bool = True,
) -> TaskForLearning:
    return TaskForLearning(
        task_id=uuid4(),
        domain_id=WRITING,
        task_type=task_type,
        is_done=is_done,
        estimated_minutes=estimated,
        sessions=tuple(sessions),
    )


def setup(task: TaskForLearning, repo: FakeLearningRepository | None = None):
    repo = repo or FakeLearningRepository()
    repo.add_task(USER, task)
    use_case = RecordTaskCompletion(repo, alpha=0.2, initial_k=1.3, outlier_ratio=5)
    return repo, use_case


def run(use_case: RecordTaskCompletion, task_id: UUID, user_id: UUID = USER):
    return asyncio.run(use_case.execute(user_id, task_id))


# --- learning ---


def test_example_from_plan_new_profile():
    # T_est 60, sessions 50' timer + 40' self-report = 90'. New profile: K = 1.3.
    # K_new = 0.2 * 1.5 + 0.8 * 1.3 = 1.34
    task = make_task()
    repo, use_case = setup(task)

    result = run(use_case, task.task_id)

    assert result == LearningResult(learned=True, k_old=1.3, k_new=1.34, sample_index=1)
    assert repo.profiles[(USER, WRITING)] == DomainProfile(k_bias=1.34, sample_count=1)


def test_log_entry_has_every_field():
    task = make_task()
    repo, use_case = setup(task)
    run(use_case, task.task_id)

    assert repo.logs[task.task_id] == KBiasLogEntry(
        task_id=task.task_id,
        old_k=1.3,
        new_k=1.34,
        alpha=0.2,
        estimated_minutes=60,
        actual_minutes=90,
        observed_ratio=1.5,
        source=SELF,
        confidence=ConfidenceTier.MEDIUM,
        sample_index=1,
    )


def test_existing_profile_is_updated():
    # K 1.5 after 4 samples; 120' timer on a 60' estimate -> 0.2*2 + 0.8*1.5 = 1.6
    task = make_task(sessions=[session(120)])
    repo = FakeLearningRepository()
    repo.profiles[(USER, WRITING)] = DomainProfile(k_bias=1.5, sample_count=4)
    repo, use_case = setup(task, repo)

    result = run(use_case, task.task_id)

    assert result == LearningResult(learned=True, k_old=1.5, k_new=1.6, sample_index=5)
    assert repo.logs[task.task_id].confidence == ConfidenceTier.HIGH
    assert repo.profiles[(USER, WRITING)].sample_count == 5


def test_new_k_is_rounded_to_3_decimals():
    # 0.2 * (70/60) + 0.8 * 1.3 = 1.27333... -> 1.273
    task = make_task(sessions=[session(70)])
    _, use_case = setup(task)
    assert run(use_case, task.task_id).k_new == 1.273


def test_profile_is_locked_before_saving():
    task = make_task()
    repo, use_case = setup(task)
    run(use_case, task.task_id)
    assert repo.locked == [(USER, WRITING)]


# --- not found / repeated calls ---


def test_unknown_task_raises_not_found():
    _, use_case = setup(make_task())
    with pytest.raises(TaskNotFoundError):
        run(use_case, uuid4())


def test_other_users_task_raises_not_found():
    task = make_task()
    _, use_case = setup(task)
    with pytest.raises(TaskNotFoundError):
        run(use_case, task.task_id, user_id=OTHER_USER)


def test_second_call_returns_the_first_result():
    task = make_task()
    repo, use_case = setup(task)
    run(use_case, task.task_id)

    result = run(use_case, task.task_id)

    assert result == LearningResult(
        learned=False,
        reason=SkipReason.ALREADY_LEARNED,
        k_old=1.3,
        k_new=1.34,
        sample_index=1,
    )
    assert repo.profiles[(USER, WRITING)].sample_count == 1  # not learned twice


class RacingRepository(FakeLearningRepository):
    """Another request saves the same task between our check and our save."""

    def __init__(self, other_entry: KBiasLogEntry) -> None:
        super().__init__()
        self.other_entry = other_entry
        self.raced = False

    async def get_log(self, user_id, task_id):
        return self.other_entry if self.raced else None

    async def save(self, user_id, domain_id, entry):
        self.raced = True
        raise TaskAlreadyLearnedError(str(entry.task_id))


def test_race_with_another_request_returns_already_learned():
    task = make_task()
    other = KBiasLogEntry(
        task.task_id, 1.3, 1.34, 0.2, 60, 90, 1.5, SELF, ConfidenceTier.MEDIUM, 1
    )
    _, use_case = setup(task, RacingRepository(other))

    result = run(use_case, task.task_id)

    assert result.reason == SkipReason.ALREADY_LEARNED
    assert result.k_new == 1.34


# --- skipped tasks (nothing is written) ---


@pytest.mark.parametrize(
    ("task", "reason"),
    [
        pytest.param(
            make_task(task_type=TaskType.FIXED, estimated=None),
            SkipReason.FIXED_TASK,
            id="fixed",
        ),
        pytest.param(make_task(is_done=False), SkipReason.TASK_NOT_DONE, id="not-done"),
        pytest.param(make_task(sessions=[]), SkipReason.NO_ACTUAL_TIME, id="no-time"),
        pytest.param(
            make_task(sessions=[session(50), session(None)]),
            SkipReason.SESSION_RUNNING,
            id="running",
        ),
        pytest.param(
            make_task(sessions=[session(200, ActualTimeSource.AUTO_TIMEOUT, False)]),
            SkipReason.UNCONFIRMED_TIME,
            id="unconfirmed",
        ),
        pytest.param(
            make_task(sessions=[session(400)]), SkipReason.OUTLIER, id="outlier-high"
        ),
        pytest.param(
            make_task(sessions=[session(10)]), SkipReason.OUTLIER, id="outlier-low"
        ),
    ],
)
def test_skipped_tasks_write_nothing(task, reason):
    repo, use_case = setup(task)

    result = run(use_case, task.task_id)

    assert result == LearningResult(learned=False, reason=reason)
    assert repo.profiles == {}
    assert repo.logs == {}
    assert repo.locked == []


# --- FR-17b: estimates set by the AI are not learned from ---

AI = EstimateSource.AI


def test_fr17b_t1_ai_estimate_is_skipped_and_writes_nothing():
    # Done, Flexible, valid T_act (90' on 60'): would learn if the user had set 60.
    task = replace(make_task(), estimate_source=AI)
    repo, use_case = setup(task)

    result = run(use_case, task.task_id)

    assert result == LearningResult(learned=False, reason=SkipReason.AI_ESTIMATE)
    assert repo.profiles == {}
    assert repo.logs == {}
    assert repo.locked == []


@pytest.mark.parametrize(
    ("task", "reason"),
    [
        pytest.param(
            make_task(task_type=TaskType.FIXED, estimated=None),
            SkipReason.FIXED_TASK,
            id="fixed-first",
        ),
        pytest.param(make_task(is_done=False), SkipReason.TASK_NOT_DONE, id="not-done"),
        pytest.param(
            make_task(sessions=[]), SkipReason.AI_ESTIMATE, id="before-actual-time"
        ),
    ],
)
def test_fr17b_t2_check_order(task, reason):
    # Fixed → not done → ai_estimate → actual time.
    ai_task = replace(task, estimate_source=AI)
    _, use_case = setup(ai_task)
    assert run(use_case, ai_task.task_id).reason == reason


def test_fr17b_t3_user_estimate_is_learned_as_before():
    task = replace(make_task(), estimate_source=EstimateSource.USER)
    _, use_case = setup(task)
    result = run(use_case, task.task_id)
    assert result == LearningResult(learned=True, k_old=1.3, k_new=1.34, sample_index=1)


def test_task_is_the_users_estimate_by_default():
    assert make_task().estimate_source is EstimateSource.USER


def test_flexible_task_without_estimate_is_bad_data():
    task = make_task(estimated=None)
    _, use_case = setup(task)
    with pytest.raises(ValueError):
        run(use_case, task.task_id)


def test_two_new_domain_tasks_learn_one_after_the_other():
    # Decision 9: the second task starts from the first task's K, not from 1.3.
    first = make_task(sessions=[session(90)])  # ratio 1.5 -> 1.34
    second = make_task(sessions=[session(120)])  # ratio 2.0 -> 0.2*2 + 0.8*1.34
    repo, use_case = setup(first)
    repo.add_task(USER, second)

    run(use_case, first.task_id)
    result = run(use_case, second.task_id)

    assert result == LearningResult(
        learned=True, k_old=1.34, k_new=1.472, sample_index=2
    )
