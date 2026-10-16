"""FR-18: save the onboarding answer (first time or from Settings), then recompute."""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from metanoia_ai.application.use_cases.get_productive_hours import (
    ProductiveHoursView,
    build_view,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    RecomputeProductiveHours,
    UserNotFoundError,
)
from metanoia_ai.domain.productive_hours import onboarding_scores, parse_blocks


class SetOnboardingHours:
    """New priors for all 24 hours, mixed with timer data at once (#4). No LLM, no quota."""

    def __init__(
        self,
        recompute: RecomputeProductiveHours,
        *,
        selected_score: Decimal,
        unselected_score: Decimal,
        top_count: int,
    ) -> None:
        self.recompute = recompute
        self.repo = recompute.repo
        self.selected_score = selected_score
        self.unselected_score = unselected_score
        self.top_count = top_count

    async def execute(self, user_id: UUID, names: Sequence[str]) -> ProductiveHoursView:
        """Raises InvalidBlocksError (bad answer) before touching the database."""
        blocks = parse_blocks(names)
        priors = onboarding_scores(names, self.selected_score, self.unselected_score)
        async with self.repo.transaction():
            profile = await self.repo.get_profile(user_id)
            if profile is None:
                raise UserNotFoundError(str(user_id))
            await self.repo.save_blocks(user_id, blocks)
            hours, _ = await self.recompute.compute(user_id, profile, priors)
            await self.repo.save_hours(user_id, hours)
        return build_view(blocks, hours, self.top_count)
