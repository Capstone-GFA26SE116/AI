"""FR-08b-1 endpoints: chat about a project brief, then apply milestones + tasks.

Only POST messages uses an AI call. 409 answers carry {"code": …} like FR-08a.
FR-08b-2: a message may carry documents (JSON `document_ids`, or multipart with
uploaded files). Document errors carry {"code": …, "file": …} where useful.
"""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import (
    get_apply_project_breakdown,
    get_get_project_chat,
    get_list_project_chats,
    get_send_project_chat_message,
    get_start_project_chat,
)
from metanoia_ai.api.routes.chat import conflict, not_found
from metanoia_ai.application.ports.document_source import (
    DocumentFetchError,
    DocumentNotFoundError,
)
from metanoia_ai.application.ports.project_chat_repository import (
    AttachmentSource,
    ChatAttachment,
    ChatRole,
    ConversationStatus,
    ProjectChatMessage,
    ProjectConversation,
)
from metanoia_ai.application.use_cases.apply_project_breakdown import (
    ApplyProjectBreakdown,
    DomainInactiveError,
)
from metanoia_ai.application.use_cases.apply_task_breakdown import (
    MessageNotFoundError,
    NotLatestDraftError,
)
from metanoia_ai.application.use_cases.get_project_chat import GetProjectChat
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.send_project_chat_message import (
    AttachedDocuments,
    DocumentAccessOffError,
    ProjectChatTurn,
    SendProjectChatMessage,
    UploadedFile,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
    InvalidMessageError,
)
from metanoia_ai.application.use_cases.start_project_chat import (
    ConversationAppliedError,
    ConversationNotFoundError,
    ListProjectChats,
    ProjectClosedError,
    ProjectNotFoundError,
    StartProjectChat,
)
from metanoia_ai.domain.document_text import DocumentError, DocumentErrorCode
from metanoia_ai.domain.models import EstimateSource
from metanoia_ai.domain.project_breakdown import (
    DuplicateOf,
    MilestoneInput,
    TaskInput,
)
from metanoia_ai.domain.task_breakdown import BadDraftError, ReplyKind

router = APIRouter(prefix="/chat", tags=["project chat"])


# --- request / response shapes ---


class ConversationResponse(BaseModel):
    conversation_id: UUID
    title: str
    status: ConversationStatus
    created_at: datetime


class SendRequest(BaseModel):
    # Up to CHAT_PROJECT_MESSAGE_MAX_CHARS, checked by the use case. May be empty
    # only when documents are attached.
    message: str
    document_ids: list[UUID] = []  # FR-08b-2: library files, 0-3 (AC1, AC4)


class AttachmentResponse(BaseModel):
    """An attached document. The extracted text is never sent back."""

    id: UUID
    name: str
    source: AttachmentSource
    document_id: UUID | None
    is_truncated: bool


class DomainResponse(BaseModel):
    id: UUID
    name: str


class DraftTaskResponse(BaseModel):
    key: str | None
    title: str
    domain: DomainResponse
    estimated_minutes: int  # shown as "AI guess — change it if you think otherwise"
    reason: str | None


class DraftMilestoneResponse(BaseModel):
    key: str | None
    title: str
    deadline: date  # a day in the user's time zone, already filled (#6)
    tasks: list[DraftTaskResponse]


class DomainNumbersResponse(BaseModel):
    domain: str
    estimated_minutes: int
    adjusted_minutes: int


class NumbersResponse(BaseModel):
    by_domain: list[DomainNumbersResponse]
    total_estimated_minutes: int
    total_adjusted_minutes: int
    days_left: int | None
    adjusted_is_hint: bool


class ProjectDraftResponse(BaseModel):
    message_id: UUID  # the message to send to /apply
    milestones: list[DraftMilestoneResponse]
    reminders: list[str]  # Fixed things: the Frontend offers "create a fixed task"
    numbers: NumbersResponse
    warnings: list[str]


class MessageResponse(BaseModel):
    id: UUID
    role: ChatRole
    kind: ReplyKind | None = None  # AI messages only
    content: str
    created_at: datetime  # ISO 8601 in the user's time zone
    draft: ProjectDraftResponse | None = None
    attachments: list[AttachmentResponse] = []  # user messages only (FR-08b-2)


class ProjectChatTurnResponse(BaseModel):
    conversation_id: UUID
    user_message: MessageResponse
    ai_message: MessageResponse


class ProjectChatResponse(BaseModel):
    conversation_id: UUID
    project_id: UUID
    status: ConversationStatus
    messages: list[MessageResponse]
    latest_draft: ProjectDraftResponse | None
    applied: bool


class EditedTask(BaseModel):
    key: str | None = None  # from the draft; empty for a task the user added
    title: str
    domain_id: UUID
    estimated_minutes: int


class EditedMilestone(BaseModel):
    key: str | None = None
    title: str
    deadline: date | None = None  # empty → the project deadline
    tasks: list[EditedTask]


class ApplyRequest(BaseModel):
    message_id: UUID
    milestones: list[EditedMilestone] | None = None  # edited by hand (#21)


