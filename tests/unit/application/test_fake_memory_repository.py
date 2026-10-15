"""The fakes must behave like the real ones, or use case tests prove nothing."""

import asyncio
from uuid import uuid4

import pytest

from metanoia_ai.application.ports.embedding_client import (
    EmbeddingClient,
    EmbeddingError,
)
from metanoia_ai.application.ports.memory_repository import (
    MemoryRepository,
    PendingMemory,
    TaskForMemory,
)
from metanoia_ai.domain.memory_text import MemoryDraft, MemoryOutcome
from metanoia_ai.domain.models import TaskType
from tests.unit.application.fakes import FakeEmbeddingClient, FakeMemoryRepository

USER = uuid4()
OTHER_USER = uuid4()


def draft(content: str = "title: A | text: Coding. Done.") -> MemoryDraft:
    return MemoryDraft(content, MemoryOutcome.DONE, 60, 90)


def make_task() -> TaskForMemory:
    return TaskForMemory(
        task_id=uuid4(),
        task_type=TaskType.FLEXIBLE,
        status="Done",
        title="A",
        domain_name="Coding",
        estimated_minutes=60,
        client_name=None,
        sessions=(),
    )


def run(coro):
    return asyncio.run(coro)


def test_fakes_match_the_ports():
    repo: MemoryRepository = FakeMemoryRepository()  # type check only
    client: EmbeddingClient = FakeEmbeddingClient()
    assert repo is not None and client is not None


def test_get_task_only_returns_the_owners_task():
    repo = FakeMemoryRepository()
    task = make_task()
    repo.add_task(USER, task)
    assert run(repo.get_task(USER, task.task_id)) == task
    assert run(repo.get_task(OTHER_USER, task.task_id)) is None


def test_upsert_keeps_one_row_per_task_and_resets_it():
    repo = FakeMemoryRepository()
    task_id = uuid4()
    first = run(repo.upsert(USER, task_id, draft("old"), "m1"))
    run(repo.save_embedding(USER, first, "old", [0.1], "m1"))

    second = run(repo.upsert(USER, task_id, draft("new"), "m1"))

    assert second == first
    assert len(repo.rows) == 1
    row = repo.rows[first]
    assert (row.draft.content, row.status, row.vector) == ("new", "Pending", None)


def test_get_pending_is_limited_to_the_owner_and_skips_done():
    repo = FakeMemoryRepository()
    memory_id = run(repo.upsert(USER, uuid4(), draft("t"), None))
    assert run(repo.get_pending(USER, memory_id)) == PendingMemory(memory_id, USER, "t")
    assert run(repo.get_pending(OTHER_USER, memory_id)) is None
    run(repo.save_embedding(USER, memory_id, "t", [0.1], "m"))
    assert run(repo.get_pending(USER, memory_id)) is None


def test_save_embedding_only_when_text_is_unchanged():
    repo = FakeMemoryRepository()
    memory_id = run(repo.upsert(USER, uuid4(), draft("new text"), None))
    assert run(repo.save_embedding(USER, memory_id, "old text", [0.1], "m")) is False
    assert repo.rows[memory_id].status == "Pending"
    assert run(repo.save_embedding(USER, memory_id, "new text", [0.1], "m")) is True
    assert repo.rows[memory_id].status == "Done"


def test_mark_failed_only_when_text_is_unchanged():
    repo = FakeMemoryRepository()
    memory_id = run(repo.upsert(USER, uuid4(), draft("new text"), None))
    assert run(repo.mark_failed(USER, memory_id, "old text")) is False
    assert run(repo.mark_failed(USER, memory_id, "new text")) is True
    assert repo.rows[memory_id].status == "Failed"


def test_list_waiting_is_oldest_first_skips_done_and_respects_limit():
    repo = FakeMemoryRepository()
    ids = [run(repo.upsert(USER, uuid4(), draft(f"t{i}"), None)) for i in range(4)]
    run(repo.save_embedding(USER, ids[1], "t1", [0.1], "m"))  # Done: skipped
    run(repo.mark_failed(USER, ids[2], "t2"))  # Failed: still waiting
    assert run(repo.list_waiting(limit=10)) == [
        (ids[0], USER),
        (ids[2], USER),
        (ids[3], USER),
    ]
    assert run(repo.list_waiting(limit=2)) == [(ids[0], USER), (ids[2], USER)]


def test_transaction_rolls_back_on_error():
    repo = FakeMemoryRepository()

    async def save_then_fail():
        async with repo.transaction():
            await repo.upsert(USER, uuid4(), draft(), None)
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run(save_then_fail())
    assert repo.rows == {}


def test_fake_embedding_client_answers_in_order():
    client = FakeEmbeddingClient(EmbeddingError("down"), [0.1, 0.2])
    with pytest.raises(EmbeddingError):
        run(client.embed("a"))
    response = run(client.embed("b"))
    assert response.vector == [0.1, 0.2]
    assert response.model == "fake-embedding"
    assert client.texts == ["a", "b"]
