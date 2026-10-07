"""FastAPI app for the METANOIA AI service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load settings at startup, so a missing value stops the app right away."""
    get_settings()
    yield


app = FastAPI(title="METANOIA AI", lifespan=lifespan)


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
