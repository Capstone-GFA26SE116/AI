"""FastAPI app for the METANOIA AI service."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import embed_waiting_memories
from metanoia_ai.api.routes import learning, schedule
from metanoia_ai.config import get_settings
from metanoia_ai.infrastructure.db.engine import create_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: load settings (a missing value stops the app), set up the DB pool,
    and start the memory scan in the background (FR-19, does not delay startup).

    Shutdown: stop the scan if still running, close all DB connections.
    """
    settings = get_settings()
    engine = create_engine(settings)
    app.state.session_factory = create_session_factory(engine)
    scan = asyncio.create_task(embed_waiting_memories(app.state, settings))
    yield
    scan.cancel()
    with suppress(asyncio.CancelledError):
        await scan
    await engine.dispose()


app = FastAPI(title="METANOIA AI", lifespan=lifespan)
app.include_router(learning.router)
app.include_router(schedule.router)


class HealthResponse(BaseModel):
    status: str


class MeResponse(BaseModel):
    user_id: UUID


@app.get("/health")
def health() -> HealthResponse:
    """Check that the service is running. No token needed."""
    return HealthResponse(status="ok")


@app.get("/me")
def me(user_id: Annotated[UUID, Depends(get_current_user_id)]) -> MeResponse:
    """Return the caller's user ID. Used to check that Backend tokens work here."""
    return MeResponse(user_id=user_id)
