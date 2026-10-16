"""What productive hours (FR-18) need from storage. The database code implements it."""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.productive_hours import FocusSample, HourScore, OnboardingBlock


@dataclass(frozen=True)
class ProductiveProfile:
    """Everything stored about one user's productive hours."""

    time_zone: ZoneInfo
    # The onboarding answer (#19); None = the user has not onboarded.
    blocks: tuple[OnboardingBlock, ...] | None
    plan_name: str | None  # name of the Active plan; None = no Active plan (#16)
    hours: dict[int, HourScore]  # stored rows by hour of day; may be empty


class ProductiveHoursRepository(Protocol):
    """Storage for productive hours. Every call is limited to the given user."""

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """All calls inside run in one DB transaction: all saved, or nothing."""
        ...

    async def get_profile(self, user_id: UUID) -> ProductiveProfile | None:
        """The user's profile, or None if the user does not exist (or was deleted)."""
        ...

    async def get_sessions(self, user_id: UUID, since: datetime) -> list[FocusSample]:
        """Focus sessions on this user's tasks that started at or after `since`.

        May also return sessions that cannot be learned from: the caller
        filters with `is_learnable`.
        """
        ...

    async def save_blocks(
        self, user_id: UUID, blocks: Sequence[OnboardingBlock]
    ) -> None:
        """Store the onboarding answer (User.ProductiveBlocks)."""
        ...

    async def save_hours(self, user_id: UUID, hours: Sequence[HourScore]) -> None:
        """Insert or update one row per hour: Score, PriorScore, Source, UpdatedAt."""
        ...
