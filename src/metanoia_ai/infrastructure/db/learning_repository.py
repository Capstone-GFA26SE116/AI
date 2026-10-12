"""K_bias learning storage on the shared Supabase Postgres (tables owned by the Backend).

Table and column names are quoted: Postgres treats "KBias" and kbias as different.
"""

from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.learning_repository import (
    DomainProfile,
    KBiasLogEntry,
    TaskAlreadyLearnedError,
    TaskForLearning,
)
from metanoia_ai.domain.models import (
    ActualTimeSource,
    ConfidenceTier,
    FocusSessionRecord,
    TaskType,
)

# Domain names <-> the strings allowed by the DB CHECK constraints.
TASK_TYPE_FROM_DB = {"Flexible": TaskType.FLEXIBLE, "Fixed": TaskType.FIXED}
SOURCE_FROM_DB = {
    "Timer": ActualTimeSource.TIMER,
    "SelfReported": ActualTimeSource.SELF_REPORTED,
    "AutoTimeout": ActualTimeSource.AUTO_TIMEOUT,
}
SOURCE_TO_DB = {source: name for name, source in SOURCE_FROM_DB.items()}
TIER_TO_DB = {
    ConfidenceTier.HIGH: "High",
    ConfidenceTier.MEDIUM: "Medium",
    ConfidenceTier.LOW: "Low",
}
TIER_FROM_DB = {name: tier for tier, name in TIER_TO_DB.items()}
DONE_STATUS = "Done"
TASK_ALREADY_LEARNED_CONSTRAINT = "KBiasUpdateLog_TaskId_key"

GET_TASK = text("""
    SELECT t."Id", t."DomainId", t."TaskType", t."Status", t."EstimatedMinutes"
    FROM "Task" t
    JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE t."Id" = :task_id AND p."UserId" = :user_id
""")

GET_SESSIONS = text("""
    SELECT "StartedAt", "EndedAt", "CaptureMethod", "IsConfirmed"
    FROM "FocusSession"
    WHERE "TaskId" = :task_id
    ORDER BY "StartedAt"
""")

GET_LOG = text("""
    SELECT l."TaskId", l."OldKBias", l."NewKBias", l."Alpha", l."EstimatedMinutes",
           l."ActualMinutes", l."ObservedRatio", l."CaptureMethod",
           l."ConfidenceTier", l."SampleIndex"
    FROM "KBiasUpdateLog" l
    JOIN "UserDomainProfile" p ON p."Id" = l."UserDomainProfileId"
    WHERE l."TaskId" = :task_id AND p."UserId" = :user_id
""")

CREATE_PROFILE_IF_MISSING = text("""
    INSERT INTO "UserDomainProfile" ("UserId", "DomainId", "KBias", "SampleCount")
    VALUES (:user_id, :domain_id, :k_bias, 0)
    ON CONFLICT ("UserId", "DomainId") DO NOTHING
""")

LOCK_PROFILE = text("""
    SELECT "KBias", "SampleCount"
    FROM "UserDomainProfile"
    WHERE "UserId" = :user_id AND "DomainId" = :domain_id
    FOR UPDATE
""")

UPDATE_PROFILE = text("""
    UPDATE "UserDomainProfile"
    SET "KBias" = :k_bias, "SampleCount" = :sample_count, "UpdatedAt" = now()
    WHERE "UserId" = :user_id AND "DomainId" = :domain_id
    RETURNING "Id"
""")

INSERT_LOG = text("""
    INSERT INTO "KBiasUpdateLog" (
        "UserDomainProfileId", "TaskId", "OldKBias", "NewKBias", "Alpha",
        "EstimatedMinutes", "ActualMinutes", "ObservedRatio", "CaptureMethod",
        "ConfidenceTier", "SampleIndex"
    ) VALUES (
        :profile_id, :task_id, :old_k, :new_k, :alpha,
        :estimated_minutes, :actual_minutes, :observed_ratio, :capture_method,
        :confidence_tier, :sample_index
    )
""")


