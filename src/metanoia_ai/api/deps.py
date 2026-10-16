"""Wiring: builds the objects each request needs (DB session, use cases)."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from metanoia_ai.application.chat_writer import ChatWriter
from metanoia_ai.application.ports.embedding_client import EmbeddingClient
from metanoia_ai.application.ports.llm_client import LLMClient
from metanoia_ai.application.project_chat_writer import ProjectChatWriter
from metanoia_ai.application.reason_writer import ReasonWriter
from metanoia_ai.application.use_cases.apply_project_breakdown import (
    ApplyProjectBreakdown,
)
from metanoia_ai.application.use_cases.apply_task_breakdown import (
    ApplyTaskBreakdown,
)
from metanoia_ai.application.use_cases.embed_pending_memories import (
    EmbedPendingMemories,
)
from metanoia_ai.application.use_cases.find_similar_tasks import FindSimilarTasks
from metanoia_ai.application.use_cases.get_project_chat import GetProjectChat
from metanoia_ai.application.use_cases.get_task_chat import GetTaskChat
from metanoia_ai.application.use_cases.propose_schedule import ProposeSchedule
from metanoia_ai.application.use_cases.record_task_completion import (
    RecordTaskCompletion,
)
from metanoia_ai.application.use_cases.save_task_memory import SaveTaskMemory
from metanoia_ai.application.use_cases.send_project_chat_message import (
    SendProjectChatMessage,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    SendTaskChatMessage,
)
from metanoia_ai.application.use_cases.start_project_chat import (
    ListProjectChats,
    StartProjectChat,
)
from metanoia_ai.config import MissingLlmSettingError, Settings, get_settings
from metanoia_ai.domain.project_breakdown import ProjectLimits
from metanoia_ai.domain.task_breakdown import DraftLimits
from metanoia_ai.infrastructure.db.chat_repository import SqlChatRepository
from metanoia_ai.infrastructure.db.learning_repository import SqlLearningRepository
from metanoia_ai.infrastructure.db.memory_repository import SqlMemoryRepository
from metanoia_ai.infrastructure.db.project_chat_repository import (
    SqlProjectChatRepository,
)
from metanoia_ai.infrastructure.db.quota_repository import SqlQuotaRepository
from metanoia_ai.infrastructure.db.schedule_repository import SqlScheduleRepository
from metanoia_ai.infrastructure.db.similar_task_repository import (
    SqlSimilarTaskRepository,
)
from metanoia_ai.infrastructure.llm.gemini_client import GeminiClient
from metanoia_ai.infrastructure.llm.gemini_embedding_client import (
    GeminiEmbeddingClient,
)

logger = logging.getLogger(__name__)

# Background job of one memory: (user id, memory id). Never raises.
EmbedMemory = Callable[[UUID, UUID], Awaitable[None]]


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


def get_save_task_memory(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SaveTaskMemory:
    """FR-19: save the memory row. Works without embedding settings (plan #19)."""
    return SaveTaskMemory(
        SqlMemoryRepository(session),
        model=settings.embedding_model,
        max_chars=settings.memory_text_max_chars,
    )


def get_embedding_client(app_state: Any, settings: Settings) -> EmbeddingClient:
    """One Gemini embedding client for the whole app, made on first use.

    Missing settings raise MissingLlmSettingError (names only, never values).
    """
    client = getattr(app_state, "embedding_client", None)
    if client is None:
        config = settings.embedding_config()
        client = GeminiEmbeddingClient(
            config.api_key, config.model, dimensions=config.dimensions
        )
        app_state.embedding_client = client
    return client


def build_embedder(
    session: AsyncSession, settings: Settings, client: EmbeddingClient
) -> EmbedPendingMemories:
    config = settings.embedding_config()
    return EmbedPendingMemories(
        SqlMemoryRepository(session),
        SqlQuotaRepository(session),  # same session, its own short transactions
        client,
        dimensions=config.dimensions,
        price_per_1m=config.price_per_1m,
        now=lambda: datetime.now(UTC),
    )


async def embed_memory(
    app_state: Any, settings: Settings, user_id: UUID, memory_id: UUID
) -> None:
    """Background job after a request. Opens its own session (plan #17); never raises."""
    try:
        client = get_embedding_client(app_state, settings)
        async with app_state.session_factory() as session:
            await build_embedder(session, settings, client).embed_one(
                user_id, memory_id
            )
    except MissingLlmSettingError as error:
        logger.warning("Missing %s: memory %s stays Pending", error, memory_id)
    except Exception:
        logger.exception("Embedding memory %s failed", memory_id)


async def embed_waiting_memories(app_state: Any, settings: Settings) -> None:
    """Startup scan (plan #20): embed Pending/Failed rows. Never raises."""
    try:
        client = get_embedding_client(app_state, settings)
        async with app_state.session_factory() as session:
            outcomes = await build_embedder(session, settings, client).embed_waiting()
        logger.info("Startup memory scan: %d rows handled", len(outcomes))
    except MissingLlmSettingError as error:
        logger.warning("Missing %s: startup memory scan skipped", error)
    except Exception:
        logger.exception("Startup memory scan failed")


def get_memory_embedder(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> EmbedMemory:
    """The background job, bound to this app (not to the request's session)."""
    return partial(embed_memory, request.app.state, settings)


def get_find_similar_tasks(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FindSimilarTasks:
    """FR-09 use case. Without embedding settings it searches text only (plan #6)."""
    try:
        client: EmbeddingClient | None = get_embedding_client(
            request.app.state, settings
        )
    except MissingLlmSettingError as error:
        logger.warning("Missing %s: similar task search uses text only", error)
        client = None
    return FindSimilarTasks(
        SqlSimilarTaskRepository(session),
        SqlQuotaRepository(session),  # same session, its own short transaction
        client,
        dimensions=settings.embedding_dimensions,
        price_per_1m=settings.embedding_price_per_1m,
        min_similarity=settings.similar_min_similarity,
        rrf_k=settings.similar_rrf_k,
        branch_limit=settings.similar_branch_limit,
        embed_timeout_seconds=settings.similar_embed_timeout_seconds,
        now=lambda: datetime.now(UTC),
    )


def ai_draft_limits(settings: Settings) -> DraftLimits:
    """Plan #9: what an AI draft may contain."""
    return DraftLimits(
        min_subtasks=settings.breakdown_min_subtasks,
        max_subtasks=settings.breakdown_max_subtasks,
        min_minutes=settings.breakdown_min_minutes,
        max_minutes=settings.breakdown_max_minutes,
    )


def manual_draft_limits(settings: Settings) -> DraftLimits:
    """Plan #19: a list edited by hand at Apply may be looser (1 subtask, 1 minute)."""
    return DraftLimits(
        min_subtasks=1,
        max_subtasks=settings.breakdown_max_subtasks,
        min_minutes=1,
        max_minutes=settings.breakdown_manual_max_minutes,
    )


def get_chat_llm_client(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> LLMClient:
    """Gemini for the task chat: same model, longer wait than FR-10 (plan #31)."""
    client = getattr(request.app.state, "chat_llm_client", None)
    if client is None:
        llm = settings.llm_config()
        client = GeminiClient(
            llm.api_key, llm.model, timeout_seconds=settings.chat_llm_timeout_seconds
        )
        request.app.state.chat_llm_client = client
    return client


def get_send_task_chat_message(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    llm_client: Annotated[LLMClient, Depends(get_chat_llm_client)],
    find_similar: Annotated[FindSimilarTasks, Depends(get_find_similar_tasks)],
) -> SendTaskChatMessage:
    """FR-08a: one chat message → Gemini. Shares the request's session."""
    llm = settings.llm_config()
    return SendTaskChatMessage(
        SqlChatRepository(session),
        SqlQuotaRepository(session),
        ChatWriter(
            llm_client,
            limits=ai_draft_limits(settings),
            max_retries=llm.max_retries,
            input_price_per_1m=llm.input_price_per_1m,
            output_price_per_1m=llm.output_price_per_1m,
        ),
        find_similar,
        initial_k=Decimal(str(settings.kbias_initial)),
        history_limit=settings.chat_history_limit,
        max_message_chars=settings.chat_message_max_chars,
        similar_limit=settings.similar_default_limit,
        warning_ratio=settings.breakdown_warning_ratio,
        quota_daily_max=settings.quota_daily_max,
        now=lambda: datetime.now(UTC),
    )


def get_task_chat(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GetTaskChat:
    return GetTaskChat(SqlChatRepository(session))


def get_apply_task_breakdown(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ApplyTaskBreakdown:
    return ApplyTaskBreakdown(SqlChatRepository(session), manual_draft_limits(settings))


# --- project chat (FR-08b-1) ---


def project_limits(settings: Settings, *, manual: bool) -> ProjectLimits:
    """#7 for AI drafts; #21 (more minutes allowed) for edits at Apply."""
    return ProjectLimits(
        max_milestones=settings.project_max_milestones,
        max_tasks_per_milestone=settings.project_max_tasks_per_milestone,
        max_tasks=settings.project_max_tasks,
        min_minutes=settings.project_manual_min_minutes
        if manual
        else settings.project_task_min_minutes,
        max_minutes=settings.project_manual_max_minutes
        if manual
        else settings.project_task_max_minutes,
        max_milestone_title_chars=settings.project_milestone_title_max_chars,
    )


def get_project_chat_llm_client(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> LLMClient:
    """Gemini for the project chat: same model, longer wait for big drafts (#23)."""
    client = getattr(request.app.state, "project_chat_llm_client", None)
    if client is None:
        llm = settings.llm_config()
        client = GeminiClient(
            llm.api_key,
            llm.model,
            timeout_seconds=settings.chat_project_llm_timeout_seconds,
        )
        request.app.state.project_chat_llm_client = client
    return client


def get_start_project_chat(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StartProjectChat:
    return StartProjectChat(
        SqlProjectChatRepository(session), now=lambda: datetime.now(UTC)
    )


def get_list_project_chats(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ListProjectChats:
    return ListProjectChats(SqlProjectChatRepository(session))


def get_send_project_chat_message(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    llm_client: Annotated[LLMClient, Depends(get_project_chat_llm_client)],
) -> SendProjectChatMessage:
    """FR-08b-1: one chat message → Gemini. Shares the request's session."""
    llm = settings.llm_config()
    return SendProjectChatMessage(
        SqlProjectChatRepository(session),
        SqlQuotaRepository(session),
        ProjectChatWriter(
            llm_client,
            limits=project_limits(settings, manual=False),
            max_retries=settings.chat_project_max_retries,
            input_price_per_1m=llm.input_price_per_1m,
            output_price_per_1m=llm.output_price_per_1m,
            retry_budget_seconds=settings.chat_project_retry_budget_seconds,
        ),
        initial_k=Decimal(str(settings.kbias_initial)),
        history_limit=settings.chat_project_history_limit,
        max_message_chars=settings.chat_project_message_max_chars,
        max_prompt_chars=settings.chat_project_prompt_max_chars,
        existing_titles_limit=settings.chat_project_existing_titles_limit,
        daily_work_hours=settings.chat_daily_work_hours,
        quota_daily_max=settings.quota_daily_max,
        now=lambda: datetime.now(UTC),
    )


def get_get_project_chat(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GetProjectChat:
    return GetProjectChat(SqlProjectChatRepository(session))


def get_apply_project_breakdown(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ApplyProjectBreakdown:
    return ApplyProjectBreakdown(
        SqlProjectChatRepository(session),
        project_limits(settings, manual=True),
        now=lambda: datetime.now(UTC),
    )
