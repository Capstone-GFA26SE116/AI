"""FR-12 endpoint: the daily "Today" briefing. Frontend → Backend → AI.

No LLM, no quota. Times are in the user's time zone (ISO 8601 with offset).
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_get_daily_briefing
from metanoia_ai.application.use_cases.get_daily_briefing import (
    Briefing,
    GetDailyBriefing,
    UserNotFoundError,
)
from metanoia_ai.domain.briefing import ReasonCode, ScheduleKind, SlotStatus
from metanoia_ai.domain.models import Language

router = APIRouter(prefix="/briefing", tags=["briefing"])

SLOT_STATUS_NAMES: dict[SlotStatus, Literal["Approved", "Done"]] = {
    SlotStatus.APPROVED: "Approved",
    SlotStatus.DONE: "Done",
}


class ScheduleItemResponse(BaseModel):
    kind: ScheduleKind
    start: datetime
    end: datetime
    title: str
    task_id: UUID | None
    status: Literal["Approved", "Done"] | None = None  # slots only


class AllDayResponse(BaseModel):
    title: str


class ReasonResponse(BaseModel):
    code: ReasonCode  # for the Frontend's icon / colour; show only `text`
    text: str


class SimilarWarningResponse(BaseModel):
    text: str


class TopTaskResponse(BaseModel):
    task_id: UUID
    title: str
    project_name: str
    deadline: datetime
    remaining_minutes: int
    reasons: list[ReasonResponse]
    similar_warning: SimilarWarningResponse | None


class AtRiskResponse(BaseModel):
    task_id: UUID
    title: str
    deadline: datetime
    missing_minutes: int


class HourRangeResponse(BaseModel):
    start: str  # "14:00"
    end: str


class ProductiveHintResponse(BaseModel):
    ranges: list[HourRangeResponse]
    text: str


class BriefingResponse(BaseModel):
    date: date
    time_zone: str
    calendar_connected: bool
    has_pending_proposal: bool
    schedule: list[ScheduleItemResponse]
    all_day: list[AllDayResponse]
    top_tasks: list[TopTaskResponse]  # empty: the Frontend invites to add a task
    at_risk: list[AtRiskResponse]
    productive_hint: ProductiveHintResponse | None


def clock(hour: int) -> str:
    return f"{hour:02d}:00"


def to_response(briefing: Briefing) -> BriefingResponse:
    hint = briefing.productive_hint
    return BriefingResponse(
        date=briefing.date,
        time_zone=briefing.time_zone.key,
        calendar_connected=briefing.calendar_connected,
        has_pending_proposal=briefing.has_pending_proposal,
        schedule=[
            ScheduleItemResponse(
                kind=item.kind,
                start=item.start,
                end=item.end,
                title=item.title,
                task_id=item.task_id,
                status=SLOT_STATUS_NAMES.get(item.status) if item.status else None,
            )
            for item in briefing.schedule
        ],
        all_day=[AllDayResponse(title=title) for title in briefing.all_day],
        top_tasks=[
            TopTaskResponse(
                task_id=t.task_id,
                title=t.title,
                project_name=t.project_name,
                deadline=t.deadline,
                remaining_minutes=t.remaining_minutes,
                reasons=[ReasonResponse(code=r.code, text=r.text) for r in t.reasons],
                similar_warning=(
                    SimilarWarningResponse(text=t.similar_warning)
                    if t.similar_warning
                    else None
                ),
            )
            for t in briefing.top_tasks
        ],
        at_risk=[
            AtRiskResponse(
                task_id=r.task_id,
                title=r.title,
                deadline=r.deadline,
                missing_minutes=r.missing_minutes,
            )
            for r in briefing.at_risk
        ],
        productive_hint=(
            ProductiveHintResponse(
                ranges=[
                    HourRangeResponse(start=clock(start), end=clock(end))
                    for start, end in hint.ranges
                ],
                text=hint.text,
            )
            if hint
            else None
        ),
    )


@router.get("/today")
async def get_today(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GetDailyBriefing, Depends(get_get_daily_briefing)],
    language: Language = Language.VI,
) -> BriefingResponse:
    """Today's briefing. `language`: vi (default) or en; anything else → 422."""
    try:
        briefing = await use_case.execute(user_id, language)
    except UserNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from error
    return to_response(briefing)
