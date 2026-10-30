"""BackendReaderClient with httpx.MockTransport (no network). T9."""

import asyncio
from datetime import date, datetime
from uuid import UUID

import httpx
import pytest

from metanoia_ai.application.ports.backend_reader import (
    BackendNotFoundError,
    BackendReader,
    BackendReadError,
    ProjectProgress,
    ProjectSummary,
)
from metanoia_ai.infrastructure.backend.reader_client import BackendReaderClient

AUTH = "Bearer user-token"
PROJECT = UUID(int=7)
FOCUS = {
    "sessionId": str(UUID(int=1)),
    "taskId": str(UUID(int=2)),
    "taskTitle": "Viết bài blog",
    "scheduleSlotId": None,
    "startedAt": "2026-10-30T02:00:00+00:00",
    "elapsedSeconds": 1830,
    "serverTime": "2026-10-30T02:30:30+00:00",
    "isOverrun": True,
}
PROJECTS = [
    {"id": str(PROJECT), "name": "Landing page", "status": "Active", "client": None},
    {
        "id": str(UUID(int=8)),
        "name": "Logo",
        "status": "OnHold",
        "client": {"name": "A"},
    },
]
PROGRESS = {
    "projectId": str(PROJECT),
    "name": "Landing page",
    "status": "Active",
    "deadline": "2026-11-15",
    "totalTasks": 8,
    "completedTasks": 3,
    "progressPercent": 37.5,
    "estimatedMinutes": 600,
    "actualMinutes": 240,
    "varianceMinutes": 0,
    "tasks": [],
}


def client_for(handler, seen: list[httpx.Request] | None = None) -> BackendReaderClient:
    def record(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return BackendReaderClient(
        "https://backend.test/",
        timeout_seconds=1,
        transport=httpx.MockTransport(record),
    )


def answer(status: int, body=None):
    return lambda request: (
        httpx.Response(status, json=body)
        if body is not None
        else httpx.Response(status)
    )


def run(coro):
    return asyncio.run(coro)


def test_matches_the_port() -> None:
    reader: BackendReader = client_for(answer(200, []))
    assert reader is not None


# T9: 200, only GET, token passed on
def test_current_focus_200_uses_get_and_passes_the_token() -> None:
    seen: list[httpx.Request] = []

    focus = run(client_for(answer(200, FOCUS), seen).current_focus(AUTH))

    assert focus.task_title == "Viết bài blog"
    assert focus.started_at == datetime.fromisoformat("2026-10-30T02:00:00+00:00")
    assert (focus.elapsed_seconds, focus.is_overrun) == (1830, True)
    [request] = seen
    assert request.method == "GET"
    assert str(request.url) == "https://backend.test/api/focus/current"
    assert request.headers["Authorization"] == AUTH


def test_no_running_timer_is_204() -> None:
    assert run(client_for(answer(204)).current_focus(AUTH)) is None


def test_projects() -> None:
    seen: list[httpx.Request] = []

    projects = run(client_for(answer(200, PROJECTS), seen).projects(AUTH))

    assert projects == [
        ProjectSummary(PROJECT, "Landing page", "Active"),
        ProjectSummary(UUID(int=8), "Logo", "OnHold"),
    ]
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/projects"


def test_project_progress() -> None:
    seen: list[httpx.Request] = []

    progress = run(
        client_for(answer(200, PROGRESS), seen).project_progress(PROJECT, AUTH)
    )

    assert progress == ProjectProgress("Landing page", date(2026, 11, 15), 8, 3, 37.5)
    assert seen[0].url.path == f"/api/projects/{PROJECT}/progress"
    no_deadline = run(
        client_for(answer(200, {**PROGRESS, "deadline": None})).project_progress(
            PROJECT, AUTH
        )
    )
    assert no_deadline.deadline is None


# T9: 404 / 403 → not found
@pytest.mark.parametrize("status", [403, 404])
def test_not_owner_is_not_found(status: int) -> None:
    with pytest.raises(BackendNotFoundError):
        run(
            client_for(answer(status, {"message": "x"})).project_progress(PROJECT, AUTH)
        )


# T9: 401, 500, timeout, bad JSON → read error
@pytest.mark.parametrize("status", [401, 500, 502])
def test_other_statuses_are_read_errors(status: int) -> None:
    with pytest.raises(BackendReadError):
        run(client_for(answer(status, {"message": "x"})).projects(AUTH))


def test_timeout_is_a_read_error() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(BackendReadError):
        run(client_for(slow).current_focus(AUTH))


@pytest.mark.parametrize(
    ("method", "body"),
    [
        ("current_focus", {"taskTitle": "x"}),
        ("projects", [{"id": "not-a-uuid", "name": "x", "status": "Active"}]),
        ("projects", {"items": []}),
        ("project_progress", {"name": "x", "deadline": "soon", "totalTasks": 1}),
        ("project_progress", []),
    ],
)
def test_unexpected_answers_are_read_errors(method: str, body) -> None:
    reader = client_for(answer(200, body))
    args = (PROJECT, AUTH) if method == "project_progress" else (AUTH,)

    with pytest.raises(BackendReadError):
        run(getattr(reader, method)(*args))


def test_not_json_is_a_read_error() -> None:
    reader = client_for(lambda request: httpx.Response(200, text="<html>"))

    with pytest.raises(BackendReadError):
        run(reader.projects(AUTH))
