"""FR-10 SQL: slots that are running still count as busy time (no DB)."""

import pytest

from metanoia_ai.infrastructure.db.schedule_repository import (
    GET_APPROVED_SLOTS,
    GET_TASKS,
)


def sql(statement) -> str:
    return " ".join(str(statement).split())


@pytest.mark.parametrize("statement", [GET_APPROVED_SLOTS, GET_TASKS])
def test_every_on_calendar_state_is_busy(statement) -> None:
    # The Backend moves an approved slot on: Due → InProgress → Overrun (DB CHECK,
    # 2026-11-10). Missing them let FR-10 propose a slot on top of a running one.
    assert "s.\"Status\" IN ('Approved', 'Due', 'InProgress', 'Overrun')" in sql(
        statement
    )


def test_busy_slots_are_limited_to_the_user_and_window() -> None:
    text = sql(GET_APPROVED_SLOTS)
    assert 'p."UserId" = :user_id' in text
    assert 's."EndTime" > :window_start AND s."StartTime" < :window_end' in text
