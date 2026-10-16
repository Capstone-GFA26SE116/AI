"""FR-08a: the user sends a message in the chat of a big task; the AI asks back
or answers with a draft list of subtasks.

1 message = 1 AI call from the quota, charged only for a valid answer (plan #11).
The LLM call runs outside any transaction; everything is saved in one at the end.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.chat_writer import (
    BreakdownTask,
    ChatLine,
    ChatReply,
    ChatWriter,
    SimilarLine,
)
from metanoia_ai.application.llm_chat import ChatQuota
from metanoia_ai.application.ports.chat_repository import (
    ChatMessage,
    ChatRepository,
    ChatRole,
    ChatTask,
    SavedDraft,
)
from metanoia_ai.application.ports.quota_repository import (
    QuotaRepository,
    Subscription,
)
from metanoia_ai.application.reason_writer import LLMCall
from metanoia_ai.application.use_cases.find_similar_tasks import SimilarTasksResult
from metanoia_ai.application.use_cases.propose_schedule import QuotaExceededError
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.domain.memory_text import mask_contact
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.similar_tasks import SimilarSummary
from metanoia_ai.domain.task_breakdown import draft_numbers

logger = logging.getLogger(__name__)

FINISHED = {"Done", "Skipped"}
DEADLINE_FORMAT = "%Y-%m-%d %H:%M"


class TaskNotSplittableError(Exception):
    """The task cannot get subtasks from the chat (plan #12). → 409."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason  # fixed / finished / has_subtasks


class InvalidMessageError(ValueError):
    """Empty message, or longer than CHAT_MESSAGE_MAX_CHARS. → 422."""


class AIUnavailableError(Exception):
    """Every LLM try failed. Nothing saved, nothing charged (plan #10). → 502."""


class _LostLastCallError(Exception):
    """Another request used the last call while this one was running."""


class SimilarFinder(Protocol):
    """FindSimilarTasks (FR-09), or a stand-in in tests."""

    async def execute(
        self, user_id: UUID, task_id: UUID, limit: int
    ) -> SimilarTasksResult: ...


@dataclass(frozen=True)
class ChatTurn:
    task_id: UUID
    user_message: ChatMessage
    ai_message: ChatMessage  # its `draft` holds the numbers and similar summary
    time_zone: ZoneInfo  # the user's, for created_at in the answer


def splittable_problem(task: ChatTask) -> str | None:
    """Why this task cannot be split now, or None (plan #12, #8b)."""
    if task.task_type is TaskType.FIXED:
        return "fixed"
    if task.status in FINISHED:
        return "finished"
    if task.subtask_count > 0:
        return "has_subtasks"
    return None


def extra_percent(k_bias: Decimal) -> int:
    """K 1.3 → 30: "you usually need ~30% more". The LLM never sees K itself."""
    return round((k_bias - 1) * 100)


class SendTaskChatMessage:
    def __init__(
        self,
        repo: ChatRepository,
        quota_repo: QuotaRepository,
        writer: ChatWriter,
        find_similar: SimilarFinder,
        *,
        initial_k: Decimal,
        history_limit: int,
        max_message_chars: int,
        similar_limit: int,
        warning_ratio: float,
        quota_daily_max: int,
        now: Callable[[], datetime],
    ) -> None:
        self.repo = repo
        self.quota = quota_repo  # shares the repo's session
        self.writer = writer
        self.find_similar = find_similar
        self.initial_k = initial_k
        self.history_limit = history_limit
        self.max_message_chars = max_message_chars
        self.similar_limit = similar_limit
        self.warning_ratio = warning_ratio
        self.quota_daily_max = quota_daily_max
        self.now = now
        self.chat_quota = ChatQuota(quota_repo, daily_max=quota_daily_max, now=now)

    async def execute(self, user_id: UUID, task_id: UUID, message: str) -> ChatTurn:
        text = message.strip()
        if not text or len(text) > self.max_message_chars:
            raise InvalidMessageError(
                f"message must be 1-{self.max_message_chars} characters"
            )

        # 1-4. Read everything in one short transaction. No LLM yet.
        async with self.repo.transaction():
            task = await self.repo.get_task(user_id, task_id)
            if task is None:
                raise TaskNotFoundError(str(task_id))
            problem = splittable_problem(task)
            if problem is not None:
                raise TaskNotSplittableError(problem)
            subscription = await self._check_quota(user_id)
            conversation_id = await self.repo.find_conversation(user_id, task_id)
            history = (
                await self.repo.list_messages(
                    user_id, conversation_id, self.history_limit
                )
                if conversation_id
                else []
            )
            k_bias = await self.repo.get_k_bias(user_id, task.domain_id)
        k_bias = k_bias if k_bias is not None else self.initial_k
        similar = await self._similar(user_id, task_id)

        # 5. One LLM call (plus retries), outside any transaction.
        result = await self.writer.write(
            self._prompt_task(task, k_bias, similar),
            [self._line(m, task.client_name) for m in history],
            mask_contact(text, task.client_name),
        )
        if result.reply is None:
            async with self.repo.transaction():
                await self._log(subscription, result.calls, charged=False)
            raise AIUnavailableError(str(task_id))

        # 6. Save both messages, charge, log: all or nothing.
        try:
            return await self._save(
                user_id,
                task_id,
                text,
                result.reply,
                result.calls,
                subscription,
                k_bias,
                similar,
            )
        except (_LostLastCallError, TaskNotSplittableError) as error:
            async with self.repo.transaction():
                await self._log(subscription, result.calls, charged=False)
            if isinstance(error, TaskNotSplittableError):
                raise
            raise QuotaExceededError("plan limit reached") from None

    async def _check_quota(self, user_id: UUID) -> Subscription:
        return await self.chat_quota.check(user_id)

    async def _similar(self, user_id: UUID, task_id: UUID) -> SimilarTasksResult | None:
        """FR-09 results; any error → None, the chat goes on (plan #23)."""
        try:
            return await self.find_similar.execute(user_id, task_id, self.similar_limit)
        except Exception:
            logger.exception("Similar task search failed for task %s", task_id)
            return None

    @staticmethod
    def _summary(similar: SimilarTasksResult | None) -> SimilarSummary | None:
        if similar is None or not similar.items:
            return None
        return similar.summary

    def _prompt_task(
        self, task: ChatTask, k_bias: Decimal, similar: SimilarTasksResult | None
    ) -> BreakdownTask:
        """What the LLM may see (plan #5): masked texts, no K, no contact details."""
        client = task.client_name
        deadline = (
            task.deadline.astimezone(task.time_zone).strftime(DEADLINE_FORMAT)
            if task.deadline
            else None
        )
        return BreakdownTask(
            title=mask_contact(task.title, client),
            description=mask_contact(task.description, client)
            if task.description
            else None,
            domain=task.domain_name,
            estimate_minutes=task.estimated_minutes or 0,
            deadline=deadline,
            usually_extra_percent=extra_percent(k_bias),
            similar_tasks=tuple(
                SimilarLine(
                    title=mask_contact(item.task.title, client),
                    outcome=item.task.outcome.value,
                    estimated_minutes=item.task.estimated_minutes,
                    actual_minutes=item.task.actual_minutes,
                )
                for item in (similar.items if similar else [])
            ),
        )

    @staticmethod
    def _line(message: ChatMessage, client_name: str | None) -> ChatLine:
        if message.role is ChatRole.USER:
            return ChatLine("user", mask_contact(message.content, client_name))
        subtasks = (
            tuple((s.title, s.estimated_minutes) for s in message.draft.draft.subtasks)
            if message.draft
            else ()
        )
        return ChatLine("assistant", message.content, subtasks)

    async def _save(
        self,
        user_id: UUID,
        task_id: UUID,
        text: str,
        reply: ChatReply,
        calls: Sequence[LLMCall],
        subscription: Subscription,
        k_bias: Decimal,
        similar: SimilarTasksResult | None,
    ) -> ChatTurn:
        async with self.repo.transaction():
            # Lock the task: two first messages at once still make one chat (#28).
            task = await self.repo.get_task(user_id, task_id, lock=True)
            if task is None:
                raise TaskNotFoundError(str(task_id))
            problem = splittable_problem(task)
            if problem is not None:  # e.g. applied in another tab meanwhile
                raise TaskNotSplittableError(problem)
            conversation_id = await self.repo.find_conversation(user_id, task_id)
            if conversation_id is None:
                conversation_id = await self.repo.create_conversation(user_id, task)

            saved_draft = None
            if reply.draft is not None:
                saved_draft = SavedDraft(
                    draft=reply.draft,
                    numbers=draft_numbers(
                        task.estimated_minutes or 0,
                        k_bias,
                        reply.draft,
                        subscription.is_free,
                        self.warning_ratio,
                        reply.language,
                    ),
                    similar=self._summary(similar),
                )
            user_message = await self.repo.add_message(
                user_id,
                conversation_id,
                ChatRole.USER,
                text,  # as written (#16)
            )
            ai_message = await self.repo.add_message(
                user_id, conversation_id, ChatRole.ASSISTANT, reply.text, saved_draft
            )
            if not await self.quota.try_charge(subscription):
                raise _LostLastCallError
            await self._log(subscription, calls, charged=True)
        return ChatTurn(task_id, user_message, ai_message, task.time_zone)

    async def _log(
        self, subscription: Subscription, calls: Sequence[LLMCall], charged: bool
    ) -> None:
        """One AIUsageLog row per LLM call; only the good call is charged."""
        await self.chat_quota.log(subscription, calls, charged)
