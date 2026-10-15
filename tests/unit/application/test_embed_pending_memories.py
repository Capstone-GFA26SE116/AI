import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from metanoia_ai.application.ports.embedding_client import EmbeddingError
from metanoia_ai.application.ports.quota_repository import AIFeature, Subscription
from metanoia_ai.application.use_cases.embed_pending_memories import (
    EmbedOutcome,
    EmbedPendingMemories,
    estimate_tokens,
)
from metanoia_ai.domain.memory_text import MemoryDraft, MemoryOutcome
from tests.unit.application.fakes import (
    FakeEmbeddingClient,
    FakeMemoryRepository,
    FakeQuotaRepository,
)

USER = uuid4()
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
DIMS = 4  # small vectors keep tests readable; the real value is 1536
GOOD = [0.1, 0.2, 0.3, 0.4]
SUBSCRIPTION = Subscription(uuid4(), price=0, ai_call_limit=50, ai_calls_used=7)


def draft(content: str = "title: A | text: Coding. Done.") -> MemoryDraft:
    return MemoryDraft(content, MemoryOutcome.DONE, 60, 90)


class Setup:
    def __init__(self, *answers, with_plan: bool = True, input_tokens: int | None = 12):
        self.memory = FakeMemoryRepository()
        self.quota = FakeQuotaRepository(now=NOW)
        if with_plan:
            self.quota.add_subscription(USER, SUBSCRIPTION, start_at=NOW)
        self.client = FakeEmbeddingClient(*answers, input_tokens=input_tokens)
        self.waits: list[float] = []
        self.use_case = EmbedPendingMemories(
            self.memory,
            self.quota,
            self.client,
            dimensions=DIMS,
            price_per_1m=0.15,
            now=lambda: NOW,
            sleep=self.fake_sleep,
        )

    async def fake_sleep(self, seconds: float) -> None:
        self.waits.append(seconds)

    def add(self, content: str = "title: A | text: Coding. Done."):
        return asyncio.run(self.memory.upsert(USER, uuid4(), draft(content), "m"))

    def embed(self, memory_id):
        return asyncio.run(self.use_case.embed_one(USER, memory_id))

    def usage(self):
        return [entry for _, _, entry in self.quota.logs]


# --- T13: success ---


def test_t13_success_saves_vector_and_logs_one_free_call():
    s = Setup(GOOD)
    memory_id = s.add()

    assert s.embed(memory_id) is EmbedOutcome.DONE

    row = s.memory.rows[memory_id]
    assert (row.status, row.vector, row.model) == (
        "Done",
        tuple(GOOD),
        "fake-embedding",
    )
    [entry] = s.usage()
    assert entry.feature is AIFeature.EMBEDDING
    assert entry.subscription_id == SUBSCRIPTION.subscription_id
    assert (entry.input_tokens, entry.output_tokens) == (12, 0)
    assert entry.cost == Decimal(12) * Decimal("0.15") / Decimal(1_000_000)
    assert entry.succeeded is True
    assert entry.quota_charged is False
    assert s.quota.calls_used[SUBSCRIPTION.subscription_id] == 7  # quota untouched
    assert s.client.texts == ["title: A | text: Coding. Done."]  # sent as stored


def test_missing_token_count_is_estimated():
    s = Setup(GOOD, input_tokens=None)
    content = "x" * 41  # ceil(41 / 4) = 11
    s.embed(s.add(content))
    assert s.usage()[0].input_tokens == 11 == estimate_tokens(content)


# --- T14: two failures then success ---


def test_t14_retries_then_succeeds():
    s = Setup(EmbeddingError("down"), EmbeddingError("down"), GOOD)
    memory_id = s.add()

    assert s.embed(memory_id) is EmbedOutcome.DONE

    assert len(s.client.texts) == 3
    assert s.waits == [1.0, 2.0]
    assert [e.succeeded for e in s.usage()] == [False, False, True]
    assert s.memory.rows[memory_id].status == "Done"


# --- T15: three failures ---


