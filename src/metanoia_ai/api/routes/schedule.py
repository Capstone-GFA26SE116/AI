"""FR-10 and FR-16b endpoints.

/propose: called by the Backend when the user clicks "Schedule".
/overrun: called by the Mobile app / Frontend when a slot runs over and the user needs
more time. It only proposes; the client applies it through the Backend (plan #2).
"""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AwareDatetime, BaseModel, Field

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import (
    get_catch_up,
    get_insert_block,
    get_plan_overrun,
    get_postpone_slot,
    get_propose_schedule,
)
from metanoia_ai.application.use_cases.plan_overrun import (
    OverrunRequest,
    OverrunResult,
    PlanOverrun,
    SlotNotFoundError,
    SlotNotLateError,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    ProposalResult,
    ProposeSchedule,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.application.use_cases.reshuffle import (
    BlockInvalidError,
    CatchUp,
    CatchUpResult,
    InsertBlock,
    InsertResult,
    PostponeResult,
    PostponeSlot,
    SlotNotMovableError,
)
from metanoia_ai.config import Settings, get_settings
from metanoia_ai.domain.models import Language, WarningType
from metanoia_ai.domain.overrun_plan import OverrunKind, SlotMove
from metanoia_ai.domain.reshuffle import Choice, ConflictKind, Why

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
    needed_minutes: int | None  # day_overloaded: work minutes on that day
    free_minutes: int | None
    day: date | None = None  # FR-10d: the overloaded day
    capacity_minutes: int | None = None  # FR-10d: what the user usually manages
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
                day=w.warning.day,
                capacity_minutes=w.warning.capacity_minutes,
                message=w.message,
            )
            for w in result.warnings
        ],
    )


# --- FR-16b ---


class OverrunBody(BaseModel):
    slot_id: UUID
    extra_minutes: int = Field(ge=5)  # upper limit from config
    language: Language = Language.VI


class ExtendedResponse(BaseModel):
    slot_id: UUID
    start: datetime  # ISO 8601 in the user's time zone
    end: datetime


class MoveResponse(BaseModel):
    slot_id: UUID
    task_title: str
    old_start: datetime
    old_end: datetime
    new_start: datetime
    new_end: datetime


class NotPlacedResponse(BaseModel):
    slot_id: UUID
    task_title: str


class OverrunResponse(BaseModel):
    kind: OverrunKind
    extended: ExtendedResponse
    moves: list[MoveResponse]  # empty when extending
    not_placed: list[NotPlacedResponse]  # would miss the task deadline
    past_day_end: bool
    summary: str  # one sentence for the screen and the 3D assistant


@router.post("/overrun")
async def overrun(
    body: OverrunBody,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    use_case: Annotated[PlanOverrun, Depends(get_plan_overrun)],
) -> OverrunResponse:
    """Propose to extend the late slot or to move the slots after it. Writes nothing."""
    if body.extra_minutes > settings.overrun_extra_minutes_max:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"extra_minutes must be at most {settings.overrun_extra_minutes_max}",
        )
    request = OverrunRequest(user_id, body.slot_id, body.extra_minutes, body.language)
    try:
        result = await use_case.execute(request)
    except (SlotNotFoundError, UserNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Slot not found"
        ) from error
    except SlotNotLateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This slot cannot run over (finished, cancelled, proposed or Fixed)",
        ) from error
    return to_overrun_response(result)


def to_overrun_response(result: OverrunResult) -> OverrunResponse:
    zone = ZoneInfo(result.time_zone)
    plan = result.plan
    return OverrunResponse(
        kind=plan.kind,
        extended=ExtendedResponse(
            slot_id=result.slot_id,
            start=plan.extended.start.astimezone(zone),
            end=plan.extended.end.astimezone(zone),
        ),
        moves=[
            MoveResponse(
                slot_id=m.slot_id,
                task_title=m.title,
                old_start=m.old.start.astimezone(zone),
                old_end=m.old.end.astimezone(zone),
                new_start=m.new.start.astimezone(zone),
                new_end=m.new.end.astimezone(zone),
            )
            for m in plan.moves
        ],
        not_placed=[
            NotPlacedResponse(slot_id=n.slot_id, task_title=n.title)
            for n in plan.not_placed
        ],
        past_day_end=plan.past_day_end,
        summary=result.summary,
    )


# --- FR-22d: urgent block (M3, M4), postpone (M7), catch up (M10) ---


class InsertBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: AwareDatetime
    end: AwareDatetime
    language: Language = Language.VI


class BlockResponse(BaseModel):
    title: str
    start: datetime
    end: datetime


class TimeResponse(BaseModel):
    start: datetime
    end: datetime


class ConflictResponse(BaseModel):
    kind: ConflictKind
    id: UUID | None  # Fixed task id, or slot id; None for a Google event
    title: str
    start: datetime
    end: datetime
    important: bool
    why: Why


