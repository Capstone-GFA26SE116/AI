"""What the personal analytics (FR-20) needs from storage. Read-only."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.analytics import DeadlineTask, DoneTask, KUpdate, Period


@dataclass(frozen=True)
class AnalyticsUser:
    time_zone: ZoneInfo
    # Only when a project_id was asked for: does it belong to the user? None otherwise.
    project_found: bool | None = None


@dataclass(frozen=True)
class AnalyticsDomainProfile:
    """UserDomainProfile of one domain, with its K updates inside the period."""

    domain_id: UUID
    domain_name: str
    k_bias: Decimal
    sample_count: int
    updates: tuple[KUpdate, ...]


class AnalyticsRepository(Protocol):
    """Every read is limited to the given user (Task through Project.UserId)."""

    async def get_user(
        self, user_id: UUID, project_id: UUID | None = None
    ) -> AnalyticsUser | None:
        """None when the user does not exist (or is deleted)."""
        ...

    async def get_deadline_tasks(
        self, user_id: UUID, period: Period
    ) -> list[DeadlineTask]:
        """Flexible tasks with a deadline inside the period (#6)."""
        ...

    async def get_domain_profiles(
        self, user_id: UUID, period: Period
    ) -> list[AnalyticsDomainProfile]:
        """Every domain profile of the user, with KBiasUpdateLog rows inside the period (#8)."""
        ...

    async def get_done_tasks(
        self, user_id: UUID, period: Period, project_id: UUID | None = None
    ) -> list[DoneTask]:
        """Flexible Done tasks completed inside the period, with all their sessions (#9)."""
        ...