class AppliedTaskResponse(BaseModel):
    id: UUID
    title: str
    estimated_minutes: int
    estimate_source: EstimateSource


class AppliedMilestoneResponse(BaseModel):
    id: UUID
    title: str
    deadline: date
    tasks: list[AppliedTaskResponse]


class SkippedResponse(BaseModel):
    title: str
    reason: DuplicateOf


class AppliedResponse(BaseModel):
    milestones: list[AppliedMilestoneResponse]
    skipped: list[SkippedResponse]


def to_conversation(conversation: ProjectConversation) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=conversation.conversation_id,
        title=conversation.title,
        status=conversation.status,
        created_at=conversation.created_at,
    )


def to_draft(message: ProjectChatMessage) -> ProjectDraftResponse | None:
    saved = message.draft
    if saved is None:
        return None
    draft, numbers = saved.draft, saved.numbers
    return ProjectDraftResponse(
        message_id=message.message_id,
        milestones=[
            DraftMilestoneResponse(
                key=m.key,
                title=m.title,
                deadline=m.deadline,
                tasks=[
                    DraftTaskResponse(
                        key=t.key,
                        title=t.title,
                        domain=DomainResponse(id=t.domain_id, name=t.domain_name),
                        estimated_minutes=t.estimated_minutes,
                        reason=t.reason,
                    )
                    for t in m.tasks
                ],
            )
            for m in draft.milestones
        ],
        reminders=list(draft.reminders),
        numbers=NumbersResponse(
            by_domain=[
                DomainNumbersResponse(
                    domain=d.domain,
                    estimated_minutes=d.estimated_minutes,
                    adjusted_minutes=d.adjusted_minutes,
                )
                for d in numbers.by_domain
            ],
            total_estimated_minutes=numbers.total_estimated_minutes,
            total_adjusted_minutes=numbers.total_adjusted_minutes,
            days_left=numbers.days_left,
            adjusted_is_hint=numbers.adjusted_is_hint,
        ),
        warnings=list(draft.warnings),  # the overload warning is in here too
    )


def to_attachment(attachment: ChatAttachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=attachment.attachment_id,
        name=attachment.name,
        source=attachment.source,
        document_id=attachment.document_id,
        is_truncated=attachment.is_truncated,
    )


def to_message(
    message: ProjectChatMessage,
    zone: ZoneInfo,
    attachments: list[ChatAttachment] | tuple[ChatAttachment, ...] = (),
) -> MessageResponse:
    kind = None
    if message.role is ChatRole.ASSISTANT:
        kind = ReplyKind.DRAFT if message.draft else ReplyKind.QUESTION
    return MessageResponse(
        id=message.message_id,
        role=message.role,
        kind=kind,
        content=message.content,
        created_at=message.created_at.astimezone(zone),
        draft=to_draft(message),
        attachments=[to_attachment(a) for a in attachments],
    )


def to_inputs(milestones: list[EditedMilestone]) -> list[MilestoneInput]:
    return [
        MilestoneInput(
            title=m.title,
            deadline=m.deadline,
            tasks=[
                TaskInput(t.title, t.domain_id, t.estimated_minutes, key=t.key)
                for t in m.tasks
            ],
            key=m.key,
        )
        for m in milestones
    ]


def unprocessable(error: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
    )


# --- endpoints ---


@router.post(
    "/projects/{project_id}/conversations", status_code=status.HTTP_201_CREATED
)
async def start_chat(
    project_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[StartProjectChat, Depends(get_start_project_chat)],
) -> ConversationResponse:
    """Open a new chat for this project. No AI call."""
    try:
        conversation = await use_case.execute(user_id, project_id)
    except ProjectNotFoundError as error:
        raise not_found("Project") from error
    except ProjectClosedError as error:
        raise conflict("project_closed") from error
    return to_conversation(conversation)


@router.get("/projects/{project_id}/conversations")
async def list_chats(
    project_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[ListProjectChats, Depends(get_list_project_chats)],
) -> list[ConversationResponse]:
    """The chats of this project, newest first. No AI call."""
    try:
        conversations = await use_case.execute(user_id, project_id)
    except ProjectNotFoundError as error:
        raise not_found("Project") from error
    return [to_conversation(c) for c in conversations]


