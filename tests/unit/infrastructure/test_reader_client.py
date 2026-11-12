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
    MilestoneSummary,
    PendingSession,
    ProjectProgress,
    ProjectSummary,
    UnreadNotice,
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
PENDING = [
    {
        "sessionId": str(UUID(int=3)),
        "taskId": str(UUID(int=2)),
        "taskTitle": "Viết bài blog",
        "scheduleSlotId": None,
        "startedAt": "2026-10-30T02:00:00+00:00",
        "endedAt": "2026-10-30T03:35:00+00:00",
        "systemMinutes": 95.4,
    }
]
NOTICES = [
    {
        "id": str(UUID(int=4)),
        "type": "NEAR_END",
        "title": "Sắp hết giờ",
        "isRead": False,
    },
    {"id": str(UUID(int=5)), "type": "OVERRUN", "title": "Đã quá giờ", "isRead": True},
]
PROPOSAL = {
    "id": str(UUID(int=6)),
    "status": "Pending",
    "isFeasible": False,
    "missingMinutes": 45,
    "explanation": "x",
    "slots": [
        {"id": str(UUID(int=9)), "startTime": "2026-10-31T07:00:00+07:00"},
        {"id": str(UUID(int=10)), "startTime": "2026-10-31T02:00:00+00:00"},
    ],
}
MILESTONES = [
    {
        "id": str(UUID(int=11)),
        "name": "Thiết kế",
        "dueDate": "2026-11-05",
        "status": "InProgress",
    },
    {"id": str(UUID(int=12)), "name": "Bàn giao", "dueDate": None, "status": "Planned"},
]


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


def test_pending_confirmations() -> None:
    seen: list[httpx.Request] = []

    sessions = run(client_for(answer(200, PENDING), seen).pending_confirmations(AUTH))

    assert sessions == [PendingSession("Viết bài blog", 95)]
    assert (seen[0].method, seen[0].url.path) == (
        "GET",
        "/api/focus/pending-confirmation",
    )
    assert seen[0].headers["Authorization"] == AUTH


def test_unread_notifications_asks_only_for_unread_ones() -> None:
    seen: list[httpx.Request] = []

    notices = run(client_for(answer(200, NOTICES), seen).unread_notifications(AUTH))

    assert notices == [UnreadNotice("Sắp hết giờ")]  # a read one is skipped anyway
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/notifications"
    assert seen[0].url.params["unreadOnly"] == "true"
    assert seen[0].url.params["take"] == "20"


def test_pending_proposal_sorts_slot_starts() -> None:
    seen: list[httpx.Request] = []

    proposal = run(client_for(answer(200, PROPOSAL), seen).pending_proposal(AUTH))

    assert [s.isoformat() for s in proposal.slot_starts] == [
        "2026-10-31T07:00:00+07:00",
        "2026-10-31T02:00:00+00:00",
    ]
    assert (proposal.is_feasible, proposal.missing_minutes) == (False, 45)
    assert (seen[0].method, seen[0].url.path) == (
        "GET",
        "/api/schedule/proposals/pending",
    )


def test_no_pending_proposal_is_204() -> None:
    assert run(client_for(answer(204)).pending_proposal(AUTH)) is None


def test_milestones() -> None:
    seen: list[httpx.Request] = []

    milestones = run(
        client_for(answer(200, MILESTONES), seen).milestones(PROJECT, AUTH)
    )

    assert milestones == [
        MilestoneSummary("Thiết kế", date(2026, 11, 5), "InProgress"),
        MilestoneSummary("Bàn giao", None, "Planned"),
    ]
    assert (seen[0].method, seen[0].url.path) == (
        "GET",
        f"/api/projects/{PROJECT}/milestones",
    )


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
        ("pending_confirmations", [{"taskTitle": "x"}]),
        ("unread_notifications", [{"title": "x"}]),
        ("pending_proposal", {"slots": [{"startTime": "soon"}], "isFeasible": True}),
        (
            "pending_proposal",
            {"slots": [{"startTime": "2026-10-31T07:00:00"}], "isFeasible": True},
        ),
        ("milestones", [{"name": "x", "dueDate": "soon", "status": "Planned"}]),
        ("milestones", {"items": []}),
    ],
)
def test_unexpected_answers_are_read_errors(method: str, body) -> None:
    reader = client_for(answer(200, body))
    with_id = {"project_progress", "milestones"}
    args = (PROJECT, AUTH) if method in with_id else (AUTH,)

    with pytest.raises(BackendReadError):
        run(getattr(reader, method)(*args))


def test_not_json_is_a_read_error() -> None:
    reader = client_for(lambda request: httpx.Response(200, text="<html>"))

    with pytest.raises(BackendReadError):
        run(reader.projects(AUTH))


# --- FR-22c (T6): ids for the commands ---


def test_current_focus_reads_session_and_slot_ids() -> None:
    body = {**FOCUS, "scheduleSlotId": str(UUID(int=9))}

    focus = run(client_for(answer(200, body)).current_focus(AUTH))

    assert focus.session_id == UUID(int=1)
    assert focus.slot_id == UUID(int=9)
    assert run(client_for(answer(200, FOCUS)).current_focus(AUTH)).slot_id is None


def test_pending_proposal_reads_its_id() -> None:
    proposal = run(client_for(answer(200, PROPOSAL)).pending_proposal(AUTH))

    assert proposal.proposal_id == UUID(int=6)
