"""FR-18: recompute a user's productive hours from scratch. Called by the Backend after each Stop."""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from metanoia_ai.application.ports.productive_hours_repository import (
    ProductiveHoursRepository,
    ProductiveProfile,
)
from metanoia_ai.domain.productive_hours import HourScore, is_learnable, learned_scores


class UserNotFoundError(Exception):
    """The user in the token is not in the database (or was deleted)."""


@dataclass(frozen=True)
class RecomputeResult:
    hours_updated: int  # 24, or 0 when nothing was written (#8)
    sessions_used: int


class RecomputeProductiveHours:
    """Onboarding scores for every plan; only the learning plans add timer data (#16).

    No LLM call, no quota. Same data → same result, so repeat calls are safe (#6, #14).
    """

    def __init__(
        self,
        repo: ProductiveHoursRepository,
        *,
        prior_weight: Decimal,
        min_session_minutes: int,
        window_days: int,
        learning_plans: Collection[str],
        now: Callable[[], datetime],
    ) -> None:
        self.repo = repo
        self.prior_weight = prior_weight
        self.min_session_minutes = min_session_minutes
        self.window_days = window_days
        self.learning_plans = frozenset(learning_plans)
        self.now = now

    async def execute(self, user_id: UUID) -> RecomputeResult:
        async with self.repo.transaction():
            profile = await self.repo.get_profile(user_id)
            if profile is None:
                raise UserNotFoundError(str(user_id))
            priors = {h: row.prior for h, row in profile.hours.items()}
            hours, used = await self.compute(user_id, profile, priors)
            if not profile.hours and used == 0:
                return RecomputeResult(hours_updated=0, sessions_used=0)  # #8
            await self.repo.save_hours(user_id, hours)
            return RecomputeResult(hours_updated=len(hours), sessions_used=used)

    async def compute(
        self, user_id: UUID, profile: ProductiveProfile, priors: Mapping[int, Decimal]
    ) -> tuple[list[HourScore], int]:
        """The 24 hours from `priors` + learnable sessions, and how many sessions counted.

        Shared with onboarding, which passes its new priors.
        """
        sessions = []
        if profile.plan_name in self.learning_plans:
            since = self.now() - timedelta(days=self.window_days)
            found = await self.repo.get_sessions(user_id, since)
            sessions = [s for s in found if is_learnable(s, since)]
        hours = learned_scores(
            priors,
            sessions,
            profile.time_zone,
            self.prior_weight,
            self.min_session_minutes,
        )
        return hours, len(sessions)
