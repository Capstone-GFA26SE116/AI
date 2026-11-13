"""API tests run without a DB: every token's user counts as active by default."""

import pytest

from metanoia_ai.api.deps import get_account_repository
from metanoia_ai.api.main import app
from tests.unit.application.fakes import FakeAccountRepository


@pytest.fixture(autouse=True)
def active_accounts():
    """Re-added before each test, because fixtures clear the overrides after one."""
    accounts = FakeAccountRepository()
    app.dependency_overrides[get_account_repository] = lambda: accounts
    yield accounts
    app.dependency_overrides.pop(get_account_repository, None)
