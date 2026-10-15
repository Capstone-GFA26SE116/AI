"""In-memory stand-ins for the database and the LLM, used by application tests."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from metanoia_ai.application.ports.embedding_client import (
    EmbeddingError,
    EmbeddingResponse,
)
from metanoia_ai.application.ports.learning_repository import (
    DomainProfile,
    KBiasLogEntry,
    TaskAlreadyLearnedError,
    TaskForLearning,
)
from metanoia_ai.application.ports.llm_client import LLMError, LLMResponse
from metanoia_ai.application.ports.memory_repository import (
    PendingMemory,
    TaskForMemory,
)
from metanoia_ai.application.ports.quota_repository import (
    Subscription,
    UsageLogEntry,
)
from metanoia_ai.application.ports.schedule_repository import (
    NewProposal,
    PlanningData,
)
from metanoia_ai.application.ports.similar_task_repository import (
    QueryTask,
    VectorMatch,
)
from metanoia_ai.domain.memory_text import MemoryDraft
from metanoia_ai.domain.similar_tasks import WORD, PastTask


class FakeLearningRepository:
    """Behaves like the real repository, but keeps everything in dicts."""

    def __init__(self) -> None:
        # task id -> (owner id, task)
        self.tasks: dict[UUID, tuple[UUID, TaskForLearning]] = {}
        # (user id, domain id) -> profile
        self.profiles: dict[tuple[UUID, UUID], DomainProfile] = {}
        # task id -> log entry
        self.logs: dict[UUID, KBiasLogEntry] = {}
        # task id -> owner id of its log (the real DB finds it via the profile)
        self.log_owners: dict[UUID, UUID] = {}
        self.locked: list[tuple[UUID, UUID]] = []

    def add_task(self, owner_id: UUID, task: TaskForLearning) -> None:
        self.tasks[task.task_id] = (owner_id, task)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        # Roll back on error, like a real transaction.
        saved = (dict(self.profiles), dict(self.logs), dict(self.log_owners))
        try:
            yield
        except BaseException:
            self.profiles, self.logs, self.log_owners = saved
            raise

    async def get_task(self, user_id: UUID, task_id: UUID) -> TaskForLearning | None:
        owner_and_task = self.tasks.get(task_id)
        if owner_and_task is None or owner_and_task[0] != user_id:
            return None
        return owner_and_task[1]

    async def get_log(self, user_id: UUID, task_id: UUID) -> KBiasLogEntry | None:
        if self.log_owners.get(task_id) != user_id:
            return None
        return self.logs.get(task_id)

    async def lock_profile(
        self, user_id: UUID, domain_id: UUID, initial_k: float
    ) -> DomainProfile:
        self.locked.append((user_id, domain_id))
        return self.profiles.setdefault(
            (user_id, domain_id), DomainProfile(k_bias=initial_k, sample_count=0)
        )

    async def save(self, user_id: UUID, domain_id: UUID, entry: KBiasLogEntry) -> None:
        if entry.task_id in self.logs:
            raise TaskAlreadyLearnedError(str(entry.task_id))
        self.profiles[(user_id, domain_id)] = DomainProfile(
            k_bias=entry.new_k, sample_count=entry.sample_index
        )
        self.logs[entry.task_id] = entry
        self.log_owners[entry.task_id] = user_id


# --- Schedule proposal (FR-10) ---


class FakeQuotaRepository:
    """Plans, the AiCallsUsed counter and AIUsageLog rows, in memory."""

    def __init__(self, now: datetime) -> None:
        self.now = now  # time stamped on new log rows
        # user id -> [(StartAt, subscription)]; several = the Backend bug case
        self.subscriptions: dict[UUID, list[tuple[datetime, Subscription]]] = {}
        self.calls_used: dict[UUID, int] = {}  # subscription id -> AiCallsUsed
        self.logs: list[tuple[UUID, datetime, UsageLogEntry]] = []  # (user, time, row)
        self.lose_next_charge = False  # simulate another request taking the last call

    def add_subscription(
        self, user_id: UUID, subscription: Subscription, start_at: datetime
    ) -> None:
        self.subscriptions.setdefault(user_id, []).append((start_at, subscription))
        self.calls_used[subscription.subscription_id] = subscription.ai_calls_used

    def owner_of(self, subscription_id: UUID) -> UUID:
        for user_id, rows in self.subscriptions.items():
            if any(s.subscription_id == subscription_id for _, s in rows):
                return user_id
        raise KeyError(subscription_id)

    async def get_subscription(
        self, user_id: UUID, now: datetime
    ) -> Subscription | None:
        rows = [
            (start, s)
            for start, s in self.subscriptions.get(user_id, [])
            if start <= now
        ]
        if not rows:
            return None
        _, newest = max(rows, key=lambda row: row[0])
        return replace(newest, ai_calls_used=self.calls_used[newest.subscription_id])

    async def count_charged_calls(self, user_id: UUID, since: datetime) -> int:
        return sum(
            1
            for owner, at, entry in self.logs
            if owner == user_id and at >= since and entry.quota_charged
        )

    async def try_charge(self, subscription: Subscription) -> bool:
        if self.lose_next_charge:
            self.lose_next_charge = False
            return False
        used = self.calls_used[subscription.subscription_id]
        limit = subscription.ai_call_limit
        if limit is not None and used >= limit:
            return False
        self.calls_used[subscription.subscription_id] = used + 1
        return True

    async def log_usage(self, entry: UsageLogEntry) -> None:
        self.logs.append((self.owner_of(entry.subscription_id), self.now, entry))


class FakeScheduleRepository:
    """Planning data and proposals in memory. Its transaction also covers the quota fake."""

    def __init__(self, quota: FakeQuotaRepository) -> None:
        self.quota = quota
        self.planning: dict[UUID, PlanningData] = {}
        # proposal id -> (owner id, status, proposal)
        self.proposals: dict[UUID, tuple[UUID, str, NewProposal]] = {}
        self.slot_status: dict[UUID, str] = {}  # slot id -> Proposed / Cancelled
        self.fail_on_save = False  # simulate a DB error while saving
        self.planning_calls: list[tuple[UUID, datetime, datetime]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        # Roll back everything on error, like a real transaction.
        saved = (
            dict(self.proposals),
            dict(self.slot_status),
            dict(self.quota.calls_used),
            list(self.quota.logs),
        )
        try:
            yield
        except BaseException:
            (
                self.proposals,
                self.slot_status,
                self.quota.calls_used,
                self.quota.logs,
            ) = saved
            raise

    async def get_planning_data(
        self, user_id: UUID, window_start: datetime, window_end: datetime
    ) -> PlanningData:
        self.planning_calls.append((user_id, window_start, window_end))
        return self.planning[user_id]

    async def save_proposal(self, user_id: UUID, proposal: NewProposal) -> None:
        for proposal_id, (owner, status, old) in list(self.proposals.items()):
            if owner == user_id and status == "Pending":
                self.proposals[proposal_id] = (owner, "Superseded", old)
                for slot in old.slots:
                    if self.slot_status[slot.slot_id] == "Proposed":
                        self.slot_status[slot.slot_id] = "Cancelled"
        if self.fail_on_save:
            raise RuntimeError("database error while saving")
        self.proposals[proposal.proposal_id] = (user_id, "Pending", proposal)
        for slot in proposal.slots:
            self.slot_status[slot.slot_id] = "Proposed"

    def pending(self, user_id: UUID) -> list[NewProposal]:
        return [
            p
            for owner, status, p in self.proposals.values()
            if owner == user_id and status == "Pending"
        ]


class FakeLLMClient:
    """Returns prepared answers in order.

    An answer can be text, an exception (raised instead), or a function that
    gets the prompt and returns text (for slot ids made at run time).
    """

    def __init__(
        self,
        *answers: str | Exception | Callable[[str], str],
        model: str = "fake-model",
    ) -> None:
        self.answers = list(answers)
        self.model = model
        self.prompts: list[str] = []
        self.schemas: list[dict[str, Any]] = []

    async def generate_json(
        self, prompt: str, response_schema: dict[str, Any]
    ) -> LLMResponse:
        self.prompts.append(prompt)
        self.schemas.append(response_schema)
        if not self.answers:
            raise LLMError("no more fake answers", model=self.model)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(prompt)
        return LLMResponse(
            text=answer, model=self.model, input_tokens=100, output_tokens=50
        )


# --- Long-term memory (FR-19) ---


@dataclass(frozen=True)
class FakeMemoryRow:
    """One TaskExecutionMemory row."""

    user_id: UUID
    task_id: UUID
    draft: MemoryDraft
    status: str  # Pending / Done / Failed
    vector: tuple[float, ...] | None
    model: str | None
    order: int  # stands in for CreatedAt (oldest first)


class FakeMemoryRepository:
    """Tasks and memory rows in memory. One row per task, like UNIQUE("TaskId")."""

    def __init__(self) -> None:
        # task id -> (owner id, task)
        self.tasks: dict[UUID, tuple[UUID, TaskForMemory]] = {}
        # memory id -> row
        self.rows: dict[UUID, FakeMemoryRow] = {}
        self.fail_on_upsert = False  # simulate a DB error while saving
        self._next_order = 0

    def add_task(self, owner_id: UUID, task: TaskForMemory) -> None:
        self.tasks[task.task_id] = (owner_id, task)

    def row_for_task(self, task_id: UUID) -> tuple[UUID, FakeMemoryRow] | None:
        for memory_id, row in self.rows.items():
            if row.task_id == task_id:
                return memory_id, row
        return None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        # Roll back on error, like a real transaction.
        saved = dict(self.rows)
        try:
            yield
        except BaseException:
            self.rows = saved
            raise

    async def get_task(self, user_id: UUID, task_id: UUID) -> TaskForMemory | None:
        owner_and_task = self.tasks.get(task_id)
        if owner_and_task is None or owner_and_task[0] != user_id:
            return None
        return owner_and_task[1]

    async def upsert(
        self, user_id: UUID, task_id: UUID, draft: MemoryDraft, model: str | None
    ) -> UUID:
        if self.fail_on_upsert:
            raise RuntimeError("database error while saving the memory")
        existing = self.row_for_task(task_id)
        if existing:
            memory_id, row = existing
            order = row.order
        else:
            memory_id, order = uuid4(), self._next_order
            self._next_order += 1
        self.rows[memory_id] = FakeMemoryRow(
            user_id, task_id, draft, "Pending", None, model, order
        )
        return memory_id

    def _own_row(self, user_id: UUID, memory_id: UUID) -> FakeMemoryRow | None:
        row = self.rows.get(memory_id)
        return row if row is not None and row.user_id == user_id else None

    async def get_pending(self, user_id: UUID, memory_id: UUID) -> PendingMemory | None:
        row = self._own_row(user_id, memory_id)
        if row is None or row.status == "Done":
            return None
        return PendingMemory(memory_id, user_id, row.draft.content)

    async def list_waiting(self, limit: int) -> list[tuple[UUID, UUID]]:
        waiting = sorted(
            (row.order, memory_id, row.user_id)
            for memory_id, row in self.rows.items()
            if row.status != "Done"
        )
        return [(memory_id, user_id) for _, memory_id, user_id in waiting[:limit]]

    async def save_embedding(
        self,
        user_id: UUID,
        memory_id: UUID,
        content: str,
        vector: list[float],
        model: str,
    ) -> bool:
        row = self._own_row(user_id, memory_id)
        if row is None or row.draft.content != content:
            return False
        self.rows[memory_id] = replace(
            row, status="Done", vector=tuple(vector), model=model
        )
        return True

    async def mark_failed(self, user_id: UUID, memory_id: UUID, content: str) -> bool:
        row = self._own_row(user_id, memory_id)
        if row is None or row.draft.content != content:
            return False
        self.rows[memory_id] = replace(row, status="Failed")
        return True


class FakeEmbeddingClient:
    """Returns prepared answers in order.

    An answer can be a vector, an exception (raised instead), or a function
    that gets the text and returns a vector (e.g. to change data mid-call).
    """

    def __init__(
        self,
        *answers: list[float] | Exception | Callable[[str], list[float]],
        model: str = "fake-embedding",
        input_tokens: int | None = 12,
    ) -> None:
        self.answers = list(answers)
        self.model = model
        self.input_tokens = input_tokens
        self.texts: list[str] = []

    async def embed(self, text: str) -> EmbeddingResponse:
        self.texts.append(text)
        if not self.answers:
            raise EmbeddingError("no more fake answers", model=self.model)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(text)
        return EmbeddingResponse(
            vector=answer, model=self.model, input_tokens=self.input_tokens
        )


@dataclass(frozen=True)
class FakeMemory:
    """A stored memory. `similarity` stands in for the vector distance:
    the fake vector search returns it whatever the query vector is."""

    owner_id: UUID
    task: PastTask
    content: str
    status: str = "Done"  # Pending / Done / Failed
    similarity: float = 0.9


class FakeSimilarTaskRepository:
    """Tasks and memories in lists. Records each search call."""

    def __init__(self) -> None:
        # task id -> (owner id, task)
        self.tasks: dict[UUID, tuple[UUID, QueryTask]] = {}
        self.memories: list[FakeMemory] = []
        self.vector_calls: list[tuple[UUID, UUID, list[float], int]] = []
        self.text_calls: list[tuple[UUID, UUID, str, int]] = []
        self.transactions: list[str] = []  # "read_only" / "write", in order

    def add_task(self, owner_id: UUID, task: QueryTask) -> None:
        self.tasks[task.task_id] = (owner_id, task)

    def add_memory(self, memory: FakeMemory) -> None:
        self.memories.append(memory)

    @asynccontextmanager
    async def read_only(self) -> AsyncIterator[None]:
        self.transactions.append("read_only")
        yield

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.transactions.append("write")
        yield

    async def get_task(self, user_id: UUID, task_id: UUID) -> QueryTask | None:
        owner_and_task = self.tasks.get(task_id)
        if owner_and_task is None or owner_and_task[0] != user_id:
            return None
        return owner_and_task[1]

    def _candidates(self, user_id: UUID, exclude_task_id: UUID) -> list[FakeMemory]:
        return [
            m
            for m in self.memories
            if m.owner_id == user_id and m.task.task_id != exclude_task_id
        ]

    async def vector_search(
        self, user_id: UUID, exclude_task_id: UUID, vector: list[float], limit: int
    ) -> list[VectorMatch]:
        self.vector_calls.append((user_id, exclude_task_id, vector, limit))
        done = [
            m for m in self._candidates(user_id, exclude_task_id) if m.status == "Done"
        ]
        done.sort(key=lambda m: m.similarity, reverse=True)
        return [VectorMatch(m.task, m.similarity) for m in done[:limit]]

    async def text_search(
        self, user_id: UUID, exclude_task_id: UUID, terms: str, limit: int
    ) -> list[PastTask]:
        """Any status. More matching terms first (like ts_rank_cd, roughly)."""
        self.text_calls.append((user_id, exclude_task_id, terms, limit))
        wanted = set(terms.split(" | "))
        scored = []
        for m in self._candidates(user_id, exclude_task_id):
            hits = len(wanted & set(WORD.findall(m.content.lower())))
            if hits:
                scored.append((-hits, len(scored), m.task))
        return [task for _, _, task in sorted(scored)[:limit]]
