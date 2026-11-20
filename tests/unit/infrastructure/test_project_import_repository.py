"""SqlProjectImportRepository (plans/FR-08c.md T4). The SQL runs on the real DB in step 4."""

import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from metanoia_ai.application.ports.project_import_repository import (
    NewProject,
    ProjectImportRepository,
)
from metanoia_ai.infrastructure.db import project_import_repository as sql
from metanoia_ai.infrastructure.db.project_import_repository import (
    SqlProjectImportRepository,
)


def flat(statement) -> str:
    return " ".join(str(statement).split())


class Session:
    def __init__(self, scalars=()):
        self.scalars = list(scalars)
        self.calls = []

    async def execute(self, statement, params):
        self.calls.append((statement, params))
        value = self.scalars.pop(0) if self.scalars else None
        row = SimpleNamespace(Id=uuid4())
        return SimpleNamespace(
            scalar_one_or_none=lambda: value,
            scalar_one=lambda: value,
            one_or_none=lambda: row,
        )


def test_is_a_project_import_repository():
    repo: ProjectImportRepository = SqlProjectImportRepository(Session())
    assert repo is not None


def test_counts_projects_like_the_backend():
    text = flat(sql.COUNT_COUNTABLE_PROJECTS)
    assert '"UserId" = :user_id' in text
    assert 'NOT "IsDefault"' in text and "\"Status\" <> 'Archived'" in text
    limit = flat(sql.GET_PROJECT_LIMIT)
    assert "us.\"Status\" = 'Active'" in limit and 'sp."ProjectLimit"' in limit


def test_insert_is_a_plain_new_active_project_of_this_user():
    text = flat(sql.INSERT_PROJECT)
    assert text.startswith('INSERT INTO "Project"')
    assert "'Active', false, false" in text
    assert 'WHERE u."Id" = :user_id' in text
    assert "UPDATE" not in text and "DELETE" not in text


def test_allowance_locks_the_user_only_when_asked():
    session = Session(scalars=[5, 2])
    allowance = asyncio.run(SqlProjectImportRepository(session).allowance(uuid4()))
    assert (allowance.limit, allowance.used) == (5, 2)
    assert sql.LOCK_USER not in [c[0] for c in session.calls]

    locked = Session(scalars=[None, None, 7])
    allowance = asyncio.run(
        SqlProjectImportRepository(locked).allowance(uuid4(), lock=True)
    )
    assert locked.calls[0][0] is sql.LOCK_USER
    assert (allowance.limit, allowance.used) == (None, 7)


def test_create_project_and_chat_send_the_right_values():
    session = Session(scalars=[uuid4()])
    repo = SqlProjectImportRepository(session)
    user = uuid4()
    asyncio.run(
        repo.create_project(
            user, NewProject("Web", None, date(2026, 11, 20), date(2026, 12, 15))
        )
    )
    _, params = session.calls[0]
    assert params == {
        "user_id": user,
        "name": "Web",
        "description": None,
        "start_date": date(2026, 11, 20),
        "deadline": date(2026, 12, 15),
    }
    chat = Session()
    asyncio.run(
        SqlProjectImportRepository(chat).create_chat(
            user, uuid4(), "Từ file a", "Brief"
        )
    )
    (_, chat_params), (_, message) = chat.calls
    assert chat_params["title"] == "Từ file a"
    assert (message["sender"], message["content"], message["proposal"]) == (
        "User",
        "Brief",
        None,
    )
