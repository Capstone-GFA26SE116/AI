"""FR-17 endpoint. Called by the Backend after it saves a task as Done."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_record_task_completion
from metanoia_ai.application.use_cases.record_task_completion import (
    RecordTaskCompletion,
    TaskNotFoundError,
)
from metanoia_ai.domain.models import SkipReason

router = APIRouter(prefix="/learning", tags=["learning"])


class TaskCompletedRequest(BaseModel):
    task_id: UUID


class LearningResponse(BaseModel):
    learned: bool
    reason: SkipReason | None = None
    k_old: float | None = None
    k_new: float | None = None
    sample_index: int | None = None


@router.post("/task-completed")
async def task_completed(
    body: TaskCompletedRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[RecordTaskCompletion, Depends(get_record_task_completion)],
) -> LearningResponse:
    """Update the user's K_bias from a finished task. Safe to call more than once."""
    try:
        result = await use_case.execute(user_id, body.task_id)
    except TaskNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        ) from error
    return LearningResponse(**asdict(result))
