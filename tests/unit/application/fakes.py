"""In-memory stand-ins for the database and the LLM, used by application tests."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from metanoia_ai.application.ports.learning_repository import (
    DomainProfile,
    KBiasLogEntry,
    TaskAlreadyLearnedError,
    TaskForLearning,
)
from metanoia_ai.application.ports.llm_client import LLMError, LLMResponse
from metanoia_ai.application.ports.quota_repository import (
    Subscription,
    UsageLogEntry,
)
from metanoia_ai.application.ports.schedule_repository import (
    NewProposal,
    PlanningData,
)


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
