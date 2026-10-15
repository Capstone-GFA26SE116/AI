"""FR-08a endpoints: chat about one big task, then apply its subtasks.

Only POST messages uses an AI call. 409 answers carry {"code": …} so the
Frontend can show the right message (plan #21).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import (
    get_apply_task_breakdown,
    get_send_task_chat_message,
    get_task_chat,
)
from metanoia_ai.application.ports.chat_repository import ChatMessage, ChatRole
from metanoia_ai.application.use_cases.apply_task_breakdown import (
    ApplyTaskBreakdown,
    MessageNotFoundError,
    NotLatestDraftError,
)
from metanoia_ai.application.use_cases.get_task_chat import GetTaskChat
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
    InvalidMessageError,
    SendTaskChatMessage,
    TaskNotSplittableError,
)
from metanoia_ai.domain.task_breakdown import BadDraftError, ReplyKind, SubtaskInput

router = APIRouter(prefix="/chat/tasks", tags=["chat"])


# --- request / response shapes ---


class SendRequest(BaseModel):
    message: str  # 1 to CHAT_MESSAGE_MAX_CHARS characters, checked by the use case


class EditedSubtask(BaseModel):
    title: str
    estimated_minutes: int


class ApplyRequest(BaseModel):
    message_id: UUID
    subtasks: list[EditedSubtask] | None = None  # edited by hand (plan #4)


class SubtaskResponse(BaseModel):
    title: str
    estimated_minutes: int
    order_index: int
    reason: str | None


class SimilarResponse(BaseModel):
    count: int
    average_ratio: float | None


class DraftResponse(BaseModel):
    message_id: UUID  # the message to send to /apply
    subtasks: list[SubtaskResponse]
    your_estimate_minutes: int
    adjusted_minutes: int
    subtask_total_minutes: int
    adjusted_is_hint: bool
    warning: str | None
    similar: SimilarResponse | None


class MessageResponse(BaseModel):
    id: UUID
    role: ChatRole
    kind: ReplyKind | None = None  # AI messages only
    content: str
    created_at: datetime  # ISO 8601 in the user's time zone
    draft: DraftResponse | None = None


class ChatTurnResponse(BaseModel):
    task_id: UUID
    user_message: MessageResponse
    ai_message: MessageResponse


class TaskChatResponse(BaseModel):
    task_id: UUID
    messages: list[MessageResponse]
    latest_draft: DraftResponse | None
    applied: bool


class AppliedSubtaskResponse(BaseModel):
    id: UUID
    title: str
    estimated_minutes: int
    order_index: int


class AppliedResponse(BaseModel):
    task_id: UUID
    subtasks: list[AppliedSubtaskResponse]


def to_draft(message: ChatMessage) -> DraftResponse | None:
    saved = message.draft
    if saved is None:
        return None
    numbers, similar = saved.numbers, saved.similar
    return DraftResponse(
        message_id=message.message_id,
        subtasks=[
            SubtaskResponse(
                title=s.title,
                estimated_minutes=s.estimated_minutes,
                order_index=s.order_index,
                reason=s.reason,
            )
            for s in saved.draft.subtasks
        ],
        your_estimate_minutes=numbers.your_estimate_minutes,
        adjusted_minutes=numbers.adjusted_minutes,
        subtask_total_minutes=numbers.subtask_total_minutes,
        adjusted_is_hint=numbers.adjusted_is_hint,
        warning=numbers.warning,
        similar=None
        if similar is None
        else SimilarResponse(count=similar.count, average_ratio=similar.average_ratio),
    )


def to_message(message: ChatMessage, zone: ZoneInfo) -> MessageResponse:
    is_ai = message.role is ChatRole.ASSISTANT
    kind = None
    if is_ai:
        kind = ReplyKind.DRAFT if message.draft else ReplyKind.QUESTION
    return MessageResponse(
        id=message.message_id,
        role=message.role,
        kind=kind,
        content=message.content,
        created_at=message.created_at.astimezone(zone),
        draft=to_draft(message),
    )


# --- errors ---


def conflict(code: str, reason: str | None = None) -> HTTPException:
    detail = {"code": code} if reason is None else {"code": code, "reason": reason}
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def not_found(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found"
    )


# --- endpoints ---


@router.post("/{task_id}/messages")
async def send_message(
    task_id: UUID,
    body: SendRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[SendTaskChatMessage, Depends(get_send_task_chat_message)],
) -> ChatTurnResponse:
    """Send one message about this task. Uses 1 AI call when the AI answers."""
    try:
        turn = await use_case.execute(user_id, task_id, body.message)
    except InvalidMessageError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except TaskNotFoundError as error:
        raise not_found("Task") from error
    except TaskNotSplittableError as error:
        raise conflict("task_not_splittable", error.reason) from error
    except NoSubscriptionError as error:
        raise conflict("no_active_plan") from error
    except QuotaExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="AI quota exceeded"
        ) from error
    except AIUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The AI could not answer. Please try again.",
        ) from error
    return ChatTurnResponse(
        task_id=turn.task_id,
        user_message=to_message(turn.user_message, turn.time_zone),
        ai_message=to_message(turn.ai_message, turn.time_zone),
    )


@router.get("/{task_id}")
async def get_chat(
    task_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GetTaskChat, Depends(get_task_chat)],
) -> TaskChatResponse:
    """The chat of this task. No chat yet → no messages. No AI call."""
    try:
        view = await use_case.execute(user_id, task_id)
    except TaskNotFoundError as error:
        raise not_found("Task") from error
    return TaskChatResponse(
        task_id=view.task_id,
        messages=[to_message(m, view.time_zone) for m in view.messages],
        latest_draft=to_draft(view.latest_draft) if view.latest_draft else None,
        applied=view.applied,
    )


@router.post("/{task_id}/apply", status_code=status.HTTP_201_CREATED)
async def apply(
    task_id: UUID,
    body: ApplyRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[ApplyTaskBreakdown, Depends(get_apply_task_breakdown)],
) -> AppliedResponse:
    """Write the newest draft (or the edited list) as subtasks. No AI call."""
    edited = (
        [SubtaskInput(s.title, s.estimated_minutes) for s in body.subtasks]
        if body.subtasks is not None
        else None
    )
    try:
        result = await use_case.execute(user_id, task_id, body.message_id, edited)
    except BadDraftError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except TaskNotFoundError as error:
        raise not_found("Task") from error
    except MessageNotFoundError as error:
        raise not_found("Message") from error
    except TaskNotSplittableError as error:
        if error.reason == "has_subtasks":
            raise conflict("has_subtasks") from error
        raise conflict("task_not_splittable", error.reason) from error
    except NotLatestDraftError as error:
        raise conflict("not_latest_draft") from error
    return AppliedResponse(
        task_id=result.task_id,
        subtasks=[
            AppliedSubtaskResponse(
                id=s.subtask_id,
                title=s.title,
                estimated_minutes=s.estimated_minutes,
                order_index=s.order_index,
            )
            for s in result.subtasks
        ],
    )
