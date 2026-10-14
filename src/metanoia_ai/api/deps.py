"""Wiring: builds the objects each request needs (DB session, use cases)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.ports.llm_client import LLMClient
from metanoia_ai.application.reason_writer import ReasonWriter
from metanoia_ai.application.use_cases.propose_schedule import ProposeSchedule
from metanoia_ai.application.use_cases.record_task_completion import (
    RecordTaskCompletion,
)
from metanoia_ai.config import Settings, get_settings
from metanoia_ai.infrastructure.db.learning_repository import SqlLearningRepository
from metanoia_ai.infrastructure.db.quota_repository import SqlQuotaRepository
from metanoia_ai.infrastructure.db.schedule_repository import SqlScheduleRepository
from metanoia_ai.infrastructure.llm.gemini_client import GeminiClient


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


def get_llm_client(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> LLMClient:
    """One Gemini client for the whole app, made on first use.

    Missing LLM settings fail here (MissingLlmSettingError), not at startup,
    so features without the LLM keep working.
    """
    client = getattr(request.app.state, "llm_client", None)
    if client is None:
        llm = settings.llm_config()
        client = GeminiClient(llm.api_key, llm.model)
        request.app.state.llm_client = client
    return client


def get_propose_schedule(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    llm_client: Annotated[LLMClient, Depends(get_llm_client)],
) -> ProposeSchedule:
    """FR-10 use case on the real database and Gemini, with values from config."""
    llm = settings.llm_config()
    return ProposeSchedule(
        SqlScheduleRepository(session),
        SqlQuotaRepository(session),  # same session → same transaction
        ReasonWriter(
            llm_client,
            max_retries=llm.max_retries,
            input_price_per_1m=llm.input_price_per_1m,
            output_price_per_1m=llm.output_price_per_1m,
        ),
        initial_k=Decimal(str(settings.kbias_initial)),
        window_days=settings.schedule_window_days,
        min_chunk_minutes=settings.schedule_min_chunk_minutes,
        round_minutes=settings.schedule_round_minutes,
        top_productive_hours=settings.schedule_top_productive_hours,
        quota_daily_max=settings.quota_daily_max,
        clock=lambda: datetime.now(UTC),
    )
