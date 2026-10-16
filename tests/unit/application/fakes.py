"""In-memory stand-ins for the database and the LLM, used by application tests."""

import copy
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.chat_repository import (
    ChatMessage,
    ChatRole,
    ChatTask,
    SavedDraft,
    SavedSubtask,
)
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
from metanoia_ai.application.ports.productive_hours_repository import (
    ProductiveProfile,
)
from metanoia_ai.application.ports.project_chat_repository import (
    ChatProject,
    ConversationStatus,
    ExistingWork,
    NewMilestone,
    NewTask,
    ProjectChatMessage,
    ProjectConversation,
    SavedProjectDraft,
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
from metanoia_ai.domain.productive_hours import (
    SCORE_STEP,
    FocusSample,
    HourScore,
    OnboardingBlock,
)
from metanoia_ai.domain.similar_tasks import WORD, PastTask
from metanoia_ai.domain.task_breakdown import Subtask


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


# --- Task chat (FR-08a) ---


@dataclass
class FakeConversation:
    owner_id: UUID
    task_id: UUID
    project_id: UUID
    title: str
    status: str = "Open"
    messages: list[ChatMessage] = field(default_factory=list)


class FakeChatRepository:
    """Tasks, chats, messages and subtasks in memory. Rolls back like a transaction."""

    def __init__(self, now: datetime) -> None:
        self.now = now  # first message time; each message is 1 s later
        # task id -> (owner id, task)
        self.tasks: dict[UUID, tuple[UUID, ChatTask]] = {}
        self.k_bias: dict[tuple[UUID, UUID], Decimal] = {}  # (user, domain) -> K
        self.conversations: dict[UUID, FakeConversation] = {}
        self.subtasks: dict[UUID, list[SavedSubtask]] = {}  # task id -> subtasks
        self.locked: list[UUID] = []  # task ids locked with get_task(lock=True)
        self.fail_on_add_message = False  # simulate a DB error while saving

    def add_task(self, owner_id: UUID, task: ChatTask) -> None:
        self.tasks[task.task_id] = (owner_id, task)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        saved = (
            copy.deepcopy(self.conversations),
            copy.deepcopy(self.subtasks),
            dict(self.tasks),
        )
        try:
            yield
        except BaseException:
            self.conversations, self.subtasks, self.tasks = saved
            raise

    async def get_task(
        self, user_id: UUID, task_id: UUID, lock: bool = False
    ) -> ChatTask | None:
        owner_and_task = self.tasks.get(task_id)
        if owner_and_task is None or owner_and_task[0] != user_id:
            return None
        if lock:
            self.locked.append(task_id)
        task = owner_and_task[1]
        return replace(task, subtask_count=len(self.subtasks.get(task_id, [])))

    async def get_k_bias(self, user_id: UUID, domain_id: UUID) -> Decimal | None:
        return self.k_bias.get((user_id, domain_id))

    async def find_conversation(self, user_id: UUID, task_id: UUID) -> UUID | None:
        for conversation_id, c in self.conversations.items():
            if c.owner_id == user_id and c.task_id == task_id:
                return conversation_id
        return None

    async def create_conversation(self, user_id: UUID, task: ChatTask) -> UUID:
        conversation_id = uuid4()
        self.conversations[conversation_id] = FakeConversation(
            user_id, task.task_id, task.project_id, task.title[:200]
        )
        return conversation_id

    def _own(self, user_id: UUID, conversation_id: UUID) -> FakeConversation | None:
        c = self.conversations.get(conversation_id)
        return c if c is not None and c.owner_id == user_id else None

    async def list_messages(
        self, user_id: UUID, conversation_id: UUID, limit: int | None = None
    ) -> list[ChatMessage]:
        c = self._own(user_id, conversation_id)
        if c is None:
            return []
        return list(c.messages if limit is None else c.messages[-limit:])

    async def latest_draft(
        self, user_id: UUID, conversation_id: UUID
    ) -> ChatMessage | None:
        c = self._own(user_id, conversation_id)
        drafts = [m for m in (c.messages if c else []) if m.draft is not None]
        return drafts[-1] if drafts else None

    async def add_message(
        self,
        user_id: UUID,
        conversation_id: UUID,
        role: ChatRole,
        content: str,
        draft: SavedDraft | None = None,
    ) -> ChatMessage:
        if self.fail_on_add_message:
            raise RuntimeError("database error while saving a message")
        c = self._own(user_id, conversation_id)
        assert c is not None, "message for a chat of another user"
        if draft is not None and role is not ChatRole.ASSISTANT:
            raise ValueError("only AI messages have a draft (DB CHECK)")
        count = sum(len(x.messages) for x in self.conversations.values())
        message = ChatMessage(
            uuid4(), role, content, self.now + timedelta(seconds=count), draft
        )
        c.messages.append(message)
        return message

    async def add_subtasks(
        self, user_id: UUID, task_id: UUID, subtasks: Sequence[Subtask]
    ) -> list[SavedSubtask]:
        assert self.tasks[task_id][0] == user_id
        saved = [
            SavedSubtask(uuid4(), s.title, s.estimated_minutes, s.order_index)
            for s in subtasks
        ]
        self.subtasks.setdefault(task_id, []).extend(saved)
        return saved

    async def mark_applied(
        self, user_id: UUID, conversation_id: UUID, message_id: UUID
    ) -> None:
        c = self._own(user_id, conversation_id)
        assert c is not None
        if any(m.is_applied for m in c.messages):
            raise ValueError("one applied draft per chat (DB unique index)")
        c.messages = [
            replace(m, is_applied=True) if m.message_id == message_id else m
            for m in c.messages
        ]
        c.status = "Applied"


# --- Project chat (FR-08b-1) ---


@dataclass
class FakeProjectChat:
    """One ChatConversation row (Scope Project) with its messages."""

    owner_id: UUID
    conversation: ProjectConversation
    messages: list[ProjectChatMessage] = field(default_factory=list)


@dataclass(frozen=True)
class FakeMilestoneRow:
    milestone_id: UUID
    project_id: UUID
    milestone: NewMilestone


@dataclass(frozen=True)
class FakeTaskRow:
    task_id: UUID
    project_id: UUID
    task: NewTask


class FakeProjectChatRepository:
    """Projects, project chats, milestones and tasks in memory.

    Rolls back like a transaction. Task chats (FR-08a) live in FakeChatRepository:
    `task_chat_ids` stands for them, so they are never found here.
    """

    def __init__(self, now: datetime) -> None:
        self.now = now  # first row time; each new row is 1 s later
        self.projects: dict[UUID, tuple[UUID, ChatProject]] = {}  # id -> (owner, p)
        self.domains: dict[UUID, tuple[str, bool]] = {}  # id -> (name, is_active)
        self.k_bias: dict[tuple[UUID, UUID], Decimal] = {}  # (user, domain) -> K
        self.chats: dict[UUID, FakeProjectChat] = {}
        self.task_chat_ids: set[UUID] = set()  # FR-08a chats: Scope Task
        # Rows the Backend made before (titles only): project id -> titles, oldest first
        self.old_milestones: dict[UUID, list[tuple[int, str]]] = {}  # (order, name)
        self.old_tasks: dict[UUID, list[str]] = {}
        self.new_milestones: list[FakeMilestoneRow] = []
        self.new_tasks: list[FakeTaskRow] = []
        self.locked: list[UUID] = []  # project / chat ids locked
        self.clock_ticks = 0

    # --- test setup ---

    def add_project(self, owner_id: UUID, project: ChatProject) -> None:
        self.projects[project.project_id] = (owner_id, project)

    def add_domain(self, domain_id: UUID, name: str, is_active: bool = True) -> None:
        self.domains[domain_id] = (name, is_active)

    def add_old_task(self, project_id: UUID, title: str) -> None:
        self.old_tasks.setdefault(project_id, []).append(title)

    def add_old_milestone(self, project_id: UUID, order_index: int, title: str) -> None:
        self.old_milestones.setdefault(project_id, []).append((order_index, title))

    def _tick(self) -> datetime:
        self.clock_ticks += 1
        return self.now + timedelta(seconds=self.clock_ticks)

    def _owns_project(self, user_id: UUID, project_id: UUID) -> bool:
        row = self.projects.get(project_id)
        return row is not None and row[0] == user_id

    def _own_chat(self, user_id: UUID, conversation_id: UUID) -> FakeProjectChat | None:
        if conversation_id in self.task_chat_ids:
            return None
        chat = self.chats.get(conversation_id)
        return chat if chat is not None and chat.owner_id == user_id else None

    # --- the port ---

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        saved = (
            copy.deepcopy(self.chats),
            list(self.new_milestones),
            list(self.new_tasks),
        )
        try:
            yield
        except BaseException:
            self.chats, self.new_milestones, self.new_tasks = saved
            raise

    async def get_project(
        self, user_id: UUID, project_id: UUID, lock: bool = False
    ) -> ChatProject | None:
        if not self._owns_project(user_id, project_id):
            return None
        if lock:
            self.locked.append(project_id)
        return self.projects[project_id][1]

    async def get_conversation(
        self, user_id: UUID, conversation_id: UUID, lock: bool = False
    ) -> ProjectConversation | None:
        chat = self._own_chat(user_id, conversation_id)
        if chat is None:
            return None
        if lock:
            self.locked.append(conversation_id)
        return chat.conversation

    async def list_conversations(
        self, user_id: UUID, project_id: UUID
    ) -> list[ProjectConversation]:
        found = [
            c.conversation
            for c in self.chats.values()
            if c.owner_id == user_id and c.conversation.project_id == project_id
        ]
        return sorted(found, key=lambda c: c.created_at, reverse=True)

    async def create_conversation(
        self, user_id: UUID, project_id: UUID, title: str
    ) -> ProjectConversation:
        assert self._owns_project(user_id, project_id), "chat for another user"
        assert len(title) <= 200, "ChatConversation.Title is varchar(200)"
        conversation = ProjectConversation(
            uuid4(), project_id, title, ConversationStatus.OPEN, self._tick()
        )
        self.chats[conversation.conversation_id] = FakeProjectChat(
            user_id, conversation
        )
        return conversation

    async def list_messages(
        self, user_id: UUID, conversation_id: UUID
    ) -> list[ProjectChatMessage]:
        chat = self._own_chat(user_id, conversation_id)
        return list(chat.messages) if chat else []

    async def latest_draft(
        self, user_id: UUID, conversation_id: UUID
    ) -> ProjectChatMessage | None:
        chat = self._own_chat(user_id, conversation_id)
        drafts = [m for m in (chat.messages if chat else []) if m.draft is not None]
        return drafts[-1] if drafts else None

    async def add_message(
        self,
        user_id: UUID,
        conversation_id: UUID,
        role: ChatRole,
        content: str,
        draft: SavedProjectDraft | None = None,
    ) -> ProjectChatMessage:
        chat = self._own_chat(user_id, conversation_id)
        assert chat is not None, "message for a chat of another user"
        if draft is not None and role is not ChatRole.ASSISTANT:
            raise ValueError("only AI messages have a draft (DB CHECK)")
        message = ProjectChatMessage(uuid4(), role, content, self._tick(), draft)
        chat.messages.append(message)
        return message

    async def active_domains(self) -> dict[UUID, str]:
        return {d: name for d, (name, active) in self.domains.items() if active}

    async def get_k_biases(self, user_id: UUID) -> dict[UUID, Decimal]:
        return {d: k for (u, d), k in self.k_bias.items() if u == user_id}

    async def existing_work(self, user_id: UUID, project_id: UUID) -> ExistingWork:
        assert self._owns_project(user_id, project_id)
        milestones = [t for _, t in self.old_milestones.get(project_id, [])] + [
            r.milestone.title for r in self.new_milestones if r.project_id == project_id
        ]
        tasks = list(self.old_tasks.get(project_id, [])) + [
            r.task.title for r in self.new_tasks if r.project_id == project_id
        ]
        return ExistingWork(tuple(reversed(milestones)), tuple(reversed(tasks)))

    async def next_milestone_order(self, user_id: UUID, project_id: UUID) -> int:
        assert self._owns_project(user_id, project_id)
        orders = [o for o, _ in self.old_milestones.get(project_id, [])] + [
            r.milestone.order_index
            for r in self.new_milestones
            if r.project_id == project_id
        ]
        return max(orders, default=0) + 1

    async def add_milestone(
        self, user_id: UUID, project_id: UUID, milestone: NewMilestone
    ) -> UUID:
        assert self._owns_project(user_id, project_id)
        assert len(milestone.title) <= 150, "Milestone.Name is varchar(150)"
        orders = [o for o, _ in self.old_milestones.get(project_id, [])] + [
            r.milestone.order_index
            for r in self.new_milestones
            if r.project_id == project_id
        ]
        if milestone.order_index in orders:
            raise ValueError("UQ_Milestone_ProjectId_OrderIndex")
        row = FakeMilestoneRow(uuid4(), project_id, milestone)
        self.new_milestones.append(row)
        return row.milestone_id

    async def add_task(self, user_id: UUID, project_id: UUID, task: NewTask) -> UUID:
        assert self._owns_project(user_id, project_id)
        assert any(r.milestone_id == task.milestone_id for r in self.new_milestones)
        assert task.deadline.tzinfo is not None
        assert len(task.title) <= 200, "Task.Title is varchar(200)"
        assert task.domain_id in self.domains
        row = FakeTaskRow(uuid4(), project_id, task)
        self.new_tasks.append(row)
        return row.task_id

    async def mark_applied(
        self, user_id: UUID, conversation_id: UUID, message_id: UUID
    ) -> None:
        chat = self._own_chat(user_id, conversation_id)
        assert chat is not None
        if any(m.is_applied for m in chat.messages):
            raise ValueError("one applied draft per chat (DB unique index)")
        chat.messages = [
            replace(m, is_applied=True) if m.message_id == message_id else m
            for m in chat.messages
        ]
        chat.conversation = replace(
            chat.conversation, status=ConversationStatus.APPLIED
        )


# --- Productive hours (FR-18) ---


@dataclass
class FakeProductiveUser:
    """One User row (the parts FR-18 reads) and its Active plan."""

    time_zone: ZoneInfo
    plan_name: str | None = None
    blocks: tuple[OnboardingBlock, ...] | None = None


class FakeProductiveHoursRepository:
    """Users, sessions and UserCircadianProfile rows in memory. Rolls back on error.

    get_sessions returns every session of the user, even ones that cannot be
    learned from (allowed by the port), so use case tests prove the filter.
    """

    def __init__(self) -> None:
        self.users: dict[UUID, FakeProductiveUser] = {}
        self.sessions: dict[UUID, list[FocusSample]] = {}
        self.hours: dict[UUID, dict[int, HourScore]] = {}  # user -> hour -> row
        self.session_reads: list[tuple[UUID, datetime]] = []
        self.saves = 0  # number of save_hours calls
        self.fail_on_save_hours = False  # simulate a DB error while saving

    # --- test setup ---

    def add_user(
        self, user_id: UUID, time_zone: str = "Asia/Ho_Chi_Minh", **kwargs: Any
    ) -> None:
        self.users[user_id] = FakeProductiveUser(ZoneInfo(time_zone), **kwargs)

    def add_session(self, user_id: UUID, sample: FocusSample) -> None:
        self.sessions.setdefault(user_id, []).append(sample)

    # --- the port ---

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        saved = (copy.deepcopy(self.users), copy.deepcopy(self.hours))
        try:
            yield
        except BaseException:
            self.users, self.hours = saved
            raise

    async def get_profile(self, user_id: UUID) -> ProductiveProfile | None:
        user = self.users.get(user_id)
        if user is None:
            return None
        return ProductiveProfile(
            time_zone=user.time_zone,
            blocks=user.blocks,
            plan_name=user.plan_name,
            hours=dict(self.hours.get(user_id, {})),
        )

    async def get_sessions(self, user_id: UUID, since: datetime) -> list[FocusSample]:
        assert since.tzinfo is not None
        self.session_reads.append((user_id, since))
        return list(self.sessions.get(user_id, []))

    async def save_blocks(
        self, user_id: UUID, blocks: Sequence[OnboardingBlock]
    ) -> None:
        self.users[user_id].blocks = tuple(blocks)

    async def save_hours(self, user_id: UUID, hours: Sequence[HourScore]) -> None:
        if self.fail_on_save_hours:
            raise RuntimeError("database error while saving hours")
        assert user_id in self.users, "UserCircadianProfile_UserId_fkey"
        assert len({h.hour for h in hours}) == len(hours), "one row per hour"
        for h in hours:
            assert 0 <= h.hour <= 23, "UserCircadianProfile_HourOfDay_check"
            for value in (h.score, h.prior):
                assert 0 <= value <= 1, "UserCircadianProfile_Score_check"
                assert value == value.quantize(SCORE_STEP), "numeric(4,3)"
        self.saves += 1
        self.hours.setdefault(user_id, {}).update({h.hour: h for h in hours})
