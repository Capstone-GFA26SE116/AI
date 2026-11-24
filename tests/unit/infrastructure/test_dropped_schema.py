"""No SQL of the AI service uses tables or columns the Backend dropped (no DB).

On 2026-11-24 the Backend dropped FocusTrack, BlindTestSession and PaymentRefund
and their columns. A query that still named "BlindTestSessionId" broke every
schedule proposal (FR-10) with "column does not exist".
"""

import importlib
import pkgutil

import pytest
from sqlalchemy.sql.elements import TextClause

import metanoia_ai.infrastructure.db as db_package

DROPPED = (
    "FocusTrack",
    "BlindTestSession",
    "PaymentRefund",
    "DisplayPosition",
    "IsChosen",
)


def all_sql() -> list[tuple[str, str]]:
    found = []
    for module_info in pkgutil.iter_modules(db_package.__path__):
        module = importlib.import_module(f"{db_package.__name__}.{module_info.name}")
        for name, value in vars(module).items():
            if isinstance(value, TextClause):
                found.append((f"{module_info.name}.{name}", str(value)))
    return found


def test_the_scan_finds_the_repository_sql() -> None:
    names = {name for name, _ in all_sql()}
    assert "schedule_repository.SUPERSEDE_OLD_PROPOSALS" in names
    assert len(names) > 50


@pytest.mark.parametrize("name,sql", all_sql())
def test_sql_does_not_use_dropped_schema(name: str, sql: str) -> None:
    for word in DROPPED:
        assert f'"{word}' not in sql, f"{name} uses dropped {word}"