class InsertResponse(BaseModel):
    block: BlockResponse
    past_day_end: bool
    project_id: UUID | None  # where the app creates the Fixed task (null → ask)
    domain_id: UUID | None  # the kind of work for it (null → ask)
    moves: list[MoveResponse]  # old + new times: apply, and keep for undo (M18)
    not_placed: list[NotPlacedResponse]
    conflicts: list[ConflictResponse]
    options: list[Choice]  # empty without conflicts
    recommendation: Choice | None
    other_time: TimeResponse | None
    skip_task_ids: list[UUID]  # overwrite: set these Fixed tasks to Skipped
    summary: str


class PostponeBody(BaseModel):
    slot_id: UUID
    language: Language = Language.VI


class PostponeResponse(BaseModel):
    slot_id: UUID
    task_title: str
    move: MoveResponse | None
    not_placed: bool  # no free time before the task deadline
    summary: str


class CatchUpBody(BaseModel):
    language: Language = Language.VI


class CatchUpMoveResponse(MoveResponse):
    task_id: UUID | None
    subtask_id: UUID | None


class CatchUpResponse(BaseModel):
    moves: list[CatchUpMoveResponse]
    not_placed: list[NotPlacedResponse]
    summary: str


def to_move(m: SlotMove, zone: ZoneInfo) -> MoveResponse:
    return MoveResponse(
        slot_id=m.slot_id,
        task_title=m.title,
        old_start=m.old.start.astimezone(zone),
        old_end=m.old.end.astimezone(zone),
        new_start=m.new.start.astimezone(zone),
        new_end=m.new.end.astimezone(zone),
    )


def to_insert_response(result: InsertResult) -> InsertResponse:
    zone = ZoneInfo(result.time_zone)
    plan = result.plan
    return InsertResponse(
        block=BlockResponse(
            title=result.title,
            start=plan.block.start.astimezone(zone),
            end=plan.block.end.astimezone(zone),
        ),
        past_day_end=plan.past_day_end,
        project_id=result.project_id,
        domain_id=result.domain_id,
        moves=[to_move(m, zone) for m in plan.moves],
        not_placed=[
            NotPlacedResponse(slot_id=n.slot_id, task_title=n.title)
            for n in plan.not_placed
        ],
        conflicts=[
            ConflictResponse(
                kind=c.blocker.kind,
                id=c.blocker.ref_id,
                title=c.blocker.title,
                start=c.blocker.time.start.astimezone(zone),
                end=c.blocker.time.end.astimezone(zone),
                important=c.important,
                why=c.why,
            )
            for c in plan.conflicts
        ],
        options=list(plan.options),
        recommendation=plan.recommendation,
        other_time=None
        if plan.other_time is None
        else TimeResponse(
            start=plan.other_time.start.astimezone(zone),
            end=plan.other_time.end.astimezone(zone),
        ),
        skip_task_ids=list(plan.skip_task_ids),
        summary=result.summary,
    )


def to_postpone_response(result: PostponeResult) -> PostponeResponse:
    zone = ZoneInfo(result.time_zone)
    return PostponeResponse(
        slot_id=result.slot_id,
        task_title=result.title,
        move=None if result.move is None else to_move(result.move, zone),
        not_placed=result.move is None,
        summary=result.summary,
    )


def to_catch_up_response(result: CatchUpResult) -> CatchUpResponse:
    zone = ZoneInfo(result.time_zone)
    return CatchUpResponse(
        moves=[
            CatchUpMoveResponse(
                **to_move(m.move, zone).model_dump(),
                task_id=m.task_id,
                subtask_id=m.subtask_id,
            )
            for m in result.moves
        ],
        not_placed=[
            NotPlacedResponse(slot_id=n.slot_id, task_title=n.title)
            for n in result.not_placed
        ],
        summary=result.summary,
    )


@router.post("/insert-block")
async def insert_block(
    body: InsertBody,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[InsertBlock, Depends(get_insert_block)],
) -> InsertResponse:
    """M3 / M4: urgent work from start to end → what moves, what is in the way.
    Writes nothing, no AI call; the app applies it through the Backend."""
    try:
        result = await use_case.execute(
            user_id, body.title.strip(), body.start, body.end, body.language
        )
    except BlockInvalidError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except UserNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from error
    return to_insert_response(result)


@router.post("/postpone")
async def postpone(
    body: PostponeBody,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[PostponeSlot, Depends(get_postpone_slot)],
) -> PostponeResponse:
    """M7: move one slot to the next working day. Writes nothing, no AI call."""
    try:
        result = await use_case.execute(user_id, body.slot_id, body.language)
    except (SlotNotFoundError, UserNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Slot not found"
        ) from error
    except SlotNotMovableError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This slot cannot move (started, finished, cancelled, Fixed or group)",
        ) from error
    return to_postpone_response(result)


@router.post("/catch-up")
async def catch_up(
    body: CatchUpBody,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[CatchUp, Depends(get_catch_up)],
) -> CatchUpResponse:
    """M10: place missed slots again from now. Writes nothing, no AI call."""
    try:
        result = await use_case.execute(user_id, body.language)
    except UserNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from error
    return to_catch_up_response(result)