def test_t15_three_failures_mark_failed():
    s = Setup(EmbeddingError("a"), EmbeddingError("b"), EmbeddingError("c"))
    memory_id = s.add()

    assert s.embed(memory_id) is EmbedOutcome.FAILED

    row = s.memory.rows[memory_id]
    assert (row.status, row.vector) == ("Failed", None)
    assert len(s.client.texts) == 3
    assert s.waits == [1.0, 2.0]  # no wait after the last call
    assert [e.succeeded for e in s.usage()] == [False, False, False]


# --- T16: wrong number of dimensions ---


def test_t16_wrong_dimensions_count_as_a_failure():
    s = Setup([0.1, 0.2], [0.1, 0.2], [0.1, 0.2])
    memory_id = s.add()

    assert s.embed(memory_id) is EmbedOutcome.FAILED

    assert s.memory.rows[memory_id].vector is None
    assert [e.succeeded for e in s.usage()] == [False, False, False]


def test_t16_wrong_then_right_dimensions():
    s = Setup([0.1], GOOD)
    assert s.embed(s.add()) is EmbedOutcome.DONE


# --- T17: no Active plan ---


def test_t17_no_plan_still_embeds_but_logs_nothing():
    s = Setup(GOOD, with_plan=False)
    memory_id = s.add()

    assert s.embed(memory_id) is EmbedOutcome.DONE

    assert s.memory.rows[memory_id].status == "Done"
    assert s.quota.logs == []


# --- T18: text changed while embedding ---


def test_t18_newer_text_is_not_overwritten_by_an_old_vector():
    s = Setup()
    memory_id = s.add("old text")

    def task_reopened_meanwhile(text):
        row = s.memory.rows[memory_id]
        s.memory.rows[memory_id] = replace(row, draft=draft("new text"))
        return GOOD

    s.client.answers = [task_reopened_meanwhile]

    assert s.embed(memory_id) is EmbedOutcome.TEXT_CHANGED

    row = s.memory.rows[memory_id]
    assert (row.draft.content, row.status, row.vector) == ("new text", "Pending", None)
    assert len(s.usage()) == 1  # the call still cost money


def test_t18_failure_does_not_mark_a_newer_text_failed():
    s = Setup()
    memory_id = s.add("old text")

    def change_then_fail(text):
        row = s.memory.rows[memory_id]
        s.memory.rows[memory_id] = replace(row, draft=draft("new text"))
        raise EmbeddingError("down")

    s.client.answers = [change_then_fail, EmbeddingError("x"), EmbeddingError("y")]

    assert s.embed(memory_id) is EmbedOutcome.TEXT_CHANGED
    assert s.memory.rows[memory_id].status == "Pending"


# --- not needed ---


def test_done_or_unknown_row_is_not_embedded_again():
    s = Setup(GOOD, GOOD)
    memory_id = s.add()
    s.embed(memory_id)

    assert s.embed(memory_id) is EmbedOutcome.NOT_NEEDED
    assert asyncio.run(s.use_case.embed_one(USER, uuid4())) is EmbedOutcome.NOT_NEEDED
    assert len(s.client.texts) == 1


def test_other_users_row_is_not_embedded():
    s = Setup(GOOD)
    memory_id = s.add()
    assert asyncio.run(s.use_case.embed_one(uuid4(), memory_id)) is (
        EmbedOutcome.NOT_NEEDED
    )
    assert s.client.texts == []


# --- T19: startup scan ---


def test_t19_startup_scan_embeds_pending_and_failed_only():
    s = Setup(GOOD, GOOD, GOOD, GOOD)
    pending = [s.add(f"p{i}") for i in range(3)]
    failed = s.add("f")
    asyncio.run(s.memory.mark_failed(USER, failed, "f"))
    done = s.add("d")
    asyncio.run(s.memory.save_embedding(USER, done, "d", GOOD, "m"))

    outcomes = asyncio.run(s.use_case.embed_waiting())

    assert outcomes == [EmbedOutcome.DONE] * 4
    assert sorted(s.client.texts) == ["f", "p0", "p1", "p2"]
    assert all(s.memory.rows[m].status == "Done" for m in [*pending, failed])


def test_startup_scan_respects_the_limit():
    s = Setup(GOOD, GOOD)
    for i in range(3):
        s.add(f"p{i}")
    assert len(asyncio.run(s.use_case.embed_waiting(limit=2))) == 2
