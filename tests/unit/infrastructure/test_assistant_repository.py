"""SqlAssistantRepository: SQL shape and row conversions (stand-in session, no DB)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.assistant_repository import AssistantRepository
from metanoia_ai.domain.briefing import SlotStatus
from metanoia_ai.domain.name_matching import NamedItem
from metanoia_ai.infrastructure.db.assistant_repository import (
    GET_OPEN_TASKS,
    GET_PROJECTS,
    GET_TIME_ZONE,
    SqlAssistantRepository,
)
from metanoia_ai.infrastructure.db.briefing_repository import (
    GET_CALENDAR_EVENTS,
    GET_FIXED_TASKS,
    GET_SLOTS,
)

USER = UUID(int=1)
VN = ZoneInfo("Asia/Ho_Chi_Minh")
START = datetime(2026, 10, 30, 12, tzinfo=VN)
END = datetime(2026, 10, 30, 18, tzinfo=VN)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class RecordingSession:
    def __init__(self, rows_by_statement=None):
        self.rows = rows_by_statement or {}
        self.calls = []

    def in_transaction(self):
        return False

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return Result(self.rows.get(str(statement), []))


def sql(statement) -> str:
    return " ".join(str(statement).split())


def run(session, method: str, *args):
    return asyncio.run(getattr(SqlAssistantRepository(session), method)(*args))


def test_matches_the_port() -> None:
    repo: AssistantRepository = SqlAssistantRepository(RecordingSession())
    assert repo is not None


# --- the SQL (AC7: only the token's user) ---


def test_open_tasks_query() -> None:
    text = sql(GET_OPEN_TASKS)
    assert 'JOIN "Project" p ON p."Id" = t."ProjectId"' in text
    assert 'p."UserId" = :user_id' in text
    assert "t.\"Status\" IN ('ToDo', 'Scheduled', 'InProgress')" in text
    assert "t.\"TaskType\" = 'Flexible'" in text


def test_projects_and_user_queries() -> None:
    assert 'p."UserId" = :user_id' in sql(GET_PROJECTS)
    assert "'Archived'" in sql(GET_PROJECTS)
    assert '"DeletedAt" IS NULL' in sql(GET_TIME_ZONE)


# --- conversions ---


def test_time_zone() -> None:
    session = RecordingSession(
        {str(GET_TIME_ZONE): [SimpleNamespace(TimeZone="Asia/Ho_Chi_Minh")]}
    )

    assert run(session, "get_time_zone", USER) == VN
    assert session.calls[0] == ("SET TRANSACTION READ ONLY", None)
    assert run(RecordingSession(), "get_time_zone", USER) is None


@pytest.mark.parametrize(
    ("method", "statement"),
    [("open_tasks", GET_OPEN_TASKS), ("projects", GET_PROJECTS)],
)
def test_names(method, statement) -> None:
    session = RecordingSession({str(statement): [(UUID(int=5), "Viết blog")]})

    assert run(session, method, USER) == [NamedItem(UUID(int=5), "Viết blog")]
    assert session.calls[1][1] == {"user_id": USER}


def test_schedule_reads_the_fr12_statements_for_the_range() -> None:
    slot = SimpleNamespace(
        TaskId=UUID(int=2),
        Title="Viết blog",
        StartTime=START,
        EndTime=END,
        Status="Approved",
    )
    fixed = SimpleNamespace(
        Id=UUID(int=3), Title="Họp", FixedStart=START, FixedEnd=END, Status="Skipped"
    )
    event = SimpleNamespace(
        Title="Sinh nhật",
        StartTime=START,
        EndTime=END,
        IsAllDay=False,
        IsCancelled=False,
        IsCreatedByMetanoia=False,
    )
    session = RecordingSession(
        {
            str(GET_SLOTS): [slot],
            str(GET_FIXED_TASKS): [fixed],
            str(GET_CALENDAR_EVENTS): [event],
        }
    )

    rows = run(session, "schedule", USER, START, END)

    assert rows.slots[0].status is SlotStatus.APPROVED
    assert rows.fixed_tasks[0].is_skipped is True
    assert rows.events[0].title == "Sinh nhật"
    params = {s: p for s, p in session.calls[1:]}
    assert params[str(GET_SLOTS)] == {
        "user_id": USER,
        "day_start": START,
        "day_end": END,
    }
    assert params[str(GET_FIXED_TASKS)] == {
        "user_id": USER,
        "day_start": START,
        "window_end": END,
    }


# --- FR-22c (T6) ---


def test_startable_slot_sql() -> None:
    from metanoia_ai.infrastructure.db.assistant_repository import GET_STARTABLE_SLOT

    text = sql(GET_STARTABLE_SLOT)
    assert 'p."UserId" = :user_id' in text
    assert 's."TaskId" = :task_id' in text
    assert "s.\"Status\" IN ('Approved', 'Due')" in text
    assert 's."EndTime" > :now AND s."StartTime" <= :latest_start' in text
    assert text.endswith('ORDER BY s."StartTime" LIMIT 1')
