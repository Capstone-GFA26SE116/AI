import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.embedding_client import (
    EmbeddingError,
    EmbeddingResponse,
)
from metanoia_ai.application.ports.quota_repository import AIFeature, Subscription
from metanoia_ai.application.ports.similar_task_repository import QueryTask
from metanoia_ai.application.use_cases.find_similar_tasks import (
    FindSimilarTasks,
    SearchMode,
)
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.similar_tasks import MatchSource, PastTask, SimilarOutcome
from tests.unit.application.fakes import (
    FakeEmbeddingClient,
    FakeMemory,
    FakeQuotaRepository,
    FakeSimilarTaskRepository,
)

USER = uuid4()
OTHER_USER = uuid4()
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
DIMS = 4
GOOD = [0.1, 0.2, 0.3, 0.4]
SUBSCRIPTION = Subscription(uuid4(), price=0, ai_call_limit=50, ai_calls_used=7)
QUERY = "task: search result | query: Landing page cho [client]. Coding"
TERMS = "landing | page | cho | coding"


def asked(task_type: TaskType = TaskType.FLEXIBLE) -> QueryTask:
    return QueryTask(
        uuid4(), task_type, "Landing page cho Acme", "Coding", "Acme", SAIGON
    )


def past(
    title: str, outcome=SimilarOutcome.DONE, actual: int = 90, domain="Coding"
) -> PastTask:
    return PastTask(
        task_id=uuid4(),
        title=title,
        domain_name=domain,
        outcome=outcome,
        estimated_minutes=60,
        actual_minutes=actual,
        finished_at=NOW,
    )


class SlowClient:
    """Answers after `seconds` (real asyncio time)."""

    model = "slow-embedding"

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    async def embed(self, text: str) -> EmbeddingResponse:
        await asyncio.sleep(self.seconds)
        return EmbeddingResponse(GOOD, self.model, 5)


class Setup:
    def __init__(self, *answers, client="fake", with_plan=True, timeout=1.5):
        self.repo = FakeSimilarTaskRepository()
        self.quota = FakeQuotaRepository(now=NOW)
        if with_plan:
            self.quota.add_subscription(USER, SUBSCRIPTION, start_at=NOW)
        self.client = FakeEmbeddingClient(*answers) if client == "fake" else client
        self.task = asked()
        self.repo.add_task(USER, self.task)
        self.use_case = FindSimilarTasks(
            self.repo,
            self.quota,
            self.client,
            dimensions=DIMS,
            price_per_1m=0.15,
            min_similarity=0.5,
            rrf_k=60,
            branch_limit=20,
            embed_timeout_seconds=timeout,
            now=lambda: NOW,
        )

    def add(
        self, task: PastTask, status="Done", similarity=0.9, owner=USER
    ) -> PastTask:
        content = f"title: {task.title} | text: {task.domain_name}. Done."
        self.repo.add_memory(FakeMemory(owner, task, content, status, similarity))
        return task

    def find(self, task_id=None, user_id=USER, limit=5):
        return asyncio.run(
            self.use_case.execute(user_id, task_id or self.task.task_id, limit)
        )

    def usage(self):
        return [entry for _, _, entry in self.quota.logs]


# --- T10: normal hybrid search ---


def test_t10_hybrid_search():
    s = Setup(GOOD)
    both = s.add(past("Landing page khách B"))
    vector_only = s.add(past("Trang giới thiệu sản phẩm"))
    text_only = s.add(past("Landing page nháp"), status="Pending")

    result = s.find()

    assert result.search_mode is SearchMode.HYBRID
    assert result.time_zone == SAIGON
    assert s.client.texts == [QUERY]  # one call, the masked query
    assert s.repo.vector_calls == [(USER, s.task.task_id, GOOD, 20)]
    assert s.repo.text_calls == [(USER, s.task.task_id, TERMS, 20)]
    assert result.items[0].task == both
    assert {r.task.task_id for r in result.items} == {
        both.task_id,
        vector_only.task_id,
        text_only.task_id,
    }
    first = result.items[0]
    assert first.matched_by == (MatchSource.VECTOR, MatchSource.TEXT)
    assert result.summary.count == 3


def test_search_runs_read_only_and_logs_in_a_write_transaction():
    s = Setup(GOOD)
    s.find()
    assert s.repo.transactions == ["read_only", "write", "read_only"]


# --- T11: embedding fails or is too slow ---


def test_t11_embedding_error_gives_text_only_without_retry():
    s = Setup(EmbeddingError("down"), GOOD)
    s.add(past("Landing page khách B"))

    result = s.find()

    assert result.search_mode is SearchMode.TEXT_ONLY
    assert len(s.client.texts) == 1  # no retry (plan #6)
    assert s.repo.vector_calls == []
    assert len(result.items) == 1
    [entry] = s.usage()
    assert (entry.succeeded, entry.input_tokens, entry.cost) == (False, 0, 0)


