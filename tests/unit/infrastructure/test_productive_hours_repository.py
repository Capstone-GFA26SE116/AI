"""SqlProductiveHoursRepository: SQL shape and row conversions (T12).

A stand-in session records each statement and returns prepared rows. The SQL
itself is checked against the real DB with EXPLAIN (step 6).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.productive_hours_repository import (
    ProductiveHoursRepository,
)
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.productive_hours import (
    HourScore,
    OnboardingBlock,
    ScoreSource,
    StopReason,
)
from metanoia_ai.infrastructure.db.productive_hours_repository import (
    GET_SESSIONS,
    GET_USER,
    SAVE_BLOCKS,
    SOURCE_FROM_DB,
    UPSERT_HOURS,
    SqlProductiveHoursRepository,
    blocks_from_db,
    hours_params,
)

USER = uuid4()
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class RecordingSession:
    """Returns the prepared rows of each call in order; records (sql, params)."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return Result(self.results.pop(0) if self.results else [])


def sql(statement) -> str:
    return " ".join(str(statement).split())


# --- the SQL (AC1) ---


def test_sessions_are_filtered_by_project_owner_and_learnable_rules():
    text = sql(GET_SESSIONS)
    assert 'JOIN "Project" p ON p."Id" = t."ProjectId"' in text
    assert 'p."UserId" = :user_id' in text
    assert 'fs."StartedAt" >= :since' in text
    assert 'fs."EndedAt" IS NOT NULL' in text
    assert 'fs."IsConfirmed"' in text
    assert "t.\"TaskType\" = 'Flexible'" in text


def test_user_query_skips_deleted_users_and_reads_the_active_plan():
    text = sql(GET_USER)
    assert '"DeletedAt" IS NULL' in text
    assert "us.\"Status\" = 'Active'" in text
    assert 'us."UserId" = u."Id"' in text


def test_blocks_are_saved_only_for_this_user():
    assert 'WHERE "Id" = :user_id' in sql(SAVE_BLOCKS)


def test_upsert_is_one_statement_on_the_unique_key():
    text = sql(UPSERT_HOURS)
    assert 'ON CONFLICT ("UserId", "HourOfDay") DO UPDATE' in text
    for column in ('"Score"', '"PriorScore"', '"Source"', '"UpdatedAt"'):
        assert f"{column} = EXCLUDED.{column}" in text
    assert "unnest(" in text


def test_save_hours_sends_24_rows_in_one_call():
    session = RecordingSession()
    repo = SqlProductiveHoursRepository(session)
    hours = [
        HourScore(h, Decimal("0.500"), Decimal("0.500"), ScoreSource.ONBOARDING)
        for h in range(24)
    ]
    asyncio.run(repo.save_hours(USER, hours))
    assert len(session.calls) == 1
    _, params = session.calls[0]
    assert params["user_id"] == USER
    assert params["hours"] == list(range(24))
    assert params["sources"] == ["Onboarding"] * 24


# --- conversions ---


def test_hours_params_keep_one_order():
    params = hours_params(
        USER,
        [
            HourScore(21, Decimal("0.682"), Decimal("0.300"), ScoreSource.LEARNED),
            HourScore(9, Decimal("0.800"), Decimal("0.800"), ScoreSource.ONBOARDING),
        ],
    )
    assert params["hours"] == [21, 9]
    assert params["scores"] == [Decimal("0.682"), Decimal("0.800")]
    assert params["priors"] == [Decimal("0.300"), Decimal("0.800")]
    assert params["sources"] == ["Learned", "Onboarding"]


def test_db_sources_match_the_check_constraint():
    assert set(SOURCE_FROM_DB) == {"Onboarding", "Learned"}
    assert set(SOURCE_FROM_DB.values()) == set(ScoreSource)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (["evening", "morning"], (OnboardingBlock.EVENING, OnboardingBlock.MORNING)),
    ],
)
def test_blocks_from_db(value, expected):
    assert blocks_from_db(value) == expected


def test_get_profile_maps_rows():
    user_row = SimpleNamespace(
        TimeZone="Asia/Ho_Chi_Minh", ProductiveBlocks=["morning"], PlanName="Cao cấp"
    )
    hour_row = SimpleNamespace(
        HourOfDay=9,
        Score=Decimal("0.556"),
        PriorScore=Decimal("0.800"),
        Source="Learned",
    )
    session = RecordingSession([user_row], [hour_row])

    profile = asyncio.run(SqlProductiveHoursRepository(session).get_profile(USER))

    assert profile.time_zone == ZoneInfo("Asia/Ho_Chi_Minh")
    assert profile.blocks == (OnboardingBlock.MORNING,)
    assert profile.plan_name == "Cao cấp"
    assert profile.hours == {
        9: HourScore(9, Decimal("0.556"), Decimal("0.800"), ScoreSource.LEARNED)
    }
    assert all(params == {"user_id": USER} for _, params in session.calls)


def test_get_profile_of_missing_user_reads_nothing_more():
    session = RecordingSession([])
    assert asyncio.run(SqlProductiveHoursRepository(session).get_profile(USER)) is None
    assert len(session.calls) == 1


def test_get_sessions_maps_rows():
    start = NOW - timedelta(days=1)
    row = SimpleNamespace(
        StartedAt=start,
        EndedAt=start + timedelta(minutes=30),
        StopReason="Other",
        IsConfirmed=True,
        TaskType="Flexible",
    )
    session = RecordingSession([row])
    since = NOW - timedelta(days=90)

    [sample] = asyncio.run(
        SqlProductiveHoursRepository(session).get_sessions(USER, since)
    )

    assert sample.stop_reason is StopReason.OTHER
    assert sample.task_type is TaskType.FLEXIBLE
    assert session.calls[0][1] == {"user_id": USER, "since": since}


def test_save_blocks_sends_db_strings():
    session = RecordingSession()
    blocks = [OnboardingBlock.LATE_NIGHT, OnboardingBlock.MORNING]
    asyncio.run(SqlProductiveHoursRepository(session).save_blocks(USER, blocks))
    assert session.calls[0][1] == {"user_id": USER, "blocks": ["late_night", "morning"]}


def test_repository_matches_the_port():
    repo: ProductiveHoursRepository = SqlProductiveHoursRepository(session=None)
    assert repo is not None
