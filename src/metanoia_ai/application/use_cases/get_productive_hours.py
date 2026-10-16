"""FR-18: show a user's productive hours (what the schedule will prefer)."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from metanoia_ai.application.ports.productive_hours_repository import (
    ProductiveHoursRepository,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.domain.planner import top_productive_hours
from metanoia_ai.domain.productive_hours import HourScore, OnboardingBlock


@dataclass(frozen=True)
class ProductiveHoursView:
    onboarded: bool
    blocks: tuple[OnboardingBlock, ...]
    hours: tuple[HourScore, ...]  # by hour of day; may be empty
    top_hours: tuple[int, ...]  # the hours FR-10 prefers, in time order


def build_view(
    blocks: Sequence[OnboardingBlock] | None,
    hours: Iterable[HourScore],
    top_count: int,
) -> ProductiveHoursView:
    """`top_hours` uses FR-10's own rule, so the user sees what the schedule uses (AC6)."""
    ordered = tuple(sorted(hours, key=lambda h: h.hour))
    top = top_productive_hours({h.hour: h.score for h in ordered}, top_count)
    return ProductiveHoursView(
        onboarded=blocks is not None,
        blocks=tuple(blocks or ()),
        hours=ordered,
        top_hours=tuple(sorted(top)),
    )


class GetProductiveHours:
    """Read only. No LLM call, no quota."""

    def __init__(self, repo: ProductiveHoursRepository, *, top_count: int) -> None:
        self.repo = repo
        self.top_count = top_count

    async def execute(self, user_id: UUID) -> ProductiveHoursView:
        profile = await self.repo.get_profile(user_id)
        if profile is None:
            raise UserNotFoundError(str(user_id))
        return build_view(profile.blocks, profile.hours.values(), self.top_count)
