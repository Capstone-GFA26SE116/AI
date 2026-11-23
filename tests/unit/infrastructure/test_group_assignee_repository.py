"""SqlGroupAssigneeRepository: SQL shape and row conversions (stand-in session, no DB). T11."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.group_assignee_repository import (
    GroupAssigneeRepository,
    GroupTaskStatus,
)
from metanoia_ai.domain.assignee_ranking import GroupRole
from metanoia_ai.domain.models import TimeRange
from metanoia_ai.infrastructure.db.group_assignee_repository import (
    GET_GROUP_WORK,
    GET_MEMBERS,
    GET_TASK,
    SqlGroupAssigneeRepository,
    task_from_row,
    work_from_rows,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 11, 23, 8, tzinfo=VN)
END = NOW + timedelta(days=2)
TASK, OTHER, GROUP = UUID(int=1), UUID(int=2), UUID(int=10)
A, B = UUID(int=21), UUID(int=22)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class RecordingSession:
    def __init__(self, rows_by_statement=None):
        self.rows = rows_by_statement or {}
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return Result(self.rows.get(str(statement), []))


def task_row(**changes):
    values = {
        "Id": TASK,
        "GroupId": GROUP,
        "GroupStatus": "Active",
        "CallerRole": "Leader",
        "Title": "Design page",
        "Status": "InProgress",
        "Deadline": END,
        "EstimatedMinutes": 120,
        "FixedStart": None,
        "FixedEnd": None,
        "AssigneeId": None,
        "OpenSubTaskMinutes": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def work_row(user, task, minutes, deadline=END, start=None, end=None):
    return SimpleNamespace(
        UserId=user,
        TaskId=task,
        Minutes=minutes,
        Deadline=deadline,
        FixedStart=start,
        FixedEnd=end,
    )


def test_repository_matches_the_port():
    repo: GroupAssigneeRepository = SqlGroupAssigneeRepository(RecordingSession())
    assert repo is not None


def test_sql_filters_group_role_status_and_deleted_accounts():
    task_sql, members_sql, work_sql = (
        str(GET_TASK),
        str(GET_MEMBERS),
        str(GET_GROUP_WORK),
    )
    assert 'LEFT JOIN "GroupMember" m' in task_sql and ":caller_id" in task_sql
    assert "('Leader', 'Member')" in members_sql
    assert '"DeletedAt" IS NULL' in members_sql and "LIMIT :limit" in members_sql
    assert work_sql.count("\"Status\" = 'Active'") == 2
    assert work_sql.count(":exclude_task_id") == 2
    assert "NOT EXISTS" in work_sql  # tasks with subtasks count through subtasks


def test_get_task_converts_the_row_and_none_when_missing():
    session = RecordingSession({str(GET_TASK): [task_row()]})
    repo = SqlGroupAssigneeRepository(session)
    task = asyncio.run(repo.get_task(TASK, A))
    assert task.caller_role is GroupRole.LEADER
    assert task.status is GroupTaskStatus.IN_PROGRESS and not task.group_archived
    assert session.calls[0][1] == {"task_id": TASK, "caller_id": A}
    empty = SqlGroupAssigneeRepository(RecordingSession())
    assert asyncio.run(empty.get_task(TASK, A)) is None


def test_task_row_details():
    outsider = task_from_row(task_row(CallerRole=None, GroupStatus="Archived"))
    assert outsider.caller_role is None and outsider.group_archived
    viewer = task_from_row(task_row(CallerRole="Viewer"))
    assert viewer.caller_role is GroupRole.VIEWER
    from_subtasks = task_from_row(
        task_row(EstimatedMinutes=None, OpenSubTaskMinutes=90)
    )
    assert from_subtasks.estimate_minutes == 90
    unknown = task_from_row(task_row(EstimatedMinutes=None, OpenSubTaskMinutes=None))
    assert unknown.estimate_minutes is None
    fixed = task_from_row(task_row(FixedStart=NOW, FixedEnd=NOW + timedelta(hours=1)))
    assert fixed.fixed_time == TimeRange(NOW, NOW + timedelta(hours=1))


def test_get_members_maps_roles_and_passes_the_limit():
    rows = [
        SimpleNamespace(UserId=A, Role="Leader"),
        SimpleNamespace(UserId=B, Role="Member"),
    ]
    session = RecordingSession({str(GET_MEMBERS): rows})
    members = asyncio.run(SqlGroupAssigneeRepository(session).get_members(GROUP, 5))
    assert [(m.user_id, m.role) for m in members] == [
        (A, GroupRole.LEADER),
        (B, GroupRole.MEMBER),
    ]
    assert session.calls[0][1] == {"group_id": GROUP, "limit": 5}


def test_group_work_with_no_users_reads_nothing():
    session = RecordingSession()
    work = asyncio.run(
        SqlGroupAssigneeRepository(session).get_group_work([], NOW, END, TASK)
    )
    assert work == {} and session.calls == []


def test_group_work_counts_minutes_due_in_the_window_and_fixed_times_once():
    fixed_start, fixed_end = NOW + timedelta(hours=1), NOW + timedelta(hours=2)
    rows = [
        work_row(A, OTHER, 60),  # due in the window → counted
        work_row(A, UUID(int=3), 30, deadline=END + timedelta(days=1)),  # later → no
        work_row(A, UUID(int=4), 45, deadline=None),  # no deadline → no
        work_row(A, UUID(int=5), None),  # no estimate → no
        work_row(B, UUID(int=6), 40, start=fixed_start, end=fixed_end),  # fixed
        work_row(B, UUID(int=6), 20, start=fixed_start, end=fixed_end),  # same task
        work_row(
            B,
            UUID(int=7),
            50,
            start=END + timedelta(days=1),
            end=END + timedelta(days=1, hours=1),
        ),  # fixed, outside window
    ]
    session = RecordingSession({str(GET_GROUP_WORK): rows})
    work = asyncio.run(
        SqlGroupAssigneeRepository(session).get_group_work([A, B], NOW, END, TASK)
    )
    assert work[A].open_minutes == 60 and work[A].fixed_times == ()
    assert work[B].open_minutes == 0
    assert work[B].fixed_times == (TimeRange(fixed_start, fixed_end),)
    assert session.calls[0][1] == {"user_ids": [A, B], "exclude_task_id": TASK}


def test_work_from_rows_skips_users_without_work():
    assert work_from_rows([], NOW, END) == {}
