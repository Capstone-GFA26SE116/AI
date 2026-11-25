"""FR-25 SQL shape and row conversions (T5). The SQL is also run read-only on the DB."""

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

from metanoia_ai.application.ports.group_slot_repository import GroupSlotRepository
from metanoia_ai.infrastructure.db.group_slot_repository import (
    GET_BOOKED,
    GET_SUBTASKS,
    GET_TASK,
    SqlGroupSlotRepository,
)

ME, LEAD, TASK, SUB_A, SUB_B = (UUID(int=n) for n in (1, 2, 100, 3, 4))
DEADLINE = datetime(2026, 11, 27, 10, tzinfo=UTC)


def sql(statement) -> str:
    return " ".join(str(statement).split())


class Result:
    def __init__(self, rows):
        self.rows = rows

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Session:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return Result(self.rows.get(str(statement), []))


def task_row(**changes):
    values = {
        "Id": TASK,
        "Title": "Báo cáo",
        "Status": "InProgress",
        "AssigneeId": ME,
        "EstimatedMinutes": 120,
        "FixedStart": None,
        "Deadline": DEADLINE,
        "GroupDeadline": date(2026, 12, 1),
        "GroupStatus": "Active",
        "IsMember": True,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def load(rows):
    session = Session(rows)
    repo: GroupSlotRepository = SqlGroupSlotRepository(session)
    return asyncio.run(repo.get_task(TASK, ME)), session


def test_only_reads_and_scopes_to_the_caller():
    for statement in (GET_TASK, GET_SUBTASKS, GET_BOOKED):
        text = sql(statement)
        assert text.startswith("SELECT")
        for word in ("INSERT", "UPDATE", "DELETE"):
            assert word not in text.upper()
    assert 'm."UserId" = :caller_id' in sql(GET_TASK)
    booked = sql(GET_BOOKED)
    assert 's."UserId" = :caller_id' in booked
    assert "('Approved', 'Due', 'InProgress', 'Overrun', 'Done')" in booked


def test_task_without_subtasks_is_one_part():
    found, _ = load(
        {
            str(GET_TASK): [task_row()],
            str(GET_BOOKED): [SimpleNamespace(GroupSubTaskId=None, Minutes=30)],
        }
    )
    [part] = found.parts
    assert (part.subtask_id, part.assignee_id, part.estimated_minutes) == (
        None,
        ME,
        120,
    )
    assert part.booked_minutes == 30 and part.is_open
    assert found.deadline == DEADLINE and found.group_deadline == date(2026, 12, 1)
    assert not (found.group_archived or found.is_closed or found.has_fixed_time)


def test_subtasks_take_the_task_assignee_when_they_have_none():
    found, _ = load(
        {
            str(GET_TASK): [task_row(AssigneeId=LEAD)],
            str(GET_SUBTASKS): [
                SimpleNamespace(
                    Id=SUB_A,
                    Title="Slide",
                    AssigneeId=ME,
                    Status="ToDo",
                    EstimatedMinutes=60,
                    OrderIndex=0,
                ),
                SimpleNamespace(
                    Id=SUB_B,
                    Title="Số liệu",
                    AssigneeId=None,
                    Status="Done",
                    EstimatedMinutes=None,
                    OrderIndex=1,
                ),
            ],
            str(GET_BOOKED): [SimpleNamespace(GroupSubTaskId=SUB_A, Minutes=45)],
        }
    )
    a, b = found.parts
    assert (a.title, a.assignee_id, a.booked_minutes, a.is_open) == (
        "Báo cáo: Slide",
        ME,
        45,
        True,
    )
    assert (b.assignee_id, b.is_open, b.order_index) == (LEAD, False, 1)


def test_an_outsider_gets_no_parts_read():
    found, session = load({str(GET_TASK): [task_row(IsMember=False)]})
    assert found.is_member is False and found.parts == ()
    assert len(session.calls) == 1


def test_flags():
    found, _ = load(
        {
            str(GET_TASK): [
                task_row(GroupStatus="Archived", Status="Skipped", FixedStart=DEADLINE)
            ]
        }
    )
    assert found.group_archived and found.is_closed and found.has_fixed_time


def test_missing_task():
    assert load({})[0] is None
