"""FR-08b-8: milestone reasons and subtasks of big tasks (plans/FR-08b-8.md T1)."""

from uuid import UUID

import pytest

from metanoia_ai.domain.project_breakdown import (
    MilestoneInput,
    ProjectLimits,
    SubtaskInput,
    TaskInput,
    check_project_draft,
)
from metanoia_ai.domain.task_breakdown import BadDraftError

DESIGN = UUID(int=1)
DOMAINS = {DESIGN: "Design"}
LIMITS = ProjectLimits(8, 15, 40, 15, 480, 150)


def draft(subtasks, minutes=240, reason="  Khách duyệt   giao diện trước  ", ai=True):
    task = TaskInput("UI trang chủ", DESIGN, minutes, "r", subtasks=subtasks)
    milestone = MilestoneInput("Thiết kế", None, [task], reason=reason)
    return check_project_draft([milestone], DOMAINS, LIMITS, assign_keys=ai)[0]


def parts(*minutes):
    return [
        SubtaskInput(f"Bước {i}", m, "kết quả kiểm được")
        for i, m in enumerate(minutes, 1)
    ]


def test_milestone_reason_is_tidied():
    assert draft([]).reason == "Khách duyệt giao diện trước"
    assert draft([], reason="").reason is None
    assert len(draft([], reason="x" * 500).reason) == 200


def test_ai_task_minutes_are_the_sum_of_its_subtasks():
    task = draft(parts(90, 60, 45), minutes=200).tasks[0]
    assert task.estimated_minutes == 195
    assert [s.title for s in task.subtasks] == ["Bước 1", "Bước 2", "Bước 3"]
    assert task.subtasks[0].reason == "kết quả kiểm được"


def test_small_ai_task_keeps_no_subtasks():
    task = draft(parts(60, 45), minutes=105).tasks[0]
    assert task.estimated_minutes == 105 and task.subtasks == ()


def test_manual_edit_keeps_the_users_minutes():
    task = draft(parts(90, 60), minutes=300, ai=False).tasks[0]
    assert task.estimated_minutes == 300 and len(task.subtasks) == 2


@pytest.mark.parametrize(
    "subtasks",
    [
        parts(240),  # only one
        parts(*[30] * 7),  # too many
        parts(121, 60),  # longer than one focus session
        parts(10, 60),  # too short
        [SubtaskInput("Vẽ", 60), SubtaskInput(" vẽ ", 60)],  # same title
        [SubtaskInput("Vẽ", 60.0), SubtaskInput("Code", 60)],  # not a whole number
    ],
)
def test_bad_subtasks_are_refused(subtasks):
    with pytest.raises(BadDraftError):
        draft(subtasks)
