"""The fake must behave like the real repository, or use case tests prove nothing."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.similar_task_repository import (
    QueryTask,
    SimilarTaskRepository,
)
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.similar_tasks import PastTask, SimilarOutcome
from tests.unit.application.fakes import FakeMemory, FakeSimilarTaskRepository

USER = uuid4()
OTHER_USER = uuid4()
ASKED = uuid4()


def past(title: str = "Landing page") -> PastTask:
    return PastTask(
        task_id=uuid4(),
        title=title,
        domain_name="Coding",
        outcome=SimilarOutcome.DONE,
        estimated_minutes=60,
        actual_minutes=90,
        finished_at=datetime(2026, 10, 10, tzinfo=UTC),
    )


def memory(task: PastTask, owner=USER, status="Done", similarity=0.9) -> FakeMemory:
    content = f"title: {task.title} | text: Coding. Done."
    return FakeMemory(owner, task, content, status, similarity)


def run(coro):
    return asyncio.run(coro)


def test_fake_matches_the_port():
    repo: SimilarTaskRepository = FakeSimilarTaskRepository()  # type check only
    assert repo is not None


def test_get_task_only_returns_the_owners_task():
    repo = FakeSimilarTaskRepository()
    task = QueryTask(
        ASKED, TaskType.FLEXIBLE, "A", "Coding", None, ZoneInfo("Asia/Ho_Chi_Minh")
    )
    repo.add_task(USER, task)
    assert run(repo.get_task(USER, ASKED)) == task
    assert run(repo.get_task(OTHER_USER, ASKED)) is None


def test_vector_search_done_only_owner_only_best_first():
    repo = FakeSimilarTaskRepository()
    low, high = past("low"), past("high")
    repo.add_memory(memory(low, similarity=0.6))
    repo.add_memory(memory(high, similarity=0.95))
    repo.add_memory(memory(past(), status="Pending"))
    repo.add_memory(memory(past(), owner=OTHER_USER))

    found = run(repo.vector_search(USER, ASKED, [0.1], limit=20))

    assert [(m.task, m.similarity) for m in found] == [(high, 0.95), (low, 0.6)]
    assert repo.vector_calls == [(USER, ASKED, [0.1], 20)]


def test_text_search_any_status_best_first_never_the_asked_task():
    repo = FakeSimilarTaskRepository()
    one_word = past("Landing")
    two_words = past("Landing page")
    pending = past("page draft")
    asked = past("Landing page")
    repo.add_memory(memory(one_word))
    repo.add_memory(memory(two_words))
    repo.add_memory(memory(pending, status="Pending"))
    repo.add_memory(memory(replace(asked, task_id=ASKED)))
    repo.add_memory(memory(past("Landing page"), owner=OTHER_USER))

    found = run(repo.text_search(USER, ASKED, "landing | page", limit=20))

    assert found == [two_words, one_word, pending]


def test_searches_respect_the_limit():
    repo = FakeSimilarTaskRepository()
    for _ in range(3):
        repo.add_memory(memory(past()))
    assert len(run(repo.vector_search(USER, ASKED, [0.1], limit=2))) == 2
    assert len(run(repo.text_search(USER, ASKED, "landing", limit=2))) == 2