def test_t11_slow_embedding_times_out_into_text_only():
    s = Setup(client=SlowClient(seconds=1.0), timeout=0.05)
    s.add(past("Landing page khách B"))

    result = s.find()

    assert result.search_mode is SearchMode.TEXT_ONLY
    assert len(result.items) == 1
    [entry] = s.usage()
    assert entry.succeeded is False
    assert entry.model == "slow-embedding"


def test_wrong_dimensions_count_as_a_failure():
    s = Setup([0.1, 0.2])
    result = s.find()
    assert result.search_mode is SearchMode.TEXT_ONLY
    assert s.repo.vector_calls == []
    [entry] = s.usage()
    assert entry.succeeded is False
    assert entry.input_tokens == 12  # Gemini answered, so the call cost money


# --- T12: embedding not configured ---


def test_t12_no_client_gives_text_only_and_no_log():
    s = Setup(client=None)
    s.add(past("Landing page khách B"))

    result = s.find()

    assert result.search_mode is SearchMode.TEXT_ONLY
    assert len(result.items) == 1
    assert s.quota.logs == []


# --- T13: Fixed task ---


def test_t13_fixed_task_searches_nothing():
    s = Setup(GOOD)
    fixed = asked(TaskType.FIXED)
    s.repo.add_task(USER, fixed)
    s.add(past("Landing page khách B"))

    result = s.find(fixed.task_id)

    assert result.search_mode is SearchMode.NONE
    assert result.items == []
    assert (result.summary.count, result.summary.average_ratio) == (0, None)
    assert s.client.texts == []
    assert s.repo.vector_calls == s.repo.text_calls == []


# --- T14: task not found ---


def test_t14_unknown_task_is_not_found():
    s = Setup(GOOD)
    with pytest.raises(TaskNotFoundError):
        s.find(uuid4())
    assert s.client.texts == []


def test_t14_other_users_task_is_not_found():
    s = Setup(GOOD)
    with pytest.raises(TaskNotFoundError):
        s.find(user_id=OTHER_USER)


# --- T15: threshold ---


def test_t15_weak_vector_matches_are_dropped_before_rrf():
    s = Setup(GOOD)
    strong = s.add(past("Trang giới thiệu"), similarity=0.8)
    s.add(past("Nấu cơm tối", domain="Cooking"), similarity=0.49)
    s.add(past("Đúng ngưỡng"), similarity=0.5)

    result = s.find()

    titles = [r.task.title for r in result.items]
    assert "Nấu cơm tối" not in titles
    assert titles == [strong.title, "Đúng ngưỡng"]  # 0.5 is kept


def test_nothing_found_gives_empty_list():
    s = Setup(GOOD)
    s.add(past("Nấu cơm tối", domain="Cooking"), similarity=0.1)
    result = s.find()
    assert result.search_mode is SearchMode.HYBRID
    assert result.items == []
    assert result.summary.average_ratio is None


# --- T16: own task and other users ---


def test_t16_never_the_asked_task_or_other_users():
    s = Setup(GOOD)
    s.add(past("Landing page của người khác"), owner=OTHER_USER)
    itself = PastTask(
        s.task.task_id,
        "Landing page cho [client]",
        "Coding",
        SimilarOutcome.DONE,
        60,
        90,
        NOW,
    )
    s.add(itself)
    mine = s.add(past("Landing page khách B"))

    result = s.find()

    assert [r.task for r in result.items] == [mine]


# --- T17: usage log on success ---


def test_t17_success_logs_one_free_embedding_call():
    s = Setup(GOOD)
    s.find()
    [entry] = s.usage()
    assert entry.feature is AIFeature.EMBEDDING
    assert entry.subscription_id == SUBSCRIPTION.subscription_id
    assert (entry.input_tokens, entry.output_tokens) == (12, 0)
    assert entry.cost == Decimal(12) * Decimal("0.15") / Decimal(1_000_000)
    assert entry.succeeded is True
    assert entry.quota_charged is False
    assert s.quota.calls_used[SUBSCRIPTION.subscription_id] == 7  # quota untouched


# --- T18: no Active plan ---


def test_t18_no_plan_still_searches_but_logs_nothing():
    s = Setup(GOOD, with_plan=False)
    s.add(past("Landing page khách B"))

    result = s.find()

    assert result.search_mode is SearchMode.HYBRID
    assert len(result.items) == 1
    assert s.quota.logs == []


# --- limit and summary ---


def test_limit_and_summary():
    s = Setup(GOOD)
    for i in range(4):
        s.add(past(f"Landing page {i}", actual=60 + 30 * i))
    s.add(past("Landing page bỏ", outcome=SimilarOutcome.SKIPPED, actual=0))

    result = s.find(limit=3)

    assert len(result.items) == 3
    assert result.summary.count == 3
