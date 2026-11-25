"""FR-10 SQL: slots that are running still count as busy time (no DB)."""

import pytest

from metanoia_ai.infrastructure.db.schedule_repository import (
    GET_APPROVED_SLOTS,
    GET_GROUP_SLOT_TIMES,
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


def test_tasks_query_reads_the_project():
    """FR-10c: the planner switches between projects."""
    from metanoia_ai.infrastructure.db.schedule_repository import GET_TASKS

    assert 't."ProjectId"' in str(GET_TASKS)


def test_recent_sessions_query_is_the_users_finished_confirmed_work():
    """FR-10d: capacity is learned only from the user's own confirmed timer data."""
    from metanoia_ai.infrastructure.db.schedule_repository import GET_RECENT_SESSIONS

    sql = " ".join(str(GET_RECENT_SESSIONS).split())
    assert 'p."UserId" = :user_id' in sql
    assert 'fs."EndedAt" IS NOT NULL' in sql
    assert 'fs."IsConfirmed"' in sql
    assert 'fs."StartedAt" >= :since' in sql


def test_day_overloaded_warning_json_has_its_day():
    import json
    from datetime import date
    from types import SimpleNamespace

    from metanoia_ai.application.ports.schedule_repository import ProposalWarning
    from metanoia_ai.domain.models import ScheduleWarning, WarningType
    from metanoia_ai.infrastructure.db.schedule_repository import warnings_json

    warning = ScheduleWarning(
        WarningType.DAY_OVERLOADED,
        needed_minutes=690,
        day=date(2026, 11, 23),
        capacity_minutes=480,
    )
    proposal = SimpleNamespace(warnings=[ProposalWarning(warning, "Ngày 23/11 …")])
    (item,) = json.loads(warnings_json(proposal))
    assert item == {
        "task_id": None,
        "type": "day_overloaded",
        "needed_minutes": 690,
        "free_minutes": None,
        "day": "2026-11-23",
        "capacity_minutes": 480,
        "message": "Ngày 23/11 …",
    }


def test_group_slots_of_the_user_are_busy() -> None:
    # Backend sql/009 (2026-11-25): a group slot has no TaskId; its owner is UserId.
    text = sql(GET_APPROVED_SLOTS)
    personal, group = text.split("UNION ALL")
    assert 'JOIN "Task"' in personal
    assert 'JOIN "Task"' not in group
    assert 's."GroupTaskId" IS NOT NULL AND s."UserId" = :user_id' in group
    assert "'Approved', 'Due', 'InProgress', 'Overrun'" in group
    assert 's."EndTime" > :window_start AND s."StartTime" < :window_end' in group


def test_group_slot_times_read_only_group_slots() -> None:
    text = sql(GET_GROUP_SLOT_TIMES)
    assert text.startswith("SELECT")
    assert 's."GroupTaskId" IS NOT NULL AND s."UserId" = :user_id' in text
    assert "UNION" not in text
