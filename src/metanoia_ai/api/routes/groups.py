"""FR-23 endpoint: suggest who should do a group task (Work Group, Backend PR #15).

Called by the Web / Mobile app when the Leader opens a group task. It only suggests;
the Leader assigns through the Backend (PUT /api/group-tasks/{id}/assignee).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_suggest_group_assignee
from metanoia_ai.application.use_cases.suggest_group_assignee import (
    GroupArchivedError,
    GroupTaskClosedError,
    GroupTaskNotFoundError,
    NotGroupLeaderError,
    ReasonSkip,
    SuggestGroupAssignee,
    SuggestionResult,
)
from metanoia_ai.domain.assignee_ranking import GroupRole
from metanoia_ai.domain.models import Language

router = APIRouter(prefix="/groups", tags=["groups"])


class SuggestRequest(BaseModel):
    domain_id: UUID | None = None  # the work category the Leader picks; optional
    language: Language = Language.VI


class SuggestionResponse(BaseModel):
    user_id: UUID
    role: GroupRole
    rank: int  # 1 = best
    is_current_assignee: bool
    busy_at_fixed_time: bool
    free_minutes: int  # free work time before the deadline, after other work
    group_work_minutes: int  # open group work in that time
    fits: bool | None  # None: the task has no estimate
    reason: str | None


class SuggestResponse(BaseModel):
    task_id: UUID
    suggestions: list[SuggestionResponse]
    reasons_skipped: ReasonSkip | None  # why reasons are missing, if they are


@router.post("/tasks/{group_task_id}/assignee-suggestions")
async def suggest_assignee(
    group_task_id: UUID,
    body: SuggestRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[SuggestGroupAssignee, Depends(get_suggest_group_assignee)],
) -> SuggestResponse:
    """Rank the Leaders and Members for this task. Writes nothing to the group."""
    try:
        result = await use_case.execute(
            user_id, group_task_id, body.domain_id, body.language
        )
    except GroupTaskNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group task not found"
        ) from error
    except NotGroupLeaderError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a Leader of the group can ask for suggestions",
        ) from error
    except GroupArchivedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="The group is archived"
        ) from error
    except GroupTaskClosedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The task is already Done or Skipped",
        ) from error
    return to_response(result)


def to_response(result: SuggestionResult) -> SuggestResponse:
    return SuggestResponse(
        task_id=result.task_id,
        suggestions=[
            SuggestionResponse(
                user_id=s.user_id,
                role=s.role,
                rank=s.rank,
                is_current_assignee=s.is_current_assignee,
                busy_at_fixed_time=s.busy_at_fixed_time,
                free_minutes=s.free_minutes,
                group_work_minutes=s.group_work_minutes,
                fits=s.fits,
                reason=s.reason,
            )
            for s in result.suggestions
        ],
        reasons_skipped=result.reasons_skipped,
    )
