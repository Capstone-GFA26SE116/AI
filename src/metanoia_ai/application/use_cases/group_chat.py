"""FR-24: the Leader's plan chat in a work group.

Same writer and prompts as the personal project chat (FR-08b): `discuss` gives a
checklist, `plan` gives a draft of milestones → tasks → subtasks. New here: every
draft task gets a suggested member (code, FR-23 ranking, work spread out).
The AI writes only the chat; the app creates the work through the Backend and
then marks the draft as applied here.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from metanoia_ai.application.group_capacity import member_candidate
from metanoia_ai.application.llm_chat import ChatQuota
from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.group_assignee_repository import (
    GroupAssigneeRepository,
    GroupWork,
)
from metanoia_ai.application.ports.group_chat_repository import (
    ChatGroup,
    GroupChatMessage,
    GroupChatRepository,
    GroupConversation,
    NewGroupMessage,
)
from metanoia_ai.application.ports.project_chat_repository import SavedProjectDraft
from metanoia_ai.application.ports.quota_repository import QuotaRepository
from metanoia_ai.application.ports.schedule_repository import ScheduleRepository
from metanoia_ai.application.project_chat_writer import (
    PLAN_BUTTON,
    UPDATE_PLAN_BUTTON,
    DraftContext,
    ProjectChatLine,
    ProjectChatWriter,
    PromptDomain,
    PromptProject,
)
from metanoia_ai.application.use_cases.propose_schedule import QuotaExceededError
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
    InvalidMessageError,
)
from metanoia_ai.application.use_cases.suggest_group_assignee import (
    GroupArchivedError,
    GroupTaskNotFoundError,
    NotGroupLeaderError,
)
from metanoia_ai.domain.assignee_ranking import Candidate, GroupRole
from metanoia_ai.domain.free_time import DailyBreak
from metanoia_ai.domain.group_plan import PlanTask, TaskAssignment, assign_tasks
from metanoia_ai.domain.memory_text import mask_contact
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_breakdown import (
    ProjectDraft,
    project_numbers,
    reply_language,
    task_deadline,
)
from metanoia_ai.domain.project_discussion import ChatMode, latest_notes

PLAN_FEATURE = "group_chat"  # sent in the 403 plan_feature_locked answer
DEFAULT_TITLE = {Language.VI: "Lập kế hoạch nhóm", Language.EN: "Team plan"}
MAX_TITLE_CHARS = 200
NO_TASK = UUID(int=0)  # no group task to leave out of the group work


class GroupChatNotFoundError(GroupTaskNotFoundError):
    """No such group chat, or the caller is not in its group (404)."""


class DraftAlreadyAppliedError(Exception):
    """Not a draft of this chat, or it was marked applied already (409)."""


@dataclass(frozen=True)
class GroupChatTurn:
    conversation_id: UUID
    user_message: GroupChatMessage
    ai_message: GroupChatMessage


@dataclass(frozen=True)
class GroupChatView:
    conversation: GroupConversation
    messages: tuple[GroupChatMessage, ...]


class _LostLastCallError(Exception):
    """Another request used the last call while this one was running."""


class GroupChat:
    def __init__(
        self,
        chats: GroupChatRepository,
        members: GroupAssigneeRepository,
        schedules: ScheduleRepository,
        quota_repo: QuotaRepository,
        writer: ProjectChatWriter,
        *,
        initial_k: Decimal,
        history_limit: int,
        max_message_chars: int,
        existing_titles_limit: int,
        daily_work_hours: float,
        window_days: int,
        max_candidates: int,
        now: Callable[[], datetime],
        breaks: Sequence[DailyBreak] = (),
    ) -> None:
        self.chats = chats
        self.members = members
        self.schedules = schedules
        self.quota_repo = quota_repo
        self.quota = ChatQuota(quota_repo, now=now)
        self.writer = writer
        self.initial_k = initial_k
        self.history_limit = history_limit
        self.max_message_chars = max_message_chars
        self.existing_titles_limit = existing_titles_limit
        self.daily_work_hours = daily_work_hours
        self.window_days = window_days
        self.max_candidates = max_candidates
        self.now = now
        self.breaks = tuple(breaks)

    # --- access ---

    async def _leader_group(self, group_id: UUID, caller_id: UUID) -> ChatGroup:
        group = await self.chats.get_group(group_id, caller_id)
        if group is None or group.caller_role is None:
            raise GroupChatNotFoundError(str(group_id))
        if group.caller_role is not GroupRole.LEADER:
            raise NotGroupLeaderError(str(group_id))
        return group

    async def _leader_chat(
        self, conversation_id: UUID, caller_id: UUID
    ) -> tuple[GroupConversation, ChatGroup]:
        conversation = await self.chats.get_conversation(conversation_id)
        if conversation is None:
            raise GroupChatNotFoundError(str(conversation_id))
        group = await self._leader_group(conversation.group_id, caller_id)
        return conversation, group

    # --- start, list, read, applied ---

    async def start(
        self, caller_id: UUID, group_id: UUID, title: str | None, language: Language
    ) -> GroupConversation:
        group = await self._leader_group(group_id, caller_id)
        if group.archived:
            raise GroupArchivedError(str(group_id))
        name = (title or "").strip()[:MAX_TITLE_CHARS] or DEFAULT_TITLE[language]
        async with self.chats.transaction():
            return await self.chats.create_conversation(group_id, name)

    async def list_chats(
        self, caller_id: UUID, group_id: UUID
    ) -> list[GroupConversation]:
        await self._leader_group(group_id, caller_id)
        return await self.chats.list_conversations(group_id)

    async def get(self, caller_id: UUID, conversation_id: UUID) -> GroupChatView:
        conversation, _ = await self._leader_chat(conversation_id, caller_id)
        messages = await self.chats.list_messages(conversation_id)
        return GroupChatView(conversation, tuple(messages))

    async def mark_applied(
        self, caller_id: UUID, conversation_id: UUID, message_id: UUID
    ) -> None:
        await self._leader_chat(conversation_id, caller_id)
        async with self.chats.transaction():
            if not await self.chats.mark_applied(conversation_id, message_id):
                raise DraftAlreadyAppliedError(str(message_id))

    # --- send ---

    async def send(
        self, caller_id: UUID, conversation_id: UUID, text: str, mode: ChatMode
    ) -> GroupChatTurn:
        message = text.strip()
        if len(message) > self.max_message_chars or (
            not message and mode is ChatMode.DISCUSS
        ):
            raise InvalidMessageError("empty or too long")

        conversation, group = await self._leader_chat(conversation_id, caller_id)
        if group.archived:
            raise GroupArchivedError(str(group.group_id))
        subscription = await self.quota.check_paid(caller_id, PLAN_FEATURE)
        history = await self.chats.list_messages(conversation_id)
        domains = await self.chats.active_domains()
        work = await self.chats.existing_work(group.group_id)

        brief = history[0].content if history else None
        language = reply_language(message, brief or group.name)
        if not message:  # the plan button
            has_draft = any(m.draft for m in history)
            message = (UPDATE_PLAN_BUTTON if has_draft else PLAN_BUTTON)[language]
        today = self.now().astimezone(group.time_zone).date()
        project = PromptProject(
            name=mask_contact(group.name, None),
            description=mask_contact(group.description, None)
            if group.description
            else None,
            deadline=group.deadline,
            existing_milestones=tuple(
                mask_contact(t, None) for t in work.milestone_titles
            ),
            existing_tasks=tuple(
                mask_contact(t, None)
                for t in work.task_titles[: self.existing_titles_limit]
            ),
        )
        lines = [to_line(m) for m in pick_history(history, self.history_limit)]
        masked = mask_contact(message, None)
        if mode is ChatMode.DISCUSS:
            result = await self.writer.discuss(project, lines, masked, today, language)
        else:
            prompt_domains = [PromptDomain(d, name, 0) for d, name in domains.items()]
            context = DraftContext(
                domains=domains,
                project_deadline=group.deadline,
                today=today,
                existing_titles=tuple(mask_contact(t, None) for t in work.task_titles),
                language=language,
            )
            agreed = latest_notes(m.notes for m in history)
            result = await self.writer.write(
                project, prompt_domains, lines, masked, context, (), agreed
            )
        if result.reply is None:
            async with self.chats.transaction():
                await self.quota.log(subscription, result.calls, charged=False)
            raise AIUnavailableError(str(conversation_id))

        reply = result.reply
        saved = None
        assignments: tuple[TaskAssignment, ...] = ()
        if reply.draft is not None:
            saved = SavedProjectDraft(
                draft=reply.draft,
                numbers=project_numbers(
                    reply.draft,
                    {},
                    self.initial_k,
                    group.deadline,
                    today,
                    subscription.is_free,
                    self.daily_work_hours,
                    language,
                ),
            )
            assignments = tuple(await self._assign(group, reply.draft, language))
        user_message = NewGroupMessage(ChatRole.USER, message)
        ai_message = NewGroupMessage(
            ChatRole.ASSISTANT, reply.text, saved, assignments, reply.notes
        )
        try:
            async with self.chats.transaction():
                if not await self.quota_repo.try_charge(subscription):
                    raise _LostLastCallError
                await self.quota.log(subscription, result.calls, charged=True)
                saved_user = await self.chats.add_message(conversation_id, user_message)
                saved_ai = await self.chats.add_message(conversation_id, ai_message)
        except _LostLastCallError:
            async with self.chats.transaction():
                await self.quota.log(subscription, result.calls, charged=False)
            raise QuotaExceededError("plan limit reached") from None
        return GroupChatTurn(conversation.conversation_id, saved_user, saved_ai)

    async def _assign(
        self, group: ChatGroup, draft: ProjectDraft, language: Language
    ) -> list[TaskAssignment]:
        """A suggested Leader / Member for every task of the draft (FR-23 ranking)."""
        now = self.now()
        longest = now + timedelta(days=self.window_days)
        tasks = []
        for milestone in draft.milestones:
            due = (
                task_deadline(milestone.deadline, group.time_zone)
                if milestone.deadline
                else longest
            )
            for task in milestone.tasks:
                tasks.append(
                    PlanTask(task.key, task.domain_id, task.estimated_minutes, due)
                )
        if not tasks:
            return []
        members = await self.members.get_members(group.group_id, self.max_candidates)
        window_end = max(max(t.deadline for t in tasks), longest)
        work = await self.members.get_group_work(
            [m.user_id for m in members], now, window_end, NO_TASK
        )
        planning = {
            m.user_id: await self.schedules.get_planning_data(
                m.user_id, now, window_end
            )
            for m in members
        }

        def candidates_for(
            task: PlanTask, given: Mapping[UUID, int]
        ) -> list[Candidate]:
            end = task.deadline if task.deadline > now else longest
            return [
                member_candidate(
                    m,
                    planning[m.user_id],
                    work.get(m.user_id, GroupWork((), 0)),
                    now=now,
                    window_end=end,
                    initial_k=self.initial_k,
                    breaks=self.breaks,
                    estimate_minutes=task.estimated_minutes,
                    domain_id=task.domain_id,
                    extra_minutes=given.get(m.user_id, 0),
                )
                for m in members
            ]

        return assign_tasks(tasks, candidates_for, language)


def pick_history(
    messages: Sequence[GroupChatMessage], recent: int
) -> list[GroupChatMessage]:
    """The first message (the brief) + the `recent` newest ones, oldest first."""
    if len(messages) <= recent + 1:
        return list(messages)
    return [messages[0], *messages[-recent:]]


def to_line(message: GroupChatMessage) -> ProjectChatLine:
    """An earlier message as the prompt shows it (Leader text masked)."""
    role = "user" if message.role is ChatRole.USER else "assistant"
    text = mask_contact(message.content, None) if role == "user" else message.content
    draft = message.draft.draft.milestones if message.draft else ()
    return ProjectChatLine(role, text, draft, message.notes)
