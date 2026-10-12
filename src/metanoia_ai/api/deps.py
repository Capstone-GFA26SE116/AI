"""Wiring: builds the objects each request needs (DB session, use cases)."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.use_cases.record_task_completion import (
    RecordTaskCompletion,
)
from metanoia_ai.config import Settings, get_settings
from metanoia_ai.infrastructure.db.learning_repository import SqlLearningRepository


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One DB session per request, closed when the request ends."""
    async with request.app.state.session_factory() as session:
        yield session


def get_record_task_completion(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RecordTaskCompletion:
    """FR-17 use case on the real database, with values from config."""
    return RecordTaskCompletion(
        SqlLearningRepository(session),
        alpha=settings.kbias_alpha,
        initial_k=settings.kbias_initial,
        outlier_ratio=settings.kbias_outlier_ratio,
    )