def to_decimal(value: float) -> Decimal:
    """Exact decimal for numeric columns (via str, so 1.34 stays 1.34)."""
    return Decimal(str(value))


class SqlLearningRepository:
    """LearningRepository on Postgres. One instance per request (one session)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def transaction(self) -> AbstractAsyncContextManager[object]:
        return self.session.begin()

    async def get_task(self, user_id: UUID, task_id: UUID) -> TaskForLearning | None:
        params = {"task_id": task_id, "user_id": user_id}
        row = (await self.session.execute(GET_TASK, params)).one_or_none()
        if row is None:
            return None
        sessions = await self.session.execute(GET_SESSIONS, {"task_id": task_id})
        return TaskForLearning(
            task_id=row.Id,
            domain_id=row.DomainId,
            task_type=TASK_TYPE_FROM_DB[row.TaskType],
            is_done=row.Status == DONE_STATUS,
            estimated_minutes=row.EstimatedMinutes,
            sessions=tuple(
                FocusSessionRecord(
                    started_at=s.StartedAt,
                    ended_at=s.EndedAt,
                    source=SOURCE_FROM_DB[s.CaptureMethod],
                    is_confirmed=s.IsConfirmed,
                )
                for s in sessions
            ),
        )

    async def get_log(self, user_id: UUID, task_id: UUID) -> KBiasLogEntry | None:
        params = {"task_id": task_id, "user_id": user_id}
        row = (await self.session.execute(GET_LOG, params)).one_or_none()
        if row is None:
            return None
        return KBiasLogEntry(
            task_id=row.TaskId,
            old_k=float(row.OldKBias),
            new_k=float(row.NewKBias),
            alpha=float(row.Alpha),
            estimated_minutes=row.EstimatedMinutes,
            actual_minutes=row.ActualMinutes,
            observed_ratio=float(row.ObservedRatio),
            source=SOURCE_FROM_DB[row.CaptureMethod],
            confidence=TIER_FROM_DB[row.ConfidenceTier],
            sample_index=row.SampleIndex,
        )

    async def lock_profile(
        self, user_id: UUID, domain_id: UUID, initial_k: float
    ) -> DomainProfile:
        keys = {"user_id": user_id, "domain_id": domain_id}
        # Make sure the row exists, so FOR UPDATE always has something to lock.
        await self.session.execute(
            CREATE_PROFILE_IF_MISSING, {**keys, "k_bias": to_decimal(initial_k)}
        )
        row = (await self.session.execute(LOCK_PROFILE, keys)).one()
        return DomainProfile(k_bias=float(row.KBias), sample_count=row.SampleCount)

    async def save(self, user_id: UUID, domain_id: UUID, entry: KBiasLogEntry) -> None:
        profile_id = (
            await self.session.execute(
                UPDATE_PROFILE,
                {
                    "user_id": user_id,
                    "domain_id": domain_id,
                    "k_bias": to_decimal(entry.new_k),
                    "sample_count": entry.sample_index,
                },
            )
        ).scalar_one()
        try:
            await self.session.execute(
                INSERT_LOG,
                {
                    "profile_id": profile_id,
                    "task_id": entry.task_id,
                    "old_k": to_decimal(entry.old_k),
                    "new_k": to_decimal(entry.new_k),
                    "alpha": to_decimal(entry.alpha),
                    "estimated_minutes": entry.estimated_minutes,
                    "actual_minutes": entry.actual_minutes,
                    "observed_ratio": to_decimal(entry.observed_ratio),
                    "capture_method": SOURCE_TO_DB[entry.source],
                    "confidence_tier": TIER_TO_DB[entry.confidence],
                    "sample_index": entry.sample_index,
                },
            )
        except IntegrityError as error:
            if TASK_ALREADY_LEARNED_CONSTRAINT in str(error.orig):
                raise TaskAlreadyLearnedError(str(entry.task_id)) from error
            raise
