"""Productive hours storage (FR-18) on the shared Supabase Postgres.

Every query is limited to one user. FocusSession reaches its owner via Task → Project.
PriorScore and User.ProductiveBlocks were added for FR-18 (docs/database.md).
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.productive_hours_repository import (
    ProductiveProfile,
)
from metanoia_ai.domain.productive_hours import (
    FocusSample,
    HourScore,
    OnboardingBlock,
    ScoreSource,
)
from metanoia_ai.infrastructure.db.learning_repository import TASK_TYPE_FROM_DB
from metanoia_ai.infrastructure.db.memory_repository import STOP_REASON_FROM_DB

SOURCE_TO_DB = {ScoreSource.ONBOARDING: "Onboarding", ScoreSource.LEARNED: "Learned"}
SOURCE_FROM_DB = {name: source for source, name in SOURCE_TO_DB.items()}

# The Active plan: at most one per user (UX_UserSubscription_OneActivePerUser).
GET_USER = text("""
    SELECT u."TimeZone", u."ProductiveBlocks",
           (SELECT sp."Name"
            FROM "UserSubscription" us
            JOIN "SubscriptionPlan" sp ON sp."Id" = us."PlanId"
            WHERE us."UserId" = u."Id" AND us."Status" = 'Active'
            ORDER BY us."StartAt" DESC
            LIMIT 1) AS "PlanName"
    FROM "User" u
    WHERE u."Id" = :user_id AND u."DeletedAt" IS NULL
""")

GET_HOURS = text("""
    SELECT "HourOfDay", "Score", "PriorScore", "Source"
    FROM "UserCircadianProfile"
    WHERE "UserId" = :user_id
""")

# Only learnable sessions (#11), only the columns needed. The use case checks again.
GET_SESSIONS = text("""
    SELECT fs."StartedAt", fs."EndedAt", fs."StopReason", fs."IsConfirmed",
           t."TaskType"
    FROM "FocusSession" fs
    JOIN "Task" t ON t."Id" = fs."TaskId"
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user_id
      AND fs."StartedAt" >= :since
      AND fs."EndedAt" IS NOT NULL
      AND fs."IsConfirmed"
      AND t."TaskType" = 'Flexible'
""")

SAVE_BLOCKS = text("""
    UPDATE "User"
    SET "ProductiveBlocks" = CAST(:blocks AS text[])
    WHERE "Id" = :user_id AND "DeletedAt" IS NULL
""")

# All hours in one statement: one array per column, one row per hour.
UPSERT_HOURS = text("""
    INSERT INTO "UserCircadianProfile" (
        "UserId", "HourOfDay", "Score", "PriorScore", "Source", "UpdatedAt"
    )
    SELECT CAST(:user_id AS uuid), h.hour, h.score, h.prior, h.source, now()
    FROM unnest(
        CAST(:hours AS smallint[]),
        CAST(:scores AS numeric[]),
        CAST(:priors AS numeric[]),
        CAST(:sources AS varchar[])
    ) AS h(hour, score, prior, source)
    ON CONFLICT ("UserId", "HourOfDay") DO UPDATE SET
        "Score" = EXCLUDED."Score",
        "PriorScore" = EXCLUDED."PriorScore",
        "Source" = EXCLUDED."Source",
        "UpdatedAt" = EXCLUDED."UpdatedAt"
""")


class SqlProductiveHoursRepository:
    """ProductiveHoursRepository on Postgres. One instance per session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def transaction(self) -> AbstractAsyncContextManager[object]:
        return self.session.begin()

    async def get_profile(self, user_id: UUID) -> ProductiveProfile | None:
        user = {"user_id": user_id}
        row = (await self.session.execute(GET_USER, user)).one_or_none()
        if row is None:
            return None
        hours = await self.session.execute(GET_HOURS, user)
        return ProductiveProfile(
            time_zone=ZoneInfo(row.TimeZone),
            blocks=blocks_from_db(row.ProductiveBlocks),
            plan_name=row.PlanName,
            hours={
                r.HourOfDay: HourScore(
                    hour=r.HourOfDay,
                    score=r.Score,
                    prior=r.PriorScore,
                    source=SOURCE_FROM_DB[r.Source],
                )
                for r in hours
            },
        )

    async def get_sessions(self, user_id: UUID, since: datetime) -> list[FocusSample]:
        params = {"user_id": user_id, "since": since}
        return [
            FocusSample(
                started_at=r.StartedAt,
                ended_at=r.EndedAt,
                stop_reason=STOP_REASON_FROM_DB[r.StopReason],
                is_confirmed=r.IsConfirmed,
                task_type=TASK_TYPE_FROM_DB[r.TaskType],
            )
            for r in await self.session.execute(GET_SESSIONS, params)
        ]

    async def save_blocks(
        self, user_id: UUID, blocks: Sequence[OnboardingBlock]
    ) -> None:
        params = {"user_id": user_id, "blocks": [b.value for b in blocks]}
        await self.session.execute(SAVE_BLOCKS, params)

    async def save_hours(self, user_id: UUID, hours: Sequence[HourScore]) -> None:
        await self.session.execute(UPSERT_HOURS, hours_params(user_id, hours))


def blocks_from_db(value: list[str] | None) -> tuple[OnboardingBlock, ...] | None:
    """User.ProductiveBlocks → domain blocks. NULL = not onboarded (#19)."""
    if value is None:
        return None
    return tuple(OnboardingBlock(name) for name in value)


def hours_params(user_id: UUID, hours: Sequence[HourScore]) -> dict[str, object]:
    """One array per column for UPSERT_HOURS (same order in every array)."""
    return {
        "user_id": user_id,
        "hours": [h.hour for h in hours],
        "scores": [h.score for h in hours],
        "priors": [h.prior for h in hours],
        "sources": [SOURCE_TO_DB[h.source] for h in hours],
    }
