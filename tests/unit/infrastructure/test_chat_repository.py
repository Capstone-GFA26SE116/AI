"""SqlChatRepository conversions only (T26). The SQL itself is checked with EXPLAIN (step 9)."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from metanoia_ai.application.ports.chat_repository import (
    ChatRepository,
    ChatRole,
    SavedDraft,
)
from metanoia_ai.domain.similar_tasks import SimilarSummary
from metanoia_ai.domain.task_breakdown import Draft, DraftNumbers, Subtask
from metanoia_ai.infrastructure.db.chat_repository import (
    ROLE_FROM_DB,
    ROLE_TO_DB,
    SqlChatRepository,
    draft_from_json,
    draft_to_json,
    to_message,
)

SAVED = SavedDraft(
    draft=Draft(
        (
            Subtask("Dựng khung trang", 60, 1, "làm trước để có chỗ gắn"),
            Subtask("Nối API", 90, 2, None),
        )
    ),
    numbers=DraftNumbers(240, 312, 150, True, "Tổng các bước là 150 phút…"),
    similar=SimilarSummary(3, 2, 1.4),
)


# --- T26: Sender ---


def test_t26_roles_match_the_db_check():
    assert ROLE_TO_DB == {ChatRole.USER: "User", ChatRole.ASSISTANT: "AI"}
    assert ROLE_FROM_DB["AI"] is ChatRole.ASSISTANT


# --- T26: ProposalJson ---


def test_t26_draft_round_trip():
    assert draft_from_json(draft_to_json(SAVED)) == SAVED


def test_draft_without_similar_round_trip():
    no_similar = SavedDraft(SAVED.draft, SAVED.numbers, None)
    assert draft_from_json(draft_to_json(no_similar)) == no_similar


def test_proposal_json_shape_is_versioned_and_readable():
    data = json.loads(draft_to_json(SAVED))
    assert data["version"] == 1
    assert data["subtasks"][0] == {
        "title": "Dựng khung trang",
        "estimated_minutes": 60,
        "order_index": 1,
        "reason": "làm trước để có chỗ gắn",
    }
    assert data["numbers"]["adjusted_minutes"] == 312
    assert "Dựng khung" in draft_to_json(SAVED)  # not escaped: readable in the DB


def test_t26_row_becomes_message():
    created = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
    row = SimpleNamespace(
        Id=uuid4(),
        Sender="AI",
        Content="Mình chia 2 bước.",
        ProposalJson=draft_to_json(SAVED),
        IsApplied=False,
        CreatedAt=created,
    )
    message = to_message(row)
    assert message.role is ChatRole.ASSISTANT
    assert message.draft == SAVED
    assert message.created_at == created


def test_user_row_has_no_draft():
    row = SimpleNamespace(
        Id=uuid4(),
        Sender="User",
        Content="chia giúp",
        ProposalJson=None,
        IsApplied=False,
        CreatedAt=datetime(2026, 10, 15, tzinfo=UTC),
    )
    assert to_message(row).draft is None


def test_unknown_sender_is_an_error():
    row = SimpleNamespace(
        Id=uuid4(),
        Sender="Bot",
        Content="x",
        ProposalJson=None,
        IsApplied=False,
        CreatedAt=datetime(2026, 10, 15, tzinfo=UTC),
    )
    with pytest.raises(KeyError):
        to_message(row)


def test_repository_matches_the_port():
    repo: ChatRepository = SqlChatRepository(session=None)  # type check only
    assert repo is not None
