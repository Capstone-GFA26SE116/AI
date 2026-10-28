"""What the AI quota needs from storage: the user's plan, the counter and AIUsageLog."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class AIFeature(StrEnum):
    """Which AI feature made a call (AIUsageLog.Feature)."""

    CHAT = "chat"
    SCHEDULE = "schedule"
    BRIEFING = "briefing"
    EMBEDDING = "embedding"


@dataclass(frozen=True)
class Subscription:
    """The user's active plan and how many AI calls it has used."""

    subscription_id: UUID
    price: int
    ai_call_limit: int | None  # None: no limit
    ai_calls_used: int

    @property
    def is_free(self) -> bool:
        return self.price == 0

    @property
    def has_calls_left(self) -> bool:
        return self.ai_call_limit is None or self.ai_calls_used < self.ai_call_limit


@dataclass(frozen=True)
class UsageLogEntry:
    """One LLM call, for cost tracking and the daily anti-spam count."""

    subscription_id: UUID
    feature: AIFeature
    model: str
    input_tokens: int
    output_tokens: int
    cost: Decimal
    latency_ms: int
    succeeded: bool
    quota_charged: bool


class QuotaRepository(Protocol):
    """Quota storage. Writes join the ScheduleRepository transaction (same session)."""

    async def get_subscription(
        self, user_id: UUID, now: datetime
    ) -> Subscription | None:
        """The Active plan (newest StartAt if there are several), or None."""
        ...

    async def try_charge(self, subscription: Subscription) -> bool:
        """Add 1 to AiCallsUsed only if still under the limit. False if not.

        One conditional UPDATE, so two requests at once cannot both pass the limit.
        """
        ...

    async def log_usage(self, entry: UsageLogEntry) -> None:
        """Add one AIUsageLog row."""
        ...
