"""FR-08b-1: Apply — write the newest draft (as is, or edited by hand) as new
Milestone + Flexible Task rows.

No LLM, no quota. Only creates rows: existing milestones and tasks are never
changed (AC4). The project row is locked, so pressing Apply twice, or in two
tabs, never makes duplicates (#22).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from uuid import UUID

from metanoia_ai.application.ports.project_chat_repository import (
    NewMilestone,
    NewSubtask,
    NewTask,
    ProjectChatMessage,
    ProjectChatRepository,
)
from metanoia_ai.application.use_cases.apply_task_breakdown import (
    MessageNotFoundError,
    NotLatestDraftError,
)
from metanoia_ai.application.use_cases.send_project_chat_message import check_open
from metanoia_ai.application.use_cases.start_project_chat import (
    ConversationAppliedError,
    ConversationNotFoundError,
    open_project,
)
from metanoia_ai.domain.models import EstimateSource, estimate_source
from metanoia_ai.domain.project_breakdown import (
    DraftMilestone,
    DroppedTask,
    MilestoneInput,
    ProjectDraft,
    ProjectLimits,
    ai_minutes_of,
    check_project_draft,
    resolve_deadlines,
    restore_client,
    same_title_key,
    split_duplicates,
    task_deadline,
)


class DomainInactiveError(Exception):
    """A domain of the draft was turned off since the draft was made. → 409."""


@dataclass(frozen=True)
class AppliedTask:
    task_id: UUID
    title: str
    estimated_minutes: int
    estimate_source: EstimateSource


@dataclass(frozen=True)
class AppliedMilestone:
    milestone_id: UUID
    title: str
    deadline: date
    tasks: list[AppliedTask]
    reused: bool = False  # FR-08b-9: added to a milestone the project already had


@dataclass(frozen=True)
class AppliedProject:
    milestones: list[AppliedMilestone]
    skipped: list[DroppedTask]  # duplicates of existing tasks, not created


class ApplyProjectBreakdown:
    def __init__(
        self,
        repo: ProjectChatRepository,
        manual_limits: ProjectLimits,
        now: Callable[[], datetime],
    ) -> None:
        self.repo = repo
        self.manual_limits = manual_limits  # #21: minutes 5-960
        self.now = now

    async def execute(
        self,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        edited: Sequence[MilestoneInput] | None = None,
    ) -> AppliedProject:
        """An edited plan that breaks a rule → BadDraftError (422), nothing written."""
        async with self.repo.transaction():
            conversation = await self.repo.get_conversation(user_id, conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            # Lock the project: one Apply at a time per project (#22).
            project = await open_project(
                self.repo, user_id, conversation.project_id, lock=True
            )
            conversation = await self.repo.get_conversation(
                user_id, conversation_id, lock=True
            )
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            check_open(conversation)  # a Closed chat → 409
            latest = await self._latest_draft(user_id, conversation_id, message_id)
            if latest.is_applied:  # FR-08b-4: each draft once, the chat many times
                raise ConversationAppliedError(str(message_id))
            assert latest.draft is not None  # checked by _latest_draft
            saved = latest.draft.draft

            domains = await self.repo.active_domains()
            today = self.now().astimezone(project.time_zone).date()
            if edited is None:
                draft = saved
                if any(t.domain_id not in domains for t in draft.tasks):
                    raise DomainInactiveError(str(conversation_id))
            else:
                checked = keep_draft_subtasks(
                    check_project_draft(
                        edited, domains, self.manual_limits, assign_keys=False
                    ),
                    edited,
                    saved,
                )
                draft = resolve_deadlines(
                    ProjectDraft(checked), project.deadline, today
                )

            draft = restore_client(
                draft, project.client_name, self.manual_limits.max_milestone_title_chars
            )
            work = await self.repo.existing_work(user_id, project.project_id)
            draft, skipped = split_duplicates(
                draft, work.task_titles
            )  # #33: may be empty

            order = await self.repo.next_milestone_order(user_id, project.project_id)
            # FR-08b-9: a draft milestone named like an existing one adds its tasks
            # there instead of making a second milestone with the same name.
            existing = {
                same_title_key(m.title): m.milestone_id
                for m in await self.repo.list_milestones(user_id, project.project_id)
            }
            created: list[AppliedMilestone] = []
            for milestone in draft.milestones:
                assert milestone.deadline is not None  # set by resolve_deadlines
                milestone_id = existing.get(same_title_key(milestone.title))
                reused = milestone_id is not None
                if milestone_id is None:
                    milestone_id = await self.repo.add_milestone(
                        user_id,
                        project.project_id,
                        NewMilestone(milestone.title, milestone.deadline, order),
                    )
                    existing[same_title_key(milestone.title)] = milestone_id
                    order += 1
                tasks: list[AppliedTask] = []
                for task in milestone.tasks:
                    ai_minutes = ai_minutes_of(task.key, saved)
                    task_id = await self.repo.add_task(
                        user_id,
                        project.project_id,
                        NewTask(
                            milestone_id=milestone_id,
                            title=task.title,
                            domain_id=task.domain_id,
                            deadline=task_deadline(
                                milestone.deadline, project.time_zone
                            ),
                            estimated_minutes=task.estimated_minutes,
                            ai_estimated_minutes=ai_minutes,
                            subtasks=tuple(
                                NewSubtask(s.title, s.estimated_minutes, index)
                                for index, s in enumerate(task.subtasks, start=1)
                            ),
                        ),
                    )
                    tasks.append(
                        AppliedTask(
                            task_id,
                            task.title,
                            task.estimated_minutes,
                            estimate_source(task.estimated_minutes, ai_minutes),
                        )
                    )
                created.append(
                    AppliedMilestone(
                        milestone_id, milestone.title, milestone.deadline, tasks, reused
                    )
                )
            await self.repo.mark_applied(user_id, conversation_id, message_id)
        return AppliedProject(created, skipped)

    async def _latest_draft(
        self, user_id: UUID, conversation_id: UUID, message_id: UUID
    ) -> ProjectChatMessage:
        """The message of `message_id`, if it holds the newest draft of this chat."""
        messages = await self.repo.list_messages(user_id, conversation_id)
        if all(m.message_id != message_id for m in messages):
            raise MessageNotFoundError(str(message_id))
        latest = await self.repo.latest_draft(user_id, conversation_id)
        if latest is None or latest.message_id != message_id or latest.draft is None:
            raise NotLatestDraftError(str(message_id))
        return latest


def keep_draft_subtasks(
    checked: tuple[DraftMilestone, ...],
    edited: Sequence[MilestoneInput],
    saved: ProjectDraft,
) -> tuple[DraftMilestone, ...]:
    """FR-08b-8: an edited task that sent no `subtasks` keeps the draft's (by key);
    sent ones, or [] (none), are used as sent."""
    by_key = {t.key: t.subtasks for t in saved.tasks if t.key}
    sent = {
        (m_index, t_index): task.subtasks is not None
        for m_index, milestone in enumerate(edited)
        for t_index, task in enumerate(milestone.tasks)
    }
    return tuple(
        replace(
            milestone,
            tasks=tuple(
                task
                if sent[(m_index, t_index)]
                else replace(task, subtasks=by_key.get(task.key, ()))
                for t_index, task in enumerate(milestone.tasks)
            ),
        )
        for m_index, milestone in enumerate(checked)
    )
