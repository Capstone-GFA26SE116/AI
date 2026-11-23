"""FR-23 endpoint: suggest who should do a group task (Work Group, Backend PR #15).

Called by the Web / Mobile app when the Leader opens a group task. It only suggests;
the Leader assigns through the Backend (PUT /api/group-tasks/{id}/assignee).
"""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_group_chat, get_suggest_group_assignee
from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.group_chat_repository import (
    GroupChatMessage,
    GroupConversation,
)
from metanoia_ai.application.use_cases.group_chat import (
    DraftAlreadyAppliedError,
    GroupChat,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    PlanFeatureError,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
    InvalidMessageError,
)
from metanoia_ai.application.use_cases.suggest_group_assignee import (
    GroupArchivedError,
    GroupTaskClosedError,
    GroupTaskNotFoundError,
    NotGroupLeaderError,
    ReasonSkip,
    SuggestGroupAssignee,
    SuggestionResult,
)
from metanoia_ai.domain.assignee_ranking import GroupRole
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_discussion import ChatMode

router = APIRouter(prefix="/groups", tags=["groups"])


class SuggestRequest(BaseModel):
    domain_id: UUID | None = None  # the work category the Leader picks; optional
    language: Language = Language.VI


class FreeTimeResponse(BaseModel):
    start: datetime  # ISO 8601 in the member's time zone
    end: datetime


class SuggestionResponse(BaseModel):
    user_id: UUID
    role: GroupRole
    rank: int  # 1 = best
    is_current_assignee: bool
    busy_at_fixed_time: bool
    free_minutes: int  # free work time before the deadline, after other work
    group_work_minutes: int  # open group work in that time
    fits: bool | None  # None: the task has no estimate
    reason: str | None
    free_times: list[FreeTimeResponse]  # the longest open spots on their calendar


class SuggestResponse(BaseModel):
    task_id: UUID
    suggestions: list[SuggestionResponse]
    reasons_skipped: ReasonSkip | None  # why reasons are missing, if they are


@router.post("/tasks/{group_task_id}/assignee-suggestions")
async def suggest_assignee(
    group_task_id: UUID,
    body: SuggestRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[SuggestGroupAssignee, Depends(get_suggest_group_assignee)],
) -> SuggestResponse:
    """Rank the Leaders and Members for this task. Writes nothing to the group."""
    try:
        result = await use_case.execute(
            user_id, group_task_id, body.domain_id, body.language
        )
    except GroupTaskNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group task not found"
        ) from error
    except NotGroupLeaderError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a Leader of the group can ask for suggestions",
        ) from error
    except GroupArchivedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="The group is archived"
        ) from error
    except GroupTaskClosedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The task is already Done or Skipped",
        ) from error
    return to_response(result)


def to_response(result: SuggestionResult) -> SuggestResponse:
    return SuggestResponse(
        task_id=result.task_id,
        suggestions=[
            SuggestionResponse(
                user_id=s.user_id,
                role=s.role,
                rank=s.rank,
                is_current_assignee=s.is_current_assignee,
                busy_at_fixed_time=s.busy_at_fixed_time,
                free_minutes=s.free_minutes,
                group_work_minutes=s.group_work_minutes,
                fits=s.fits,
                reason=s.reason,
                free_times=[
                    FreeTimeResponse(start=t.start, end=t.end) for t in s.free_times
                ],
            )
            for s in result.suggestions
        ],
        reasons_skipped=result.reasons_skipped,
    )


# --- FR-24: the Leader's plan chat ---


class StartChatRequest(BaseModel):
    title: str | None = None
    language: Language = Language.VI


class ChatResponse(BaseModel):
    conversation_id: UUID
    group_id: UUID
    title: str
    status: str
    created_at: datetime


class SendRequest(BaseModel):
    text: str = ""  # empty + mode "plan" = the "Plan" button
    mode: ChatMode = ChatMode.PLAN


class AssigneeResponse(BaseModel):
    user_id: UUID | None  # None: no Leader / Member to give it to
    fits: bool | None
    reason: str | None


class SubtaskResponse(BaseModel):
    title: str
    estimated_minutes: int
    reason: str | None


class TaskResponse(BaseModel):
    key: str | None
    title: str
    domain_id: UUID
    domain_name: str
    estimated_minutes: int
    reason: str | None
    subtasks: list[SubtaskResponse]
    suggested_assignee: AssigneeResponse | None


class MilestoneResponse(BaseModel):
    key: str | None
    title: str
    deadline: date | None
    reason: str | None
    tasks: list[TaskResponse]


class DraftResponse(BaseModel):
    milestones: list[MilestoneResponse]
    reminders: list[str]
    warnings: list[str]
    total_estimated_minutes: int


class NotesResponse(BaseModel):
    clear: list[str]
    missing: list[str]
    questions: list[str]
    risks: list[str]
    ready_to_plan: bool


class MessageResponse(BaseModel):
    message_id: UUID
    role: str  # "user" / "assistant"
    content: str
    created_at: datetime
    draft: DraftResponse | None
    notes: NotesResponse | None
    is_applied: bool


class ChatViewResponse(BaseModel):
    conversation: ChatResponse
    messages: list[MessageResponse]


class TurnResponse(BaseModel):
    conversation_id: UUID
    user_message: MessageResponse
    ai_message: MessageResponse


