"""Chat history page: the user's chats, newest first, with search (plans/chat-history.md).

No AI call, no quota. Open a chat with GET /chat/conversations/{id} (project) or
GET /chat/tasks/{task_id} (task).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_list_chat_history
from metanoia_ai.application.ports.chat_history_repository import ChatScope
from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.project_chat_repository import ConversationStatus
from metanoia_ai.application.use_cases.list_chat_history import (
    ChatHistoryEntry,
    InvalidHistoryRequestError,
    ListChatHistory,
    UserNotFoundError,
)
from metanoia_ai.domain.chat_history import DEFAULT_PAGE_SIZE

router = APIRouter(prefix="/chat", tags=["chat history"])


class NamedRef(BaseModel):
    id: UUID
    name: str


class LastMessageResponse(BaseModel):
    role: ChatRole
    preview: str  # one line, ≤ 120 characters
    created_at: datetime  # in the user's time zone


class MatchResponse(BaseModel):
    message_id: UUID  # scroll the chat to this message
    snippet: str
    created_at: datetime


class ChatHistoryItem(BaseModel):
    conversation_id: UUID
    scope: ChatScope
    title: str
    status: ConversationStatus
    applied: bool
    project: NamedRef
    task: NamedRef | None  # task chats only
    message_count: int
    last_message: LastMessageResponse
    match: MatchResponse | None  # only when q matched a message


class ChatHistoryResponse(BaseModel):
    items: list[ChatHistoryItem]
    has_more: bool


def to_item(entry: ChatHistoryEntry, zone) -> ChatHistoryItem:
    chat = entry.chat
    task = None
    if chat.task_id is not None:
        task = NamedRef(id=chat.task_id, name=chat.task_title or "")
    match = None
    if chat.match is not None and entry.snippet is not None:
        match = MatchResponse(
            message_id=chat.match.message_id,
            snippet=entry.snippet,
            created_at=chat.match.created_at.astimezone(zone),
        )
    return ChatHistoryItem(
        conversation_id=chat.conversation_id,
        scope=chat.scope,
        title=chat.title,
        status=chat.status,
        applied=chat.applied,
        project=NamedRef(id=chat.project_id, name=chat.project_name),
        task=task,
        message_count=chat.message_count,
        last_message=LastMessageResponse(
            role=chat.last_role,
            preview=entry.preview,
            created_at=chat.last_at.astimezone(zone),
        ),
        match=match,
    )


@router.get("/conversations")
async def chat_history(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[ListChatHistory, Depends(get_list_chat_history)],
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query()] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query()] = 0,
) -> ChatHistoryResponse:
    """All the user's chats (project and task), newest message first.
    q: search messages, project / task names and chat titles (any case)."""
    try:
        page = await use_case.execute(user_id, q, limit, offset)
    except InvalidHistoryRequestError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except UserNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from error
    return ChatHistoryResponse(
        items=[to_item(e, page.time_zone) for e in page.entries],
        has_more=page.has_more,
    )
