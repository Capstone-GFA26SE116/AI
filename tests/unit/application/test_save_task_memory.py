import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from metanoia_ai.application.ports.memory_repository import TaskForMemory
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.application.use_cases.save_task_memory import (
    MemoryStatus,
    SaveMemoryResult,
    SaveTaskMemory,
)
from metanoia_ai.domain.memory_text import MemoryOutcome, MemorySession, StopReason
from metanoia_ai.domain.models import TaskType
from tests.unit.application.fakes import FakeMemoryRepository

USER = uuid4()
OTHER_USER = uuid4()
MONDAY_9AM = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def session(day: int, minutes: int, reason: StopReason, note: str | None = None):
    start = MONDAY_9AM + timedelta(days=day)
    return MemorySession(start, start + timedelta(minutes=minutes), reason, note)


def make_task(
    status: str = "Done",
    task_type: TaskType = TaskType.FLEXIBLE,
    estimated: int | None = 60,
    sessions=(),
) -> TaskForMemory:
    return TaskForMemory(
        task_id=uuid4(),
        task_type=task_type,
        status=status,
        title="Landing page cho Acme",
        domain_name="Coding",
        estimated_minutes=estimated,
        client_name="Acme",
        sessions=tuple(sessions),
    )


def setup(task: TaskForMemory, model: str | None = "fake-embedding"):
    repo = FakeMemoryRepository()
    repo.add_task(USER, task)
    return repo, SaveTaskMemory(repo, model=model, max_chars=1000)


def run(use_case: SaveTaskMemory, task_id: UUID, user_id: UUID = USER):
    return asyncio.run(use_case.execute(user_id, task_id))


# --- T8, T9: Done and Skipped get one Pending row ---


def test_t8_done_task_is_queued():
    task = make_task(
        sessions=[
            session(0, 50, StopReason.INTERRUPTED, "chờ logo"),
            session(1, 40, StopReason.COMPLETED),
        ]
    )
    repo, use_case = setup(task)

    result = run(use_case, task.task_id)

    memory_id, row = repo.row_for_task(task.task_id)
    assert result == SaveMemoryResult(MemoryStatus.QUEUED, memory_id)
    assert row.status == "Pending"
    assert row.user_id == USER
    assert row.model == "fake-embedding"
    assert row.draft.outcome is MemoryOutcome.DONE
    assert (row.draft.estimated_minutes, row.draft.actual_minutes) == (60, 90)
    assert row.draft.content == (
        "title: Landing page cho [client] | text: Coding. Done. "
        "Interrupted 1 time. Notes: chờ logo."
    )


def test_t9_skipped_task_is_queued():
    task = make_task(status="Skipped")
    repo, use_case = setup(task)

    result = run(use_case, task.task_id)

    assert result.status is MemoryStatus.QUEUED
    _, row = repo.row_for_task(task.task_id)
    assert row.draft.outcome is MemoryOutcome.SKIPPED
    assert row.draft.actual_minutes == 0


def test_model_not_set_yet_still_saves_pending():
    # Plan #19: without EMBEDDING_MODEL the row is saved; the startup scan embeds it later.
    task = make_task()
    repo, use_case = setup(task, model=None)
    assert run(use_case, task.task_id).status is MemoryStatus.QUEUED
    assert repo.row_for_task(task.task_id)[1].model is None


# --- T10: Fixed or not finished → no row ---


@pytest.mark.parametrize(
    "task",
    [
        pytest.param(
            make_task(task_type=TaskType.FIXED, estimated=None), id="fixed-done"
        ),
        pytest.param(make_task(status="ToDo"), id="todo"),
        pytest.param(make_task(status="InProgress"), id="in-progress"),
        pytest.param(make_task(status="Scheduled"), id="scheduled"),
    ],
)
def test_t10_no_memory_for_fixed_or_unfinished(task):
    repo, use_case = setup(task)
    assert run(use_case, task.task_id) == SaveMemoryResult(MemoryStatus.SKIPPED)
    assert repo.rows == {}


# --- T11: second call updates the same row ---


def test_t11_second_call_updates_the_same_row():
    task = make_task(sessions=[session(0, 30, StopReason.COMPLETED, "lần 1")])
    repo, use_case = setup(task)
    first = run(use_case, task.task_id)
    memory_id = first.memory_id
    asyncio.run(
        repo.save_embedding(
            USER, memory_id, repo.rows[memory_id].draft.content, [0.1], "m"
        )
    )

    reopened = replace(
        task,
        sessions=(
            session(0, 30, StopReason.COMPLETED, "lần 1"),
            session(2, 20, StopReason.COMPLETED, "lần 2"),
        ),
    )
    repo.add_task(USER, reopened)
    second = run(use_case, task.task_id)

    assert second.memory_id == memory_id
    assert len(repo.rows) == 1
    row = repo.rows[memory_id]
    assert row.status == "Pending"
    assert row.vector is None
    assert row.draft.content.endswith("Notes: lần 1; lần 2.")


# --- T12: not the user's task ---


def test_t12_other_users_task_is_not_found():
    task = make_task()
    repo, use_case = setup(task)
    with pytest.raises(TaskNotFoundError):
        run(use_case, task.task_id, user_id=OTHER_USER)
    assert repo.rows == {}


def test_unknown_task_is_not_found():
    _, use_case = setup(make_task())
    with pytest.raises(TaskNotFoundError):
        run(use_case, uuid4())


# --- errors never escape (AC2) ---


def test_database_error_gives_failed_and_saves_nothing():
    task = make_task()
    repo, use_case = setup(task)
    repo.fail_on_upsert = True
    assert run(use_case, task.task_id) == SaveMemoryResult(MemoryStatus.FAILED)
    assert repo.rows == {}


def test_flexible_task_without_estimate_gives_failed():
    task = make_task(estimated=None)
    _, use_case = setup(task)
    assert run(use_case, task.task_id).status is MemoryStatus.FAILED
