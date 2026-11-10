"""BackendReader over HTTP (FR-22). Only GET; the user's token is passed on as is.

The Backend answers in camelCase JSON. Anything unexpected becomes BackendReadError,
so one bad source only spoils that one answer (#13). Tokens are never logged.
"""

import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

import httpx

from metanoia_ai.application.ports.backend_reader import (
    BackendNotFoundError,
    BackendReadError,
    MilestoneSummary,
    PendingProposal,
    PendingSession,
    ProjectProgress,
    ProjectSummary,
    RunningFocus,
    UnreadNotice,
)

logger = logging.getLogger(__name__)

# 403: the Backend's owner check. Treated as "not found" so ids cannot be probed.
NOT_FOUND_STATUSES = {403, 404}
# Enough to count what is new; only the first ones are read aloud.
UNREAD_NOTIFICATIONS_TAKE = 20


class BackendReaderClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,  # tests only
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self.transport = transport

    async def _get(
        self, path: str, auth_header: str, params: dict[str, str] | None = None
    ) -> Any:
        """GET path → parsed JSON; None for 204. The only HTTP method used here."""
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.get(
                    f"{self.base_url}{path}",
                    params=params,
                    headers={"Authorization": auth_header},
                )
        except httpx.HTTPError as error:  # timeout, connection, protocol
            logger.warning("Backend GET %s failed: %s", path, type(error).__name__)
            raise BackendReadError(type(error).__name__) from error
        if response.status_code in NOT_FOUND_STATUSES:
            raise BackendNotFoundError(path)
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            logger.warning(
                "Backend GET %s answered HTTP %d", path, response.status_code
            )
            raise BackendReadError(f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise BackendReadError("not JSON") from error

    async def current_focus(self, auth_header: str) -> RunningFocus | None:
        body = await self._get("/api/focus/current", auth_header)
        if body is None:
            return None
        try:
            return RunningFocus(
                task_title=str(body["taskTitle"]),
                started_at=datetime.fromisoformat(body["startedAt"]),
                elapsed_seconds=int(body["elapsedSeconds"]),
                is_overrun=bool(body["isOverrun"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise BackendReadError("unexpected focus answer") from error

    async def projects(self, auth_header: str) -> list[ProjectSummary]:
        body = await self._get("/api/projects", auth_header)
        try:
            return [
                ProjectSummary(UUID(p["id"]), str(p["name"]), str(p["status"]))
                for p in body
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise BackendReadError("unexpected projects answer") from error

    async def project_progress(
        self, project_id: UUID, auth_header: str
    ) -> ProjectProgress:
        body = await self._get(f"/api/projects/{project_id}/progress", auth_header)
        try:
            deadline = body.get("deadline")
            return ProjectProgress(
                name=str(body["name"]),
                deadline=None if deadline is None else date.fromisoformat(deadline),
                total_tasks=int(body["totalTasks"]),
                completed_tasks=int(body["completedTasks"]),
                progress_percent=float(body["progressPercent"]),
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise BackendReadError("unexpected progress answer") from error

    async def pending_confirmations(self, auth_header: str) -> list[PendingSession]:
        body = await self._get("/api/focus/pending-confirmation", auth_header)
        try:
            return [
                PendingSession(str(s["taskTitle"]), round(float(s["systemMinutes"])))
                for s in body
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise BackendReadError("unexpected pending sessions answer") from error

    async def unread_notifications(self, auth_header: str) -> list[UnreadNotice]:
        body = await self._get(
            "/api/notifications",
            auth_header,
            {"unreadOnly": "true", "take": str(UNREAD_NOTIFICATIONS_TAKE)},
        )
        try:
            return [UnreadNotice(str(n["title"])) for n in body if not n["isRead"]]
        except (KeyError, TypeError, ValueError) as error:
            raise BackendReadError("unexpected notifications answer") from error

    async def pending_proposal(self, auth_header: str) -> PendingProposal | None:
        body = await self._get("/api/schedule/proposals/pending", auth_header)
        if body is None:
            return None
        try:
            starts = sorted(
                datetime.fromisoformat(slot["startTime"]) for slot in body["slots"]
            )
            if any(start.tzinfo is None for start in starts):
                raise ValueError("slot time without a time zone")
            missing = body.get("missingMinutes")
            return PendingProposal(
                slot_starts=tuple(starts),
                is_feasible=bool(body["isFeasible"]),
                missing_minutes=None if missing is None else int(missing),
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise BackendReadError("unexpected proposal answer") from error

    async def milestones(
        self, project_id: UUID, auth_header: str
    ) -> list[MilestoneSummary]:
        body = await self._get(f"/api/projects/{project_id}/milestones", auth_header)
        try:
            return [
                MilestoneSummary(
                    name=str(m["name"]),
                    due_date=None
                    if m.get("dueDate") is None
                    else date.fromisoformat(m["dueDate"]),
                    status=str(m["status"]),
                )
                for m in body
            ]
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise BackendReadError("unexpected milestones answer") from error
