"""FR-09 endpoint: similar past tasks. For the Frontend ("similar tasks before") and demos."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_find_similar_tasks
from metanoia_ai.application.use_cases.find_similar_tasks import (
    FindSimilarTasks,
    SearchMode,
    SimilarTasksResult,
)
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.config import Settings, get_settings
from metanoia_ai.domain.similar_tasks import MatchSource, SimilarOutcome

router = APIRouter(prefix="/memory", tags=["memory"])


class SimilarItemResponse(BaseModel):
    task_id: UUID
    title: str  # as the user wrote it; mask before sending to an LLM (#9)
    domain: str
    outcome: SimilarOutcome
    estimated_minutes: int
    actual_minutes: int
    ratio: float | None
    finished_at: datetime | None  # ISO 8601 in the user's time zone
    score: float
    matched_by: list[MatchSource]


class SummaryResponse(BaseModel):
    count: int
    done_count: int
    average_ratio: float | None


class SimilarResponse(BaseModel):
    task_id: UUID
    search_mode: SearchMode
    items: list[SimilarItemResponse]
    summary: SummaryResponse


def to_response(result: SimilarTasksResult) -> SimilarResponse:
    zone = result.time_zone
    return SimilarResponse(
        task_id=result.task_id,
        search_mode=result.search_mode,
        items=[
            SimilarItemResponse(
                task_id=item.task.task_id,
                title=item.task.title,
                domain=item.task.domain_name,
                outcome=item.task.outcome,
                estimated_minutes=item.task.estimated_minutes,
                actual_minutes=item.task.actual_minutes,
                ratio=item.ratio,
                finished_at=item.task.finished_at.astimezone(zone)
                if item.task.finished_at
                else None,
                score=item.score,
                matched_by=list(item.matched_by),
            )
            for item in result.items
        ],
        summary=SummaryResponse(
            count=result.summary.count,
            done_count=result.summary.done_count,
            average_ratio=result.summary.average_ratio,
        ),
    )


@router.get("/similar")
async def similar(
    task_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[FindSimilarTasks, Depends(get_find_similar_tasks)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int | None, Query(ge=1, le=10)] = None,
) -> SimilarResponse:
    """Up to `limit` past tasks of this user most like the given task. No quota used."""
    try:
        result = await use_case.execute(
            user_id, task_id, limit or settings.similar_default_limit
        )
    except TaskNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        ) from error
    return to_response(result)
