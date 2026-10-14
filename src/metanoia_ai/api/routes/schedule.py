"""FR-10 endpoint. Called by the Backend when the user clicks "Schedule"."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_propose_schedule
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    ProposalResult,
    ProposeSchedule,
    QuotaExceededError,
)
from metanoia_ai.domain.models import Language, WarningType

router = APIRouter(prefix="/schedule", tags=["schedule"])


class ProposeRequest(BaseModel):
    task_ids: list[UUID] | None = None  # None: every task that can be scheduled
    language: Language = Language.VI


class SlotResponse(BaseModel):
    slot_id: UUID
    task_id: UUID
    subtask_id: UUID | None
    start: datetime  # ISO 8601 in the user's time zone
    end: datetime
    proposed_minutes: int
    explanation: str | None


class WarningResponse(BaseModel):
    task_id: UUID | None
    type: WarningType
    needed_minutes: int | None
    free_minutes: int | None
    message: str


class ProposalResponse(BaseModel):
    proposal_id: UUID | None  # None: nothing to schedule, nothing saved
    is_feasible: bool
    missing_minutes: int | None
    explanation: str
    slots: list[SlotResponse]
    warnings: list[WarningResponse]


@router.post("/propose")
async def propose(
    body: ProposeRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[ProposeSchedule, Depends(get_propose_schedule)],
) -> ProposalResponse:
    """Propose slots for the user's Flexible tasks. Nothing reaches the calendar yet."""
    try:
        result = await use_case.execute(user_id, body.task_ids, body.language)
    except QuotaExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI quota exceeded",
        ) from error
    except NoSubscriptionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No active subscription"
        ) from error
    return to_response(result)


def to_response(result: ProposalResult) -> ProposalResponse:
    return ProposalResponse(
        proposal_id=result.proposal_id,
        is_feasible=result.is_feasible,
        missing_minutes=result.missing_minutes,
        explanation=result.explanation,
        slots=[
            SlotResponse(
                slot_id=s.slot_id,
                task_id=s.task_id,
                subtask_id=s.subtask_id,
                start=s.start,
                end=s.end,
                proposed_minutes=s.proposed_minutes,
                explanation=s.explanation,
            )
            for s in result.slots
        ],
        warnings=[
            WarningResponse(
                task_id=w.warning.task_id,
                type=w.warning.type,
                needed_minutes=w.warning.needed_minutes,
                free_minutes=w.warning.free_minutes,
                message=w.message,
            )
            for w in result.warnings
        ],
    )
