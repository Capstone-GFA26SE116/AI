"""AI quota on the shared Supabase Postgres: plan, AiCallsUsed counter, AIUsageLog."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.quota_repository import (
    AIFeature,
    Subscription,
    UsageLogEntry,
)

FEATURE_TO_DB = {
    AIFeature.CHAT: "Chat",
    AIFeature.SCHEDULE: "Schedule",
    AIFeature.BRIEFING: "Briefing",
    AIFeature.EMBEDDING: "Embedding",
}

# Several Active rows would be a Backend bug; take the newest (FR-10 #18).
GET_SUBSCRIPTION = text("""
    SELECT us."Id", sp."Price", sp."AiCallLimit", us."AiCallsUsed"
    FROM "UserSubscription" us
    JOIN "SubscriptionPlan" sp ON sp."Id" = us."PlanId"
    WHERE us."UserId" = :user_id
      AND us."Status" = 'Active'
      AND us."StartAt" <= :now
    ORDER BY us."StartAt" DESC
    LIMIT 1
""")

COUNT_CHARGED_CALLS = text("""
    SELECT count(*)
    FROM "AIUsageLog" l
    JOIN "UserSubscription" us ON us."Id" = l."UserSubscriptionId"
    WHERE us."UserId" = :user_id
      AND l."QuotaCharged"
      AND l."CreatedAt" >= :since
""")

# One statement: two requests at once cannot both take the last call.
TRY_CHARGE = text("""
    UPDATE "UserSubscription" us
    SET "AiCallsUsed" = us."AiCallsUsed" + 1
    FROM "SubscriptionPlan" sp
    WHERE us."Id" = :subscription_id
      AND sp."Id" = us."PlanId"
      AND (sp."AiCallLimit" IS NULL OR us."AiCallsUsed" < sp."AiCallLimit")
    RETURNING us."Id"
""")

INSERT_USAGE = text("""
    INSERT INTO "AIUsageLog" (
        "UserSubscriptionId", "Feature", "Model", "InputTokens", "OutputTokens",
        "Cost", "LatencyMs", "Succeeded", "QuotaCharged"
    ) VALUES (
        :subscription_id, :feature, :model, :input_tokens, :output_tokens,
        :cost, :latency_ms, :succeeded, :quota_charged
    )
""")


class SqlQuotaRepository:
    """QuotaRepository on Postgres. Shares the request's session (and transaction)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_subscription(
        self, user_id: UUID, now: datetime
    ) -> Subscription | None:
        params = {"user_id": user_id, "now": now}
        row = (await self.session.execute(GET_SUBSCRIPTION, params)).one_or_none()
        if row is None:
            return None
        return Subscription(
            subscription_id=row.Id,
            price=row.Price,
            ai_call_limit=row.AiCallLimit,
            ai_calls_used=row.AiCallsUsed,
        )

    async def count_charged_calls(self, user_id: UUID, since: datetime) -> int:
        params = {"user_id": user_id, "since": since}
        return (await self.session.execute(COUNT_CHARGED_CALLS, params)).scalar_one()

    async def try_charge(self, subscription: Subscription) -> bool:
        params = {"subscription_id": subscription.subscription_id}
        return (await self.session.execute(TRY_CHARGE, params)).first() is not None

    async def log_usage(self, entry: UsageLogEntry) -> None:
        await self.session.execute(
            INSERT_USAGE,
            {
                "subscription_id": entry.subscription_id,
                "feature": FEATURE_TO_DB[entry.feature],
                "model": entry.model,
                "input_tokens": entry.input_tokens,
                "output_tokens": entry.output_tokens,
                "cost": entry.cost,
                "latency_ms": entry.latency_ms,
                "succeeded": entry.succeeded,
                "quota_charged": entry.quota_charged,
            },
        )
