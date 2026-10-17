"""SqlBriefingRepository: SQL shape and row conversions.

A stand-in session records each statement and returns prepared rows. The SQL
itself is checked against the real DB with EXPLAIN (step 8).
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.briefing_repository import (
    BriefingRepository,
    DomainK,
)
from metanoia_ai.application.ports.schedule_repository import TaskStatus
from metanoia_ai.domain.briefing import OpenSubtask, SlotStatus
from metanoia_ai.infrastructure.db.briefing_repository import (
    GET_CALENDAR_EVENTS,
    GET_CONNECTION_STATUS,
    GET_FIXED_TASKS,
    GET_HOURS,
    GET_K_BIAS,
    GET_SESSIONS,
    GET_SLOTS,
    GET_SUBTASKS,
    GET_TASKS,
    GET_USER,
    SLOT_STATUS_FROM_DB,
    SqlBriefingRepository,
)

USER = uuid4()
NOW = datetime(2026, 10, 17, 1, 0, tzinfo=UTC)
DAY_START, DAY_END = NOW - timedelta(days=1), NOW + timedelta(days=1)
WINDOW_END = NOW + timedelta(days=14)


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
    """Returns prepared rows per SQL statement; records (sql, params) and transactions."""

    def __init__(self, rows_by_statement=None):
        self.rows = rows_by_statement or {}
        self.calls = []
        self.events = []

    def in_transaction(self):
        return False

    @asynccontextmanager
    async def begin(self):
        self.events.append("begin")
        yield
        self.events.append("commit")

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return Result(self.rows.get(str(statement), []))


def sql(statement) -> str:
    return " ".join(str(statement).split())


def load(session):
    repo = SqlBriefingRepository(session)
    return asyncio.run(repo.get_briefing_data(USER, DAY_START, DAY_END, WINDOW_END))


USER_ROW = SimpleNamespace(
    TimeZone="Asia/Ho_Chi_Minh",
    WorkDayStart=time(8),
    WorkDayEnd=time(18),
    WorksOnWeekend=False,
    DefaultBufferMinutes=15,
    ProductiveBlocks=None,
    PlanPrice=79000,
)


# --- the SQL (AC7: every read is limited to the user) ---


@pytest.mark.parametrize(
    "statement",
    [GET_TASKS, GET_SUBTASKS, GET_SESSIONS, GET_SLOTS, GET_FIXED_TASKS],
)
def test_task_queries_filter_by_project_owner(statement):
    text = sql(statement)
    assert 'JOIN "Project" p ON p."Id" = t."ProjectId"' in text
    assert 'p."UserId" = :user_id' in text


@pytest.mark.parametrize("statement", [GET_K_BIAS, GET_HOURS])
def test_profile_queries_filter_by_user(statement):
    assert '"UserId" = :user_id' in sql(statement)


def test_events_filter_by_connection_owner():
    text = sql(GET_CALENDAR_EVENTS)
    assert 'JOIN "GoogleConnection" c ON c."Id" = e."GoogleConnectionId"' in text
    assert 'c."UserId" = :user_id' in text


def test_user_query_skips_deleted_users_and_reads_the_active_plan():
    text = sql(GET_USER)
    assert '"DeletedAt" IS NULL' in text
    assert "us.\"Status\" = 'Active'" in text


def test_candidate_filter():
    text = sql(GET_TASKS)
    assert "t.\"TaskType\" = 'Flexible'" in text
    assert "t.\"Status\" IN ('ToDo', 'Scheduled', 'InProgress')" in text
    assert "p.\"Status\" NOT IN ('Completed', 'Archived')" in text


def test_slots_of_today_only_shown_states():
    text = sql(GET_SLOTS)
    assert "s.\"Status\" IN ('Proposed', 'Approved', 'Done')" in text
    assert 's."EndTime" > :day_start AND s."StartTime" < :day_end' in text
    assert 't."Title" || \': \' || st."Title"' in text


def test_slot_states_match_the_check_constraint():
    assert set(SLOT_STATUS_FROM_DB.values()) == set(SlotStatus)


# --- conversions ---


def test_missing_user_reads_nothing_more():
    session = RecordingSession()
    assert load(session) is None
    assert len(session.calls) == 2  # SET TRANSACTION READ ONLY + GET_USER


def test_reads_run_in_one_read_only_transaction():
    session = RecordingSession({str(GET_USER): [USER_ROW]})
    load(session)
    assert session.events == ["begin", "commit"]
    assert session.calls[0][0] == "SET TRANSACTION READ ONLY"


def test_maps_rows():
    task_id, sub_open, sub_done, domain = uuid4(), uuid4(), uuid4(), uuid4()
    started = NOW - timedelta(hours=2)
    rows = {
        str(GET_USER): [USER_ROW],
        str(GET_TASKS): [
            SimpleNamespace(
                Id=task_id,
                Title="Landing page",
                ProjectName="Website",
                DomainId=domain,
                DomainName="Coding",
                Status="InProgress",
                Deadline=NOW + timedelta(days=2),
                EstimatedMinutes=120,
            )
        ],
        str(GET_SUBTASKS): [
            SimpleNamespace(
                Id=sub_open, TaskId=task_id, EstimatedMinutes=30, Status="ToDo"
            ),
            SimpleNamespace(
                Id=sub_done, TaskId=task_id, EstimatedMinutes=40, Status="Done"
            ),
        ],
        str(GET_SESSIONS): [
            SimpleNamespace(
                TaskId=task_id,
                SubTaskId=sub_open,
                StartedAt=started,
                EndedAt=None,
                CaptureMethod="Timer",
                IsConfirmed=True,
                RemainingMinutes=None,
            )
        ],
        str(GET_K_BIAS): [
            SimpleNamespace(DomainId=domain, KBias=Decimal("1.400"), SampleCount=5)
        ],
        str(GET_HOURS): [
            SimpleNamespace(HourOfDay=14, Score=Decimal("0.800"), Source="Learned")
        ],
        str(GET_SLOTS): [
            SimpleNamespace(
                TaskId=task_id,
                Title="Landing page: Header",
                StartTime=NOW,
                EndTime=NOW + timedelta(hours=1),
                Status="Approved",
            )
        ],
        str(GET_FIXED_TASKS): [
            SimpleNamespace(
                Id=uuid4(),
                Title="Họp",
                FixedStart=NOW,
                FixedEnd=NOW + timedelta(hours=1),
                Status="Skipped",
            )
        ],
        str(GET_CALENDAR_EVENTS): [
            SimpleNamespace(
                Title="Nghỉ lễ",
                StartTime=NOW,
                EndTime=NOW + timedelta(days=1),
                IsAllDay=True,
                IsCancelled=False,
                IsCreatedByMetanoia=False,
            )
        ],
    }
    data = load(RecordingSession(rows))

    assert data.hours.time_zone == ZoneInfo("Asia/Ho_Chi_Minh")
    assert data.is_free_plan is False
    [task] = data.tasks
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.has_subtasks is True
    assert task.open_subtasks == (OpenSubtask(sub_open, 30),)
    assert task.sessions[0].subtask_id == sub_open
    assert data.k_by_domain == {domain: DomainK(Decimal("1.400"), 5)}
    assert data.productive_scores == {14: Decimal("0.800")}
    assert data.has_productive_answer is True  # a Learned hour
    assert data.slots[0].status is SlotStatus.APPROVED
    assert data.slots[0].title == "Landing page: Header"
    assert data.fixed_tasks[0].is_skipped is True
    assert data.events[0].is_all_day is True
    assert data.calendar_connected is False  # no connection row


def test_params_carry_the_user_and_the_windows():
    session = RecordingSession({str(GET_USER): [USER_ROW]})
    load(session)
    params = {statement: p for statement, p in session.calls}
    assert params[str(GET_SLOTS)] == {
        "user_id": USER,
        "day_start": DAY_START,
        "day_end": DAY_END,
    }
    assert params[str(GET_FIXED_TASKS)] == {
        "user_id": USER,
        "day_start": DAY_START,
        "window_end": WINDOW_END,
    }
    assert all(p is None or p["user_id"] == USER for _, p in session.calls[1:])


@pytest.mark.parametrize(("price", "free"), [(None, True), (0, True), (79000, False)])
def test_no_active_plan_counts_as_free(price, free):
    row = SimpleNamespace(**{**vars(USER_ROW), "PlanPrice": price})
    assert load(RecordingSession({str(GET_USER): [row]})).is_free_plan is free


@pytest.mark.parametrize(
    ("blocks", "expected"), [(None, False), ([], False), (["evening"], True)]
)
def test_productive_answer_from_onboarding_blocks(blocks, expected):
    row = SimpleNamespace(**{**vars(USER_ROW), "ProductiveBlocks": blocks})
    data = load(RecordingSession({str(GET_USER): [row]}))
    assert data.has_productive_answer is expected


def test_active_connection_counts_as_connected():
    session = RecordingSession(
        {str(GET_USER): [USER_ROW], str(GET_CONNECTION_STATUS): ["Active"]}
    )
    assert load(session).calendar_connected is True


def test_repository_matches_the_port():
    repo: BriefingRepository = SqlBriefingRepository(session=None)
    assert repo is not None
