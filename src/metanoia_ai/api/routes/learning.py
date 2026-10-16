"""Learning endpoints, called by the Backend.

FR-17 + FR-19: after it saves a task as Done or Skipped.
FR-18: after each timer Stop (without waiting for the answer).
"""

import logging
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import (
    EmbedMemory,
    get_memory_embedder,
    get_recompute_productive_hours,
    get_record_task_completion,
    get_save_task_memory,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    RecomputeProductiveHours,
    UserNotFoundError,
)
from metanoia_ai.application.use_cases.record_task_completion import (
    RecordTaskCompletion,
    TaskNotFoundError,
)
from metanoia_ai.application.use_cases.save_task_memory import (
    MemoryStatus,
    SaveMemoryResult,
    SaveTaskMemory,
)
from metanoia_ai.domain.models import SkipReason

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/learning", tags=["learning"])


class TaskCompletedRequest(BaseModel):
    task_id: UUID


class LearningResponse(BaseModel):
    learned: bool
    reason: SkipReason | None = None
    k_old: float | None = None
    k_new: float | None = None
    sample_index: int | None = None
    memory: MemoryStatus  # FR-19: queued / skipped / failed


async def save_memory(
    use_case: SaveTaskMemory, user_id: UUID, task_id: UUID
) -> SaveMemoryResult:
    """FR-19 part. Any error → failed, so the FR-17 result is never lost (AC2)."""
    try:
        return await use_case.execute(user_id, task_id)
    except Exception:
        logger.exception("Saving the memory of task %s failed", task_id)
        return SaveMemoryResult(MemoryStatus.FAILED)


@router.post("/task-completed")
async def task_completed(
    body: TaskCompletedRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[RecordTaskCompletion, Depends(get_record_task_completion)],
    memory_use_case: Annotated[SaveTaskMemory, Depends(get_save_task_memory)],
    embed_memory: Annotated[EmbedMemory, Depends(get_memory_embedder)],
    background_tasks: BackgroundTasks,
) -> LearningResponse:
    """Update K_bias (FR-17), then save the task's memory (FR-19).

    The vector is made after the response is sent. Safe to call more than once.
    """
    try:
        result = await use_case.execute(user_id, body.task_id)
    except TaskNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        ) from error

    memory = await save_memory(memory_use_case, user_id, body.task_id)
    if memory.status is MemoryStatus.QUEUED and memory.memory_id is not None:
        background_tasks.add_task(embed_memory, user_id, memory.memory_id)
    return LearningResponse(**asdict(result), memory=memory.status)


class RecomputeResponse(BaseModel):
    hours_updated: int  # 24, or 0 when nothing was written
    sessions_used: int


@router.post("/productive-hours/recompute")
async def recompute_productive_hours(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[
        RecomputeProductiveHours, Depends(get_recompute_productive_hours)
    ],
) -> RecomputeResponse:
    """FR-18: recompute the 24 hours from scratch. Safe to call more than once."""
    try:
        result = await use_case.execute(user_id)
    except UserNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from error
    return RecomputeResponse(**asdict(result))
