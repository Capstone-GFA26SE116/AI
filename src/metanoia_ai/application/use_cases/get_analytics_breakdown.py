"""FR-20: planned vs actual time per project, client or task. No LLM, no quota."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.analytics_repository import AnalyticsRepository
from metanoia_ai.application.use_cases.get_analytics_overview import read_period
from metanoia_ai.application.use_cases.start_project_chat import ProjectNotFoundError
from metanoia_ai.domain.analytics import (
    AnalyticsRange,
    BreakdownBy,
    BreakdownItem,
    Period,
    breakdown,
)
from metanoia_ai.domain.analytics_text import difference_text, item_name
from metanoia_ai.domain.models import Language

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BreakdownRow:
    item: BreakdownItem
    name: str  # "No client" group already named in the asked language
    text: str


@dataclass(frozen=True)
class AnalyticsBreakdown:
    by: BreakdownBy
    range: AnalyticsRange
    period: Period
    time_zone: ZoneInfo
    rows: tuple[BreakdownRow, ...]


class GetAnalyticsBreakdown:
    def __init__(self, repo: AnalyticsRepository, now: Callable[[], datetime]) -> None:
        self.repo = repo
        self.now = now

    async def execute(
        self,
        user_id: UUID,
        by: BreakdownBy,
        range_: AnalyticsRange,
        language: Language,
        project_id: UUID | None = None,
        limit: int | None = None,
    ) -> AnalyticsBreakdown:
        """project_id and limit only apply to by = task (the route rejects them otherwise)."""
        period, zone, project_found = await read_period(
            self.repo, user_id, range_, self.now(), project_id
        )
        if project_id is not None and not project_found:
            raise ProjectNotFoundError(str(project_id))

        tasks = await self.repo.get_done_tasks(user_id, period, project_id)
        result = breakdown(tasks, by, period, limit if by is BreakdownBy.TASK else None)
        if result.missing_estimate:
            logger.warning(
                "Analytics: %d Done Flexible task(s) without estimate",
                result.missing_estimate,
            )

        return AnalyticsBreakdown(
            by=by,
            range=range_,
            period=period,
            time_zone=zone,
            rows=tuple(
                BreakdownRow(
                    item=item,
                    name=item_name(item, language),
                    text=difference_text(item.difference_percent, language),
                )
                for item in result.items
            ),
        )
