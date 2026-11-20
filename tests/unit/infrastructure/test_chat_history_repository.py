"""SqlChatHistoryRepository (plans/chat-history.md T4). The SQL runs on the real DB in step 5."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from metanoia_ai.application.ports.chat_history_repository import (
    ChatHistoryRepository,
    ChatScope,
)
from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.project_chat_repository import ConversationStatus
from metanoia_ai.infrastructure.db import chat_history_repository as sql
from metanoia_ai.infrastructure.db.chat_history_repository import (
    SqlChatHistoryRepository,
    like_pattern,
    to_summary,
)

NOW = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)


def flat(statement) -> str:
    return " ".join(str(statement).split())


def row(**kw):
    base = SimpleNamespace(
        Id=uuid4(),
        Scope="Task",
        Title="Chia nhỏ: Logo",
        Status="Applied",
        ProjectId=uuid4(),
        ProjectName="Quán cà phê",
        TaskId=uuid4(),
        TaskTitle="Logo",
        LastSender="AI",
        LastContent="Xong",
        LastAt=NOW,
        Count=4,
        Applied=True,
        MatchId=None,
        MatchContent=None,
        MatchAt=None,
    )
    return SimpleNamespace(**{**vars(base), **kw})


class RecordingSession:
    def __init__(self, rows=(), zone="Asia/Ho_Chi_Minh"):
        self.rows, self.zone = list(rows), zone
        self.calls = []

    async def execute(self, statement, params):
        self.calls.append((statement, params))
        return SimpleNamespace(
            all=lambda: self.rows, scalar_one_or_none=lambda: self.zone
        )


def test_is_a_chat_history_repository():
    repo: ChatHistoryRepository = SqlChatHistoryRepository(RecordingSession())
    assert repo is not None


def test_only_the_users_chats_newest_last_message_first():
    text = flat(sql.LIST_CHATS)
    assert 'WHERE p."UserId" = :user_id' in text
    assert 'JOIN LATERAL ( SELECT m."Sender"' in text  # inner join: no message, no row
    assert text.endswith(
        'ORDER BY last."CreatedAt" DESC, c."Id" DESC LIMIT :limit OFFSET :offset'
    )
    assert ":pattern" not in text


def test_applied_comes_from_the_flag_or_the_draft_time():
    text = flat(sql.LIST_CHATS)
    assert 'm."IsApplied" OR m."ProposalJson" ->> \'applied_at\' IS NOT NULL' in text


def test_search_matches_messages_and_names():
    text = flat(sql.SEARCH_CHATS)
    assert 'm."Content" ILIKE :pattern' in text
    for name in ('c."ProjectName"', 'c."TaskTitle"', 'c."Title"'):
        assert f"{name} ILIKE :pattern" in text


def test_typed_wildcards_are_plain_characters():
    assert like_pattern("blog") == "%blog%"
    assert like_pattern("50%_a\\b") == "%50\\%\\_a\\\\b%"


def test_search_uses_the_pattern_and_listing_does_not():
    session = RecordingSession()
    repo = SqlChatHistoryRepository(session)
    asyncio.run(repo.search(uuid4(), None, 21, 0))
    asyncio.run(repo.search(uuid4(), "blog", 21, 20))
    (first, p1), (second, p2) = session.calls
    assert first is sql.LIST_CHATS and "pattern" not in p1
    assert second is sql.SEARCH_CHATS and p2["pattern"] == "%blog%"
    assert (p2["limit"], p2["offset"]) == (21, 20)


def test_row_becomes_a_summary():
    summary = to_summary(row())
    assert summary.scope is ChatScope.TASK
    assert summary.status is ConversationStatus.APPLIED
    assert summary.last_role is ChatRole.ASSISTANT
    assert summary.applied and summary.message_count == 4
    assert summary.match is None


def test_row_with_a_match():
    hit = uuid4()
    summary = to_summary(
        row(
            Scope="Project",
            TaskId=None,
            TaskTitle=None,
            MatchId=hit,
            MatchContent="trang blog",
            MatchAt=NOW,
        )
    )
    assert summary.scope is ChatScope.PROJECT and summary.task_id is None
    assert summary.match.message_id == hit and summary.match.content == "trang blog"


def test_time_zone():
    repo = SqlChatHistoryRepository(RecordingSession())
    assert str(asyncio.run(repo.time_zone(uuid4()))) == "Asia/Ho_Chi_Minh"
    missing = SqlChatHistoryRepository(RecordingSession(zone=None))
    assert asyncio.run(missing.time_zone(uuid4())) is None
