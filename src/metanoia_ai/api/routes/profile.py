"""FR-18 endpoints for the user's productive hours. Frontend → Backend → AI (#1)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_get_productive_hours, get_set_onboarding_hours
from metanoia_ai.application.use_cases.get_productive_hours import (
    GetProductiveHours,
    ProductiveHoursView,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.application.use_cases.set_onboarding_hours import SetOnboardingHours
from metanoia_ai.domain.productive_hours import InvalidBlocksError, ScoreSource

router = APIRouter(prefix="/profile", tags=["profile"])

SOURCE_NAMES: dict[ScoreSource, Literal["Onboarding", "Learned"]] = {
    ScoreSource.ONBOARDING: "Onboarding",
    ScoreSource.LEARNED: "Learned",
}


class OnboardingRequest(BaseModel):
    # morning / midday / afternoon / evening / late_night; 1-5, no repeats.
    blocks: list[str]


class HourResponse(BaseModel):
    hour: int
    score: float
    source: Literal["Onboarding", "Learned"]


class ProductiveHoursResponse(BaseModel):
    onboarded: bool
    blocks: list[str]
    hours: list[HourResponse]  # by hour of day
    top_hours: list[int]  # the hours the schedule prefers


def to_response(view: ProductiveHoursView) -> ProductiveHoursResponse:
    return ProductiveHoursResponse(
        onboarded=view.onboarded,
        blocks=[b.value for b in view.blocks],
        hours=[
            HourResponse(
                hour=h.hour, score=float(h.score), source=SOURCE_NAMES[h.source]
            )
            for h in view.hours
        ],
        top_hours=list(view.top_hours),
    )


def user_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.put("/productive-hours/onboarding")
async def set_onboarding(
    body: OnboardingRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[SetOnboardingHours, Depends(get_set_onboarding_hours)],
) -> ProductiveHoursResponse:
    """Save the onboarding answer and recompute at once (first time or from Settings)."""
    try:
        view = await use_case.execute(user_id, body.blocks)
    except InvalidBlocksError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except UserNotFoundError as error:
        raise user_not_found() from error
    return to_response(view)


@router.get("/productive-hours")
async def get_productive_hours(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[GetProductiveHours, Depends(get_get_productive_hours)],
) -> ProductiveHoursResponse:
    """Current scores. No rows yet → `hours: []`; no answer yet → `onboarded: false`."""
    try:
        return to_response(await use_case.execute(user_id))
    except UserNotFoundError as error:
        raise user_not_found() from error