def chat_errors(error: Exception) -> HTTPException:
    """The same codes as the project chat and FR-23."""
    if isinstance(error, GroupTaskNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Group chat not found")
    if isinstance(error, NotGroupLeaderError):
        return HTTPException(
            status.HTTP_403_FORBIDDEN, "Only a Leader of the group can use the chat"
        )
    if isinstance(error, PlanFeatureError):
        return HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"code": "plan_feature_locked", "feature": error.feature},
        )
    if isinstance(error, GroupArchivedError):
        return HTTPException(status.HTTP_409_CONFLICT, "The group is archived")
    if isinstance(error, DraftAlreadyAppliedError):
        return HTTPException(status.HTTP_409_CONFLICT, "already_applied")
    if isinstance(error, NoSubscriptionError):
        return HTTPException(status.HTTP_409_CONFLICT, "no_active_plan")
    if isinstance(error, InvalidMessageError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Message empty or too long"
        )
    if isinstance(error, QuotaExceededError):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "AI quota exceeded")
    if isinstance(error, AIUnavailableError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, "AI unavailable, try again")
    raise error


CHAT_ERRORS = (
    GroupTaskNotFoundError,
    NotGroupLeaderError,
    PlanFeatureError,
    GroupArchivedError,
    DraftAlreadyAppliedError,
    NoSubscriptionError,
    InvalidMessageError,
    QuotaExceededError,
    AIUnavailableError,
)


@router.post("/{group_id}/chats", status_code=status.HTTP_201_CREATED)
async def start_chat(
    group_id: UUID,
    body: StartChatRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GroupChat, Depends(get_group_chat)],
) -> ChatResponse:
    try:
        chat = await use_case.start(user_id, group_id, body.title, body.language)
    except CHAT_ERRORS as error:
        raise chat_errors(error) from error
    return to_chat(chat)


@router.get("/{group_id}/chats")
async def list_chats(
    group_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GroupChat, Depends(get_group_chat)],
) -> list[ChatResponse]:
    try:
        chats = await use_case.list_chats(user_id, group_id)
    except CHAT_ERRORS as error:
        raise chat_errors(error) from error
    return [to_chat(c) for c in chats]


@router.get("/chats/{conversation_id}")
async def get_chat(
    conversation_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GroupChat, Depends(get_group_chat)],
) -> ChatViewResponse:
    try:
        view = await use_case.get(user_id, conversation_id)
    except CHAT_ERRORS as error:
        raise chat_errors(error) from error
    return ChatViewResponse(
        conversation=to_chat(view.conversation),
        messages=[to_message(m) for m in view.messages],
    )


@router.post("/chats/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID,
    body: SendRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GroupChat, Depends(get_group_chat)],
) -> TurnResponse:
    """discuss → checklist; plan → draft with a suggested member per task. Writes
    only the chat: the app creates the work through the Backend, then calls /applied."""
    try:
        turn = await use_case.send(user_id, conversation_id, body.text, body.mode)
    except CHAT_ERRORS as error:
        raise chat_errors(error) from error
    return TurnResponse(
        conversation_id=turn.conversation_id,
        user_message=to_message(turn.user_message),
        ai_message=to_message(turn.ai_message),
    )


@router.post(
    "/chats/{conversation_id}/messages/{message_id}/applied",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def mark_applied(
    conversation_id: UUID,
    message_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GroupChat, Depends(get_group_chat)],
) -> None:
    try:
        await use_case.mark_applied(user_id, conversation_id, message_id)
    except CHAT_ERRORS as error:
        raise chat_errors(error) from error


def to_chat(chat: GroupConversation) -> ChatResponse:
    return ChatResponse(
        conversation_id=chat.conversation_id,
        group_id=chat.group_id,
        title=chat.title,
        status=chat.status.value,
        created_at=chat.created_at,
    )


def to_message(message: GroupChatMessage) -> MessageResponse:
    draft = None
    if message.draft is not None:
        by_key = {a.task_key: a for a in message.assignments}
        saved = message.draft
        draft = DraftResponse(
            milestones=[
                MilestoneResponse(
                    key=m.key,
                    title=m.title,
                    deadline=m.deadline,
                    reason=m.reason,
                    tasks=[
                        TaskResponse(
                            key=t.key,
                            title=t.title,
                            domain_id=t.domain_id,
                            domain_name=t.domain_name,
                            estimated_minutes=t.estimated_minutes,
                            reason=t.reason,
                            subtasks=[
                                SubtaskResponse(
                                    title=s.title,
                                    estimated_minutes=s.estimated_minutes,
                                    reason=s.reason,
                                )
                                for s in t.subtasks
                            ],
                            suggested_assignee=(
                                AssigneeResponse(
                                    user_id=by_key[t.key].user_id,
                                    fits=by_key[t.key].fits,
                                    reason=by_key[t.key].reason,
                                )
                                if t.key in by_key
                                else None
                            ),
                        )
                        for t in m.tasks
                    ],
                )
                for m in saved.draft.milestones
            ],
            reminders=list(saved.draft.reminders),
            warnings=list(saved.draft.warnings),
            total_estimated_minutes=saved.numbers.total_estimated_minutes,
        )
    notes = None
    if message.notes is not None:
        n = message.notes
        notes = NotesResponse(
            clear=list(n.clear),
            missing=list(n.missing),
            questions=list(n.questions),
            risks=list(n.risks),
            ready_to_plan=n.ready_to_plan,
        )
    return MessageResponse(
        message_id=message.message_id,
        role="user" if message.role is ChatRole.USER else "assistant",
        content=message.content,
        created_at=message.created_at,
        draft=draft,
        notes=notes,
        is_applied=message.is_applied,
    )
