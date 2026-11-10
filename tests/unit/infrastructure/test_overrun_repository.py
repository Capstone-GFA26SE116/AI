"""SqlOverrunRepository: SQL shape and row conversions (stand-in session, no DB). T7."""

import asyncio
from datetime import datetime, time
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.overrun_repository import OverrunRepository
from metanoia_ai.domain.overrun_plan import SlotState
from metanoia_ai.infrastructure.db.overrun_repository import (
    GET_SLOT,
    GET_SLOTS,
    STATE_FROM_DB,
    SqlOverrunRepository,
)
from metanoia_ai.infrastructure.db.schedule_repository import (
    GET_CALENDAR_EVENTS,
    GET_FIXED_TASKS,
    GET_USER,
)

USER = UUID(int=1)
SLOT = UUID(int=10)
VN = ZoneInfo("Asia/Ho_Chi_Minh")
START = datetime(2026, 11, 9, 10, tzinfo=VN)
END = datetime(2026, 11, 17, 10, tzinfo=VN)
USER_ROW = SimpleNamespace(
    TimeZone="Asia/Ho_Chi_Minh",
    WorkDayStart=time(8),
    WorkDayEnd=time(18),
    WorksOnWeekend=False,
    DefaultBufferMinutes=15,
)


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

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return Result(self.rows.get(str(statement), []))


def sql(statement) -> str:
    return " ".join(str(statement).split())


def slot(n: int, status: str, task_type: str = "Flexible", started: bool = False):
    return SimpleNamespace(
        Id=UUID(int=n),
        StartTime=START,
        EndTime=END,
        Status=status,
        Title=f"Việc {n}",
        TaskType=task_type,
        Deadline=END,
        Started=started,
    )


def run(session):
    repo = SqlOverrunRepository(session)
    return asyncio.run(repo.get_overrun_data(USER, SLOT, START, END))


def test_matches_the_port() -> None:
    repo: OverrunRepository = SqlOverrunRepository(RecordingSession())
    assert repo is not None


def test_every_query_is_limited_to_the_user_and_only_reads() -> None:
    for statement in (GET_SLOT, GET_SLOTS):
        text = sql(statement)
        assert '"UserId" = :user_id' in text
        assert text.startswith("SELECT")
        for word in ("INSERT", "UPDATE", "DELETE"):
            assert word not in text.upper()
    assert 'fs."ScheduleSlotId" = s."Id"' in sql(GET_SLOTS)  # "started" (#6)
    assert ":slot_id" in sql(GET_SLOT)


def test_every_db_status_is_known() -> None:
    # The CHECK constraint read on 2026-11-10 (plan step 0).
    assert set(STATE_FROM_DB) == {
        "Proposed",
        "Approved",
        "Due",
        "InProgress",
        "Done",
        "Skipped",
        "Missed",
        "Overrun",
        "Postponed",
        "Cancelled",
    }


def test_unknown_user_is_none() -> None:
    session = RecordingSession()

    assert run(session) is None
    assert len(session.calls) == 1  # nothing else is read


def test_rows_become_slot_rows() -> None:
    session = RecordingSession(
        {
            str(GET_USER): [USER_ROW],
            str(GET_SLOT): [slot(10, "Overrun", started=True)],
            str(GET_SLOTS): [slot(1, "Due"), slot(2, "Approved", "Fixed")],
            str(GET_FIXED_TASKS): [
                SimpleNamespace(FixedStart=START, FixedEnd=END, Status="Skipped")
            ],
            str(GET_CALENDAR_EVENTS): [
                SimpleNamespace(
                    StartTime=START,
                    EndTime=END,
                    IsAllDay=False,
                    IsCancelled=False,
                    IsCreatedByMetanoia=True,
                )
            ],
        }
    )

    data = run(session)

    assert data.hours.time_zone == VN and data.hours.buffer_minutes == 15
    assert (data.late.state, data.late.started) == (SlotState.OVERRUN, True)
    assert [(s.state, s.is_fixed, s.deadline) for s in data.slots] == [
        (SlotState.DUE, False, END),
        (SlotState.APPROVED, True, None),
    ]
    assert data.fixed_tasks[0].is_skipped
    assert data.calendar_events[0].is_created_by_metanoia
    params = dict(session.calls)
    assert params[str(GET_SLOT)] == {"user_id": USER, "slot_id": SLOT}
    assert params[str(GET_SLOTS)] == {
        "user_id": USER,
        "window_start": START,
        "window_end": END,
    }


def test_slot_of_someone_else_is_none() -> None:
    session = RecordingSession({str(GET_USER): [USER_ROW]})

    assert run(session).late is None


@pytest.mark.parametrize("status", ["Unknown"])
def test_a_new_db_status_fails_loudly(status: str) -> None:
    session = RecordingSession(
        {str(GET_USER): [USER_ROW], str(GET_SLOT): [slot(10, status)]}
    )

    with pytest.raises(KeyError):
        run(session)