def document_problem(code: DocumentErrorCode, file: str) -> HTTPException:
    """422 for a file that cannot be used (AC3), with its name."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": str(code), "file": file},
    )


async def send(
    use_case: SendProjectChatMessage,
    user_id: UUID,
    conversation_id: UUID,
    message: str,
    attached: AttachedDocuments,
) -> ProjectChatTurnResponse:
    """Run the use case and turn its errors into HTTP answers (both endpoints)."""
    try:
        turn = await use_case.execute(user_id, conversation_id, message, attached)
    except DocumentError as error:
        raise document_problem(error.code, error.name) from error
    except DocumentNotFoundError as error:
        raise not_found("Document") from error
    except DocumentAccessOffError as error:
        raise conflict("document_access_off") from error
    except DocumentFetchError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "document_fetch_failed"},
        ) from error
    except InvalidMessageError as error:
        raise unprocessable(error) from error
    except (ConversationNotFoundError, ProjectNotFoundError) as error:
        raise not_found("Conversation") from error
    except ConversationAppliedError as error:
        raise conflict("already_applied") from error
    except ProjectClosedError as error:
        raise conflict("project_closed") from error
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
    return to_turn(turn)


def to_turn(turn: ProjectChatTurn) -> ProjectChatTurnResponse:
    return ProjectChatTurnResponse(
        conversation_id=turn.conversation_id,
        user_message=to_message(turn.user_message, turn.time_zone, turn.attachments),
        ai_message=to_message(turn.ai_message, turn.time_zone),
    )


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID,
    body: SendRequest,
    request: Request,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[SendProjectChatMessage, Depends(get_send_project_chat_message)],
) -> ProjectChatTurnResponse:
    """Send one message (usually the brief), with up to 3 library documents.
    Uses 1 AI call when the AI answers."""
    attached = AttachedDocuments(
        document_ids=tuple(body.document_ids),
        auth_header=request.headers.get("Authorization", ""),  # #18: passed on only
    )
    return await send(use_case, user_id, conversation_id, body.message, attached)


@router.post("/conversations/{conversation_id}/messages/files")
async def send_message_with_files(
    conversation_id: UUID,
    request: Request,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[SendProjectChatMessage, Depends(get_send_project_chat_message)],
    message: Annotated[str, Form()] = "",
    document_ids: Annotated[list[UUID], Form()] = [],  # noqa: B006 (FastAPI form)
    files: Annotated[list[UploadFile], File()] = [],  # noqa: B006 (FastAPI form)
) -> ProjectChatTurnResponse:
    """Like /messages, plus files uploaded straight into the chat (FR-08b-2).
    Uploads do not need the project's document switch (AC1)."""
    limits = use_case.limits
    if len(document_ids) + len(files) > limits.max_files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"at most {limits.max_files} documents per message",
        )
    uploads = []
    for upload in files:
        name = upload.filename or "file"
        data = await upload.read(limits.max_file_bytes + 1)  # stop just past the limit
        if len(data) > limits.max_file_bytes:
            raise document_problem(DocumentErrorCode.TOO_LARGE, name)
        uploads.append(UploadedFile(name, data))
    attached = AttachedDocuments(
        document_ids=tuple(document_ids),
        uploads=tuple(uploads),
        auth_header=request.headers.get("Authorization", ""),
    )
    return await send(use_case, user_id, conversation_id, message, attached)


@router.get("/conversations/{conversation_id}")
async def get_chat(
    conversation_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GetProjectChat, Depends(get_get_project_chat)],
) -> ProjectChatResponse:
    """One project chat with its messages and newest draft. No AI call."""
    try:
        view = await use_case.execute(user_id, conversation_id)
    except ConversationNotFoundError as error:
        raise not_found("Conversation") from error
    by_message: dict[UUID, list[ChatAttachment]] = {}
    for attachment in view.attachments:
        by_message.setdefault(attachment.message_id, []).append(attachment)
    return ProjectChatResponse(
        conversation_id=view.conversation.conversation_id,
        project_id=view.conversation.project_id,
        status=view.conversation.status,
        messages=[
            to_message(m, view.time_zone, by_message.get(m.message_id, []))
            for m in view.messages
        ],
        latest_draft=to_draft(view.latest_draft) if view.latest_draft else None,
        applied=view.applied,
    )


@router.post(
    "/conversations/{conversation_id}/apply", status_code=status.HTTP_201_CREATED
)
async def apply(
    conversation_id: UUID,
    body: ApplyRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[ApplyProjectBreakdown, Depends(get_apply_project_breakdown)],
) -> AppliedResponse:
    """Create the milestones + tasks of the newest draft (or the edited plan). No AI call."""
    edited = to_inputs(body.milestones) if body.milestones is not None else None
    try:
        result = await use_case.execute(
            user_id, conversation_id, body.message_id, edited
        )
    except BadDraftError as error:
        raise unprocessable(error) from error
    except (ConversationNotFoundError, ProjectNotFoundError) as error:
        raise not_found("Conversation") from error
    except MessageNotFoundError as error:
        raise not_found("Message") from error
    except NotLatestDraftError as error:
        raise conflict("not_latest_draft") from error
    except ConversationAppliedError as error:
        raise conflict("already_applied") from error
    except ProjectClosedError as error:
        raise conflict("project_closed") from error
    except DomainInactiveError as error:
        raise conflict("domain_inactive") from error
    return AppliedResponse(
        milestones=[
            AppliedMilestoneResponse(
                id=m.milestone_id,
                title=m.title,
                deadline=m.deadline,
                tasks=[
                    AppliedTaskResponse(
                        id=t.task_id,
                        title=t.title,
                        estimated_minutes=t.estimated_minutes,
                        estimate_source=t.estimate_source,
                    )
                    for t in m.tasks
                ],
            )
            for m in result.milestones
        ],
        skipped=[
            SkippedResponse(title=s.title, reason=s.reason) for s in result.skipped
        ],
    )
