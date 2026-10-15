"""FR-08a: Apply — write the newest draft (as is, or edited by hand) as SubTask rows.

No LLM, no quota. The task row is locked, so pressing Apply twice never makes
duplicates (plan #18).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from metanoia_ai.application.ports.chat_repository import (
    ChatRepository,
    SavedSubtask,
)
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    TaskNotSplittableError,
    splittable_problem,
)
from metanoia_ai.domain.task_breakdown import (
    Draft,
    DraftLimits,
    SubtaskInput,
    check_draft,
)


class MessageNotFoundError(Exception):
    """The message is not in this task's chat. → 404."""


class NotLatestDraftError(Exception):
    """The message is not the newest draft of the chat (plan #17). → 409."""


@dataclass(frozen=True)
class AppliedBreakdown:
    task_id: UUID
    subtasks: list[SavedSubtask]


class ApplyTaskBreakdown:
    def __init__(self, repo: ChatRepository, manual_limits: DraftLimits) -> None:
        self.repo = repo
        self.manual_limits = manual_limits  # plan #19: 1-10 subtasks, 1-480 minutes

    async def execute(
        self,
        user_id: UUID,
        task_id: UUID,
        message_id: UUID,
        edited: Sequence[SubtaskInput] | None = None,
    ) -> AppliedBreakdown:
        """Edited list breaks a rule → BadDraftError (422), before touching the DB."""
        edited_draft = (
            check_draft(edited, self.manual_limits) if edited is not None else None
        )
        async with self.repo.transaction():
            task = await self.repo.get_task(user_id, task_id, lock=True)
            if task is None:
                raise TaskNotFoundError(str(task_id))
            problem = splittable_problem(task)  # has_subtasks: already applied
            if problem is not None:
                raise TaskNotSplittableError(problem)

            conversation_id, draft = await self._latest_draft(
                user_id, task_id, message_id
            )
            subtasks = (edited_draft or draft).subtasks
            saved = await self.repo.add_subtasks(user_id, task_id, subtasks)
            await self.repo.mark_applied(user_id, conversation_id, message_id)
        return AppliedBreakdown(task_id, saved)

    async def _latest_draft(
        self, user_id: UUID, task_id: UUID, message_id: UUID
    ) -> tuple[UUID, Draft]:
        """The chat id and the draft of `message_id`, if it is the newest draft."""
        conversation_id = await self.repo.find_conversation(user_id, task_id)
        if conversation_id is None:
            raise MessageNotFoundError(str(message_id))
        messages = await self.repo.list_messages(user_id, conversation_id)
        if all(m.message_id != message_id for m in messages):
            raise MessageNotFoundError(str(message_id))
        latest = await self.repo.latest_draft(user_id, conversation_id)
        if latest is None or latest.message_id != message_id or latest.draft is None:
            raise NotLatestDraftError(str(message_id))
        return conversation_id, latest.draft.draft
