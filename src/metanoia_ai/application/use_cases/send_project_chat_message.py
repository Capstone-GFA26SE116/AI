"""FR-08b-1: the user sends a message (usually a client brief) in a project chat;
the AI asks back or answers with a draft of milestones + tasks.

1 message = 1 AI call from the quota, charged only for a valid answer (#12).
The LLM call runs outside any transaction; everything is saved in one at the end.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.llm_chat import ChatQuota
from metanoia_ai.application.ports.project_chat_repository import (
    ChatProject,
    ChatRole,
    ConversationStatus,
    ExistingWork,
    ProjectChatMessage,
    ProjectChatRepository,
    ProjectConversation,
    SavedProjectDraft,
)
from metanoia_ai.application.ports.quota_repository import (
    QuotaRepository,
    Subscription,
)
from metanoia_ai.application.project_chat_writer import (
    DraftContext,
    ProjectChatLine,
    ProjectChatWriter,
    ProjectReply,
    PromptDomain,
    PromptProject,
)
from metanoia_ai.application.reason_writer import LLMCall
from metanoia_ai.application.use_cases.propose_schedule import QuotaExceededError
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
    InvalidMessageError,
    extra_percent,
)
from metanoia_ai.application.use_cases.start_project_chat import (
    ConversationAppliedError,
    ConversationNotFoundError,
    open_project,
)
from metanoia_ai.domain.memory_text import mask_contact
from metanoia_ai.domain.project_breakdown import project_numbers


class _LostLastCallError(Exception):
    """Another request used the last call while this one was running."""


@dataclass(frozen=True)
class ProjectChatTurn:
    conversation_id: UUID
    user_message: ProjectChatMessage
    ai_message: ProjectChatMessage  # its `draft` holds the numbers
    time_zone: ZoneInfo  # the user's, for created_at in the answer


def pick_history(
    messages: Sequence[ProjectChatMessage], recent: int
) -> list[ProjectChatMessage]:
    """The first message (the brief) + the `recent` newest ones, oldest first (#18)."""
    if len(messages) <= recent + 1:
        return list(messages)
    return [messages[0], *messages[-recent:]]


def check_open(conversation: ProjectConversation) -> None:
    if conversation.status is not ConversationStatus.OPEN:
        raise ConversationAppliedError(str(conversation.conversation_id))


@dataclass(frozen=True)
class _Prepared:
    """Everything read before the LLM call."""

    project: ChatProject
    subscription: Subscription
    history: list[ProjectChatMessage]
    domains: dict[UUID, str]
    k_bias: dict[UUID, Decimal]
    work: ExistingWork


class SendProjectChatMessage:
    def __init__(
        self,
        repo: ProjectChatRepository,
        quota_repo: QuotaRepository,
        writer: ProjectChatWriter,
        *,
        initial_k: Decimal,
        history_limit: int,
        max_message_chars: int,
        max_prompt_chars: int,
        existing_titles_limit: int,
        daily_work_hours: float,
        quota_daily_max: int,
        now: Callable[[], datetime],
    ) -> None:
        self.repo = repo
        self.quota_repo = quota_repo  # shares the repo's session
        self.writer = writer
        self.initial_k = initial_k
        self.history_limit = history_limit
        self.max_message_chars = max_message_chars
        self.max_prompt_chars = max_prompt_chars
        self.existing_titles_limit = existing_titles_limit
        self.daily_work_hours = daily_work_hours
        self.now = now
        self.quota = ChatQuota(quota_repo, daily_max=quota_daily_max, now=now)

    async def execute(
        self, user_id: UUID, conversation_id: UUID, message: str
    ) -> ProjectChatTurn:
        text = message.strip()
        if not text or len(text) > self.max_message_chars:
            raise InvalidMessageError(
                f"message must be 1-{self.max_message_chars} characters; "
                "paste the main part of the brief"
            )

        # 1-3. Read everything in one short transaction. No LLM yet.
        async with self.repo.transaction():
            prepared = await self._prepare(user_id, conversation_id)
        project = prepared.project
        today = self.now().astimezone(project.time_zone).date()

        # 4. One LLM call (plus a retry), outside any transaction.
        client = project.client_name
        prompt_project = self._prompt_project(project, prepared.work)
        domains = self._prompt_domains(prepared.domains, prepared.k_bias)
        masked_text = mask_contact(text, client)
        history = self._fit_history(
            prepared.history, prompt_project, domains, masked_text, client, today
        )
        context = DraftContext(
            domains=prepared.domains,
            project_deadline=project.deadline,
            today=today,
            existing_titles=tuple(
                mask_contact(t, client) for t in prepared.work.task_titles
            ),
        )
        result = await self.writer.write(
            prompt_project, domains, history, masked_text, context
        )
        if result.reply is None:
            async with self.repo.transaction():
                await self.quota.log(prepared.subscription, result.calls, charged=False)
            raise AIUnavailableError(str(conversation_id))

        # 5. Save both messages, charge, log: all or nothing.
        try:
            return await self._save(
                user_id,
                conversation_id,
                text,
                result.reply,
                result.calls,
                prepared,
                today,
            )
        except (_LostLastCallError, ConversationAppliedError) as error:
            async with self.repo.transaction():
                await self.quota.log(prepared.subscription, result.calls, charged=False)
            if isinstance(error, ConversationAppliedError):
                raise
            raise QuotaExceededError("plan limit reached") from None

    async def _prepare(self, user_id: UUID, conversation_id: UUID) -> _Prepared:
        conversation = await self.repo.get_conversation(user_id, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(str(conversation_id))
        check_open(conversation)
        project = await open_project(self.repo, user_id, conversation.project_id)
        subscription = await self.quota.check(user_id)
        messages = await self.repo.list_messages(user_id, conversation_id)
        return _Prepared(
            project=project,
            subscription=subscription,
            history=pick_history(messages, self.history_limit),
            domains=await self.repo.active_domains(),
            k_bias=await self.repo.get_k_biases(user_id),
            work=await self.repo.existing_work(user_id, project.project_id),
        )

    def _prompt_project(
        self, project: ChatProject, work: ExistingWork
    ) -> PromptProject:
        """What the LLM may see (#3, #19): masked texts, no client name or contacts."""
        client = project.client_name
        limit = self.existing_titles_limit
        return PromptProject(
            name=mask_contact(project.name, client),
            description=mask_contact(project.description, client)
            if project.description
            else None,
            deadline=project.deadline,
            existing_milestones=tuple(
                mask_contact(t, client) for t in work.milestone_titles[:limit]
            ),
            existing_tasks=tuple(
                mask_contact(t, client) for t in work.task_titles[:limit]
            ),
        )

    def _prompt_domains(
        self, domains: dict[UUID, str], k_bias: dict[UUID, Decimal]
    ) -> list[PromptDomain]:
        return [
            PromptDomain(
                domain_id, name, extra_percent(k_bias.get(domain_id, self.initial_k))
            )
            for domain_id, name in domains.items()
        ]

    @staticmethod
    def _line(message: ProjectChatMessage, client: str | None) -> ProjectChatLine:
        if message.role is ChatRole.USER:
            return ProjectChatLine("user", mask_contact(message.content, client))
        draft = message.draft.draft.milestones if message.draft else ()
        return ProjectChatLine("assistant", message.content, draft)

    def _fit_history(
        self,
        history: list[ProjectChatMessage],
        project: PromptProject,
        domains: list[PromptDomain],
        message: str,
        client: str | None,
        today: date,
    ) -> list[ProjectChatLine]:
        """Keep the brief; drop the oldest of the others until the prompt fits (#18)."""
        lines = [self._line(m, client) for m in history]
        while len(lines) > 1:
            prompt = self.writer.render(project, domains, lines, message, today)
            if len(prompt) <= self.max_prompt_chars:
                break
            del lines[1]  # lines[0] is the brief
        return lines

    async def _save(
        self,
        user_id: UUID,
        conversation_id: UUID,
        text: str,
        reply: ProjectReply,
        calls: Sequence[LLMCall],
        prepared: _Prepared,
        today: date,
    ) -> ProjectChatTurn:
        project = prepared.project
        async with self.repo.transaction():
            # Lock the chat: an Apply in another tab meanwhile wins (#5 of the flow).
            conversation = await self.repo.get_conversation(
                user_id, conversation_id, lock=True
            )
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            check_open(conversation)

            saved_draft = None
            if reply.draft is not None:
                numbers = project_numbers(
                    reply.draft,
                    prepared.k_bias,
                    self.initial_k,
                    project.deadline,
                    today,
                    prepared.subscription.is_free,
                    self.daily_work_hours,
                    reply.language,
                )
                draft = reply.draft
                if numbers.warning:
                    draft = replace(draft, warnings=(*draft.warnings, numbers.warning))
                saved_draft = SavedProjectDraft(draft, numbers)
            user_message = await self.repo.add_message(
                user_id,
                conversation_id,
                ChatRole.USER,
                text,  # as written
            )
            ai_message = await self.repo.add_message(
                user_id, conversation_id, ChatRole.ASSISTANT, reply.text, saved_draft
            )
            if not await self.quota_repo.try_charge(prepared.subscription):
                raise _LostLastCallError
            await self.quota.log(prepared.subscription, calls, charged=True)
        return ProjectChatTurn(
            conversation_id, user_message, ai_message, project.time_zone
        )
