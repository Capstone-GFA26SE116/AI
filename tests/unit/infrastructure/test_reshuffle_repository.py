"""SqlReshuffleRepository: SQL shape and row conversions (stand-in session, no DB). T10."""

import asyncio
from datetime import datetime, time
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.reshuffle_repository import ReshuffleRepository
from metanoia_ai.domain.overrun_plan import SlotState
from metanoia_ai.infrastructure.db.reshuffle_repository import (
    GET_DEFAULT_PROJECT,
    GET_EVENTS,
    GET_FIXED_TASKS,
    GET_RECENT_DOMAIN,
    GET_SLOT,
    GET_SLOTS,
    SqlReshuffleRepository,
)
from metanoia_ai.infrastructure.db.schedule_repository import GET_USER

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
ALL_QUERIES = (
    GET_SLOT,
    GET_SLOTS,
    GET_FIXED_TASKS,
    GET_EVENTS,
    GET_DEFAULT_PROJECT,
    GET_RECENT_DOMAIN,
)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
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


def slot(n: int, status: str, task_type="Flexible", task_status="ToDo", group=False):
    return SimpleNamespace(
        Id=UUID(int=n),
        StartTime=START,
        EndTime=END,
        Status=status,
        Title=f"Việc {n}",
        TaskType=None if group else task_type,
        Deadline=END,
        TaskStatus=None if group else task_status,
        TaskId=None if group else UUID(int=100 + n),
        SubTaskId=None,
        Started=False,
        IsGroup=group,
    )


def run(session, slot_id=SLOT):
    repo = SqlReshuffleRepository(session)
    return asyncio.run(repo.get_calendar(USER, START, END, slot_id))


def test_matches_the_port() -> None:
    repo: ReshuffleRepository = SqlReshuffleRepository(RecordingSession())
    assert repo is not None


def test_every_query_is_limited_to_the_user_and_only_reads() -> None:
    for statement in ALL_QUERIES:
        text = sql(statement)
        assert ":user_id" in text
        assert text.startswith("SELECT")
        for word in ("INSERT", "UPDATE", "DELETE"):
            assert word not in text.upper()
    for statement in (GET_SLOT, GET_SLOTS):
        personal, group = sql(statement).split("UNION ALL")
        assert 'p."UserId" = :user_id' in personal
        assert 's."GroupTaskId" IS NOT NULL AND s."UserId" = :user_id' in group
    assert 'p."ClientId" IS NOT NULL AS "WithClient"' in sql(GET_FIXED_TASKS)
    assert 'c."UserId" = :user_id' in sql(GET_EVENTS)


def test_unknown_user_is_none() -> None:
    session = RecordingSession()

    assert run(session) is None
    assert len(session.calls) == 1


def test_rows_become_calendar_data() -> None:
    session = RecordingSession(
        {
            str(GET_USER): [USER_ROW],
            str(GET_SLOT): [slot(10, "Due")],
            str(GET_SLOTS): [
                slot(1, "Approved", task_status="Done"),
                slot(2, "Approved", "Fixed"),
                slot(3, "Missed", group=True),
            ],
            str(GET_FIXED_TASKS): [
                SimpleNamespace(
                    Id=UUID(int=9),
                    Title="Họp",
                    FixedStart=START,
                    FixedEnd=END,
                    Status="Skipped",
                    WithClient=True,
                )
            ],
            str(GET_EVENTS): [
                SimpleNamespace(
                    Title=None,
                    StartTime=START,
                    EndTime=END,
                    IsAllDay=False,
                    IsCancelled=False,
                    IsCreatedByMetanoia=False,
                )
            ],
            str(GET_DEFAULT_PROJECT): [UUID(int=500)],
            str(GET_RECENT_DOMAIN): [UUID(int=600)],
        }
    )

    data = run(session)

    assert data.hours.time_zone == VN
    assert (data.target.slot_id, data.target.state) == (SLOT, SlotState.DUE)
    assert [
        (s.state, s.is_fixed, s.deadline, s.task_finished, s.is_group, s.task_id)
        for s in data.slots
    ] == [
        (SlotState.APPROVED, False, END, True, False, UUID(int=101)),
        (SlotState.APPROVED, True, None, False, False, UUID(int=102)),
        (SlotState.MISSED, False, END, False, True, None),
    ]
    fixed = data.fixed_tasks[0]
    assert (fixed.task_id, fixed.is_skipped, fixed.with_client) == (
        UUID(int=9),
        True,
        True,
    )
    assert data.events[0].title == ""  # a Google event without a title
    assert (data.default_project_id, data.recent_domain_id) == (
        UUID(int=500),
        UUID(int=600),
    )
    params = dict(session.calls)
    assert params[str(GET_SLOTS)] == {
        "user_id": USER,
        "window_start": START,
        "window_end": END,
    }
    assert params[str(GET_SLOT)] == {"user_id": USER, "slot_id": SLOT}


def test_no_slot_asked_reads_no_target() -> None:
    session = RecordingSession({str(GET_USER): [USER_ROW]})

    data = run(session, slot_id=None)

    assert data.target is None
    assert str(GET_SLOT) not in dict(session.calls)
    assert (data.default_project_id, data.recent_domain_id) == (None, None)


def test_slot_of_someone_else_is_no_target() -> None:
    assert run(RecordingSession({str(GET_USER): [USER_ROW]})).target is None
