"""SqlAnalyticsRepository: SQL shape and row conversions.

A stand-in session records each statement and returns prepared rows. The SQL
itself runs against the real DB in scripts/try_analytics.py (step 8).
"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.analytics_repository import (
    AnalyticsRepository,
    AnalyticsUser,
)
from metanoia_ai.domain.analytics import KUpdate, Period, TaskStatus
from metanoia_ai.domain.models import ActualTimeSource, TaskType
from metanoia_ai.infrastructure.db.analytics_repository import (
    GET_DEADLINE_TASKS,
    GET_DONE_TASKS,
    GET_K_UPDATES,
    GET_PROFILES,
    GET_USER,
    SqlAnalyticsRepository,
    parse_sessions,
)

USER = uuid4()
PROJECT = uuid4()
VN = ZoneInfo("Asia/Ho_Chi_Minh")
END = datetime(2026, 10, 29, 17, 20, tzinfo=VN)
PERIOD = Period(start=datetime(2026, 9, 29, tzinfo=VN), end=END)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return list(self.rows)

    def one_or_none(self):
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


def run(session, method: str, *args):
    return asyncio.run(getattr(SqlAnalyticsRepository(session), method)(*args))


def test_matches_the_port() -> None:
    repo: AnalyticsRepository = SqlAnalyticsRepository(RecordingSession())
    assert repo is not None


# --- the SQL (AC6: every read is limited to the user) ---


@pytest.mark.parametrize("statement", [GET_DEADLINE_TASKS, GET_DONE_TASKS])
def test_task_queries_filter_by_project_owner(statement) -> None:
    text = sql(statement)
    assert 'JOIN "Project" p ON p."Id" = t."ProjectId"' in text
    assert 'p."UserId" = :user_id' in text
    assert "t.\"TaskType\" = 'Flexible'" in text


def test_profile_queries_filter_by_user() -> None:
    assert 'udp."UserId" = :user_id' in sql(GET_PROFILES)
    text = sql(GET_K_UPDATES)
    assert 'JOIN "UserDomainProfile" udp ON udp."Id" = l."UserDomainProfileId"' in text
    assert 'udp."UserId" = :user_id' in text


def test_user_query_skips_deleted_users_and_checks_project_owner() -> None:
    text = sql(GET_USER)
    assert '"DeletedAt" IS NULL' in text
    assert 'p."UserId" = u."Id"' in text


def test_done_tasks_only_done_in_period_with_sessions_in_one_query() -> None:
    text = sql(GET_DONE_TASKS)
    assert "t.\"Status\" = 'Done'" in text
    assert 't."CompletedAt" <= :end' in text
    assert 'WHERE fs."TaskId" = t."Id"' in text  # sessions inside the same query
    assert 'c."UserId" = p."UserId"' in text  # a client of another user is never shown


def test_period_filters_allow_the_all_range() -> None:
    for statement in (GET_DEADLINE_TASKS, GET_K_UPDATES, GET_DONE_TASKS):
        assert "CAST(:start AS timestamptz) IS NULL" in sql(statement)


# --- conversions ---


def test_get_user() -> None:
    session = RecordingSession(
        {
            str(GET_USER): [
                SimpleNamespace(TimeZone="Asia/Ho_Chi_Minh", ProjectFound=True)
            ]
        }
    )

    user = run(session, "get_user", USER, PROJECT)

    assert user == AnalyticsUser(VN, True)
    assert session.calls[1][1] == {"user_id": USER, "project_id": PROJECT}
    assert session.calls[0][0] == "SET TRANSACTION READ ONLY"
    assert run(RecordingSession(), "get_user", USER) is None


def test_get_deadline_tasks_converts_db_strings() -> None:
    row = SimpleNamespace(
        TaskType="Flexible", Status="InProgress", Deadline=END, CompletedAt=None
    )
    session = RecordingSession({str(GET_DEADLINE_TASKS): [row]})

    [task] = run(session, "get_deadline_tasks", USER, PERIOD)

    assert (task.task_type, task.status, task.deadline) == (
        TaskType.FLEXIBLE,
        TaskStatus.IN_PROGRESS,
        END,
    )
    assert session.calls[1][1] == {"user_id": USER, "start": PERIOD.start, "end": END}


def test_get_domain_profiles_attaches_updates() -> None:
    first, second = uuid4(), uuid4()
    profiles = [
        SimpleNamespace(
            Id=first,
            DomainId=uuid4(),
            DomainName="Design",
            KBias=Decimal("1.05"),
            SampleCount=3,
        ),
        SimpleNamespace(
            Id=second,
            DomainId=uuid4(),
            DomainName="Writing",
            KBias=Decimal("1.31"),
            SampleCount=12,
        ),
    ]
    updates = [
        SimpleNamespace(
            UserDomainProfileId=second, CreatedAt=END, NewKBias=Decimal("1.31")
        )
    ]
    session = RecordingSession(
        {str(GET_PROFILES): profiles, str(GET_K_UPDATES): updates}
    )

    result = run(session, "get_domain_profiles", USER, Period(None, END))

    assert [(p.domain_name, p.updates) for p in result] == [
        ("Design", ()),
        ("Writing", (KUpdate(END, Decimal("1.31")),)),
    ]
    assert session.calls[-1][1]["start"] is None


def test_get_done_tasks_parses_sessions() -> None:
    started = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)
    sessions = json.dumps(
        [
            {
                "started_at": started.isoformat(),
                "ended_at": (started + timedelta(minutes=50)).isoformat(),
                "source": "Timer",
                "confirmed": True,
            },
            {
                "started_at": "2026-10-28T09:00:00+07:00",
                "ended_at": None,
                "source": "SelfReported",
                "confirmed": False,
            },
        ]
    )
    row = SimpleNamespace(
        Id=uuid4(),
        Title="Write post",
        ProjectId=PROJECT,
        ProjectName="Blog",
        ClientId=None,
        ClientName=None,
        DomainName="Writing",
        EstimatedMinutes=60,
        AiEstimatedMinutes=None,
        CompletedAt=END,
        Sessions=sessions,
    )
    session = RecordingSession({str(GET_DONE_TASKS): [row]})

    [task] = run(session, "get_done_tasks", USER, PERIOD, PROJECT)

    assert task.project_name == "Blog" and task.client_id is None
    assert [(s.started_at, s.source, s.is_confirmed) for s in task.sessions] == [
        (started, ActualTimeSource.TIMER, True),
        (
            datetime(2026, 10, 28, 2, 0, tzinfo=UTC),
            ActualTimeSource.SELF_REPORTED,
            False,
        ),
    ]
    assert task.sessions[0].ended_at - task.sessions[0].started_at == timedelta(
        minutes=50
    )
    assert task.sessions[1].ended_at is None
    assert session.calls[1][1]["project_id"] == PROJECT


def test_parse_empty_sessions() -> None:
    assert parse_sessions("[]") == ()
