"""FR-20 endpoints: personal analytics for the Dashboard. Frontend → Backend → AI.

No LLM, no quota, nothing written. Times are in the user's time zone (ISO 8601
with offset); `week_start` is a date. The Frontend shows only `text`; numbers are
for charts, `code` for icon / colour.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_get_analytics_breakdown, get_get_analytics_overview
from metanoia_ai.application.use_cases.get_analytics_breakdown import (
    AnalyticsBreakdown,
    GetAnalyticsBreakdown,
)
from metanoia_ai.application.use_cases.get_analytics_overview import (
    AnalyticsOverview,
    DomainView,
    GetAnalyticsOverview,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.application.use_cases.start_project_chat import ProjectNotFoundError
from metanoia_ai.config import Settings, get_settings
from metanoia_ai.domain.analytics import (
    AnalyticsRange,
    BiasConfidence,
    BiasDirection,
    BreakdownBy,
    InsightCode,
    Period,
)
from metanoia_ai.domain.models import EstimateSource, Language

router = APIRouter(prefix="/analytics", tags=["analytics"])

DEFAULT_TASK_LIMIT = 50


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


class PeriodResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    range: AnalyticsRange
    from_: datetime | None = Field(alias="from")  # null when range = all
    to: datetime


class CompletionResponse(BaseModel):
    total: int
    done: int
    skipped: int
    overdue_open: int
    completion_rate: float | None
    on_time_done: int
    on_time_rate: float | None
    text: str


class WeekPointResponse(BaseModel):
    week_start: date
    factor: float
    samples: int


class DomainResponse(BaseModel):
    domain_id: UUID
    domain_name: str
    samples: int
    confidence: BiasConfidence
    factor: float | None
    direction: BiasDirection | None
    percent: int | None
    text: str
    trend: list[WeekPointResponse]


class InsightResponse(BaseModel):
    code: InsightCode
    text: str


class AnalyticsOverviewResponse(PeriodResponse):
    time_zone: str
    completion: CompletionResponse
    domains: list[DomainResponse]
    insights: list[InsightResponse]


class BreakdownItemResponse(BaseModel):
    id: UUID | None  # null for the "no client" group
    name: str
    project_name: str | None  # by = task only
    domain_name: str | None  # by = task only
    task_count: int
    missing_actual_count: int
    estimated_minutes: int
    actual_minutes: int
    difference_percent: int
    estimate_source: EstimateSource | None  # by = task only
    completed_at: datetime | None  # by = task only
    text: str


class AnalyticsBreakdownResponse(PeriodResponse):
    by: BreakdownBy
    items: list[BreakdownItemResponse]


def _period(range_: AnalyticsRange, period: Period) -> dict[str, object]:
    return {"range": range_, "from_": period.start, "to": period.end}


def _domain(view: DomainView) -> DomainResponse:
    bias = view.domain.view
    return DomainResponse(
        domain_id=view.domain.domain_id,
        domain_name=view.domain.domain_name,
        samples=bias.samples,
        confidence=bias.confidence,
        factor=_float(bias.factor),
        direction=bias.direction,
        percent=bias.percent,
        text=view.text,
        trend=[
            WeekPointResponse(
                week_start=p.week_start, factor=float(p.factor), samples=p.samples
            )
            for p in view.trend
        ],
    )


def to_overview_response(result: AnalyticsOverview) -> AnalyticsOverviewResponse:
    stats = result.completion.stats
    return AnalyticsOverviewResponse(
        **_period(result.range, result.period),
        time_zone=result.time_zone.key,
        completion=CompletionResponse(
            total=stats.total,
            done=stats.done,
            skipped=stats.skipped,
            overdue_open=stats.overdue_open,
            completion_rate=_float(stats.completion_rate),
            on_time_done=stats.on_time_done,
            on_time_rate=_float(stats.on_time_rate),
            text=result.completion.text,
        ),
        domains=[_domain(d) for d in result.domains],
        insights=[InsightResponse(code=i.code, text=i.text) for i in result.insights],
    )


def to_breakdown_response(result: AnalyticsBreakdown) -> AnalyticsBreakdownResponse:
    zone = result.time_zone
    return AnalyticsBreakdownResponse(
        **_period(result.range, result.period),
        by=result.by,
        items=[
            BreakdownItemResponse(
                id=row.item.id,
                name=row.name,
                project_name=row.item.project_name,
                domain_name=row.item.domain_name,
                task_count=row.item.task_count,
                missing_actual_count=row.item.missing_actual_count,
                estimated_minutes=row.item.estimated_minutes,
                actual_minutes=row.item.actual_minutes,
                difference_percent=row.item.difference_percent,
                estimate_source=row.item.estimate_source,
                completed_at=(
                    row.item.completed_at.astimezone(zone)
                    if row.item.completed_at
                    else None
                ),
                text=row.text,
            )
            for row in result.rows
        ],
    )


def _user_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


def _bad_query(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message
    )


@router.get("/overview")
async def get_overview(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GetAnalyticsOverview, Depends(get_get_analytics_overview)],
    range: AnalyticsRange = AnalyticsRange.LAST_30_DAYS,
    language: Language = Language.VI,
) -> AnalyticsOverviewResponse:
    """Completion, learned bias per domain with weekly trend, up to 3 insights."""
    try:
        result = await use_case.execute(user_id, range, language)
    except UserNotFoundError as error:
        raise _user_not_found() from error
    return to_overview_response(result)


@router.get("/breakdown")
async def get_breakdown(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GetAnalyticsBreakdown, Depends(get_get_analytics_breakdown)],
    settings: Annotated[Settings, Depends(get_settings)],
    by: BreakdownBy,
    range: AnalyticsRange = AnalyticsRange.LAST_30_DAYS,
    language: Language = Language.VI,
    project_id: UUID | None = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> AnalyticsBreakdownResponse:
    """Planned vs actual per project, client or task. project_id / limit: by = task only."""
    if by is not BreakdownBy.TASK and (project_id is not None or limit is not None):
        raise _bad_query("project_id and limit are only allowed with by=task")
    if limit is not None and limit > settings.analytics_task_limit_max:
        raise _bad_query(f"limit must be at most {settings.analytics_task_limit_max}")
    if by is BreakdownBy.TASK and limit is None:
        limit = min(DEFAULT_TASK_LIMIT, settings.analytics_task_limit_max)
    try:
        result = await use_case.execute(user_id, by, range, language, project_id, limit)
    except UserNotFoundError as error:
        raise _user_not_found() from error
    except ProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from error
    return to_breakdown_response(result)
