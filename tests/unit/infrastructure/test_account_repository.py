"""SqlAccountRepository: SQL shape, result and transaction handling (T4, T5)."""

import asyncio
from uuid import uuid4

import pytest

from metanoia_ai.application.ports.account_repository import AccountRepository
from metanoia_ai.infrastructure.db.account_repository import (
    IS_ACTIVE,
    SqlAccountRepository,
)

USER = uuid4()


class Result:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None


class RecordingSession:
    """Returns the prepared rows; records calls and whether it was rolled back."""

    def __init__(self, rows, in_transaction=False, error=None):
        self.rows = rows
        self.open = in_transaction
        self.error = error
        self.calls = []
        self.rollbacks = 0

    def in_transaction(self):
        return self.open

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))
        self.open = True  # autobegin
        if self.error:
            raise self.error
        return Result(self.rows)

    async def rollback(self):
        self.rollbacks += 1
        self.open = False


def is_active(session):
    return asyncio.run(SqlAccountRepository(session).is_active(USER))


def test_matches_the_port():
    repo: AccountRepository = SqlAccountRepository(RecordingSession([]))
    assert repo is not None


def test_sql_filters_by_id_and_not_deleted():
    sql = " ".join(str(IS_ACTIVE).split())
    assert '"Id" = :user_id' in sql
    assert '"DeletedAt" IS NULL' in sql


def test_row_found_means_active():
    session = RecordingSession([(1,)])
    assert is_active(session) is True
    assert session.calls[0][1] == {"user_id": USER}


def test_no_row_means_not_active():
    assert is_active(RecordingSession([])) is False


def test_closes_the_read_it_opened():
    session = RecordingSession([(1,)])
    is_active(session)
    assert session.rollbacks == 1
    assert not session.in_transaction()


def test_keeps_a_transaction_it_did_not_open():
    session = RecordingSession([(1,)], in_transaction=True)
    is_active(session)
    assert session.rollbacks == 0


def test_db_error_is_raised_after_closing_the_read():
    session = RecordingSession([], error=OSError("db down"))
    with pytest.raises(OSError):
        is_active(session)
    assert session.rollbacks == 1
