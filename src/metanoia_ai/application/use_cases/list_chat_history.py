"""The "Chat history" page: all the user's chats, newest first, with search.

No LLM call, no quota (plans/chat-history.md).
"""

from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.chat_history_repository import (
    ChatHistoryRepository,
    ChatSummary,
)
from metanoia_ai.domain.chat_history import (
    DEFAULT_PAGE_SIZE,
    MAX_OFFSET,
    MAX_PAGE_SIZE,
    clean_query,
    preview,
    snippet,
)


class InvalidHistoryRequestError(ValueError):
    """q too long, or limit / offset out of range."""


class UserNotFoundError(Exception):
    """The token's user has no row (should not happen after the JWT check)."""


@dataclass(frozen=True)
class ChatHistoryEntry:
    chat: ChatSummary
    preview: str  # the last message, one line
    snippet: str | None  # around the match, when searching


@dataclass(frozen=True)
class ChatHistoryPage:
    entries: tuple[ChatHistoryEntry, ...]
    has_more: bool
    time_zone: ZoneInfo  # the user's, for the times in the answer


class ListChatHistory:
    def __init__(self, repo: ChatHistoryRepository) -> None:
        self.repo = repo

    async def execute(
        self,
        user_id: UUID,
        query: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> ChatHistoryPage:
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidHistoryRequestError(f"limit must be 1-{MAX_PAGE_SIZE}")
        if not 0 <= offset <= MAX_OFFSET:
            raise InvalidHistoryRequestError(f"offset must be 0-{MAX_OFFSET}")
        try:
            text = clean_query(query)
        except ValueError as error:
            raise InvalidHistoryRequestError(str(error)) from error

        zone = await self.repo.time_zone(user_id)
        if zone is None:
            raise UserNotFoundError(str(user_id))
        # One row more than asked tells whether there is a next page.
        chats = await self.repo.search(user_id, text, limit + 1, offset)
        return ChatHistoryPage(
            entries=tuple(entry(chat, text) for chat in chats[:limit]),
            has_more=len(chats) > limit,
            time_zone=zone,
        )


def entry(chat: ChatSummary, query: str | None) -> ChatHistoryEntry:
    found = None
    if query and chat.match:
        found = snippet(chat.match.content, query)
    return ChatHistoryEntry(chat, preview(chat.last_content), found)
