"""FR-20: Dashboard overview. Code computes everything; sentences from templates; no LLM.

1. Read the user's time zone → period of the range.
2. Completion and on-time rates (#6, #7).
3. Learned bias per domain with a weekly trend (#3, #4, #8).
4. Up to 3 insights (plan §2 #10), using planned vs actual per project.

No quota is used and nothing is written.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.analytics_repository import (
    AnalyticsDomainProfile,
    AnalyticsRepository,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.domain.analytics import (
    AnalyticsRange,
    BreakdownBy,
    CompletionStats,
    DomainBias,
    InsightCode,
    Period,
    WeekPoint,
    bias_sort_key,
    breakdown,
    classify_bias,
    completion_stats,
    period_for,
    select_insights,
    weekly_trend,
)
from metanoia_ai.domain.analytics_text import completion_text, domain_text, insight_text
from metanoia_ai.domain.models import Language

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalyticsSettings:
    bias_min_samples: int
    bias_confident_samples: int
    bias_neutral_band: Decimal
    project_insight_min_percent: int
    project_insight_min_tasks: int
    insight_limit: int


@dataclass(frozen=True)
class CompletionView:
    stats: CompletionStats
    text: str


@dataclass(frozen=True)
class DomainView:
    domain: DomainBias
    trend: tuple[WeekPoint, ...]
    text: str


@dataclass(frozen=True)
class InsightView:
    code: InsightCode
    text: str


@dataclass(frozen=True)
class AnalyticsOverview:
    range: AnalyticsRange
    period: Period
    time_zone: ZoneInfo
    completion: CompletionView
    domains: tuple[DomainView, ...]
    insights: tuple[InsightView, ...]


async def read_period(
    repo: AnalyticsRepository,
    user_id: UUID,
    range_: AnalyticsRange,
    now: datetime,
    project_id: UUID | None = None,
) -> tuple[Period, ZoneInfo, bool | None]:
    """Time zone of the user and the period of the range; also checks the project."""
    user = await repo.get_user(user_id, project_id)
    if user is None:
        raise UserNotFoundError(str(user_id))
    return period_for(range_, now, user.time_zone), user.time_zone, user.project_found


class GetAnalyticsOverview:
    def __init__(
        self,
        repo: AnalyticsRepository,
        settings: AnalyticsSettings,
        now: Callable[[], datetime],
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.now = now

    async def execute(
        self, user_id: UUID, range_: AnalyticsRange, language: Language
    ) -> AnalyticsOverview:
        period, zone, _ = await read_period(self.repo, user_id, range_, self.now())

        stats = completion_stats(
            await self.repo.get_deadline_tasks(user_id, period), period
        )
        if stats.missing_deadline or stats.done_without_completed_at:
            logger.warning(
                "Analytics: %d Flexible task(s) without deadline, %d Done without CompletedAt",
                stats.missing_deadline,
                stats.done_without_completed_at,
            )

        profiles = await self.repo.get_domain_profiles(user_id, period)
        domains = sorted(
            (self._domain(p, period, zone, language) for p in profiles),
            key=lambda d: bias_sort_key(d.domain.view),
        )

        projects = breakdown(
            await self.repo.get_done_tasks(user_id, period), BreakdownBy.PROJECT, period
        )
        insights = select_insights(
            [d.domain for d in domains],
            projects.items,
            stats,
            limit=self.settings.insight_limit,
            project_min_percent=self.settings.project_insight_min_percent,
            project_min_tasks=self.settings.project_insight_min_tasks,
        )

        return AnalyticsOverview(
            range=range_,
            period=period,
            time_zone=zone,
            completion=CompletionView(stats, completion_text(stats, language)),
            domains=tuple(domains),
            insights=tuple(
                InsightView(i.code, insight_text(i, language)) for i in insights
            ),
        )

    def _domain(
        self,
        profile: AnalyticsDomainProfile,
        period: Period,
        zone: ZoneInfo,
        language: Language,
    ) -> DomainView:
        view = classify_bias(
            profile.k_bias,
            profile.sample_count,
            min_samples=self.settings.bias_min_samples,
            confident_samples=self.settings.bias_confident_samples,
            neutral_band=self.settings.bias_neutral_band,
        )
        domain = DomainBias(profile.domain_id, profile.domain_name, view)
        return DomainView(
            domain=domain,
            trend=tuple(weekly_trend(profile.updates, period, zone)),
            text=domain_text(domain, language),
        )
