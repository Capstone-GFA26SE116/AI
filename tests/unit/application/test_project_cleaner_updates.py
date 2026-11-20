"""FR-08b-9: cleaner update drafts (plans/FR-08b-9.md T1-T4)."""

from metanoia_ai.application.project_chat_writer import PROMPT_VERSION
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_breakdown import (
    DroppedTask,
    DuplicateOf,
    duplicate_warnings,
)
from tests.unit.application.test_apply_subtasks import apply
from tests.unit.application.test_project_chat import (
    CODING,
    World,
    draft_answer,
    task,
)
from tests.unit.application.test_project_chat_continuous import press_plan
from tests.unit.application.test_project_chat_writer import fill

OLD = DuplicateOf.EXISTING_TASK


def dropped(*titles, reason=OLD):
    return [DroppedTask(t, reason) for t in titles]


# --- T1: one line per reason ---


def test_one_dropped_task_keeps_the_old_line():
    assert duplicate_warnings(dropped("Logo"), Language.VI) == [
        'Đã bỏ "Logo": dự án đã có việc này.'
    ]


def test_many_dropped_tasks_become_one_line():
    lines = duplicate_warnings(dropped("A", "B", "C", "D", "E"), Language.VI)
    assert lines == ['Đã bỏ 5 việc dự án đã có: "A", "B", "C"….']


def test_each_reason_gets_its_own_line_in_english():
    lines = duplicate_warnings(
        dropped("A", "B") + dropped("C", "D", reason=DuplicateOf.IN_DRAFT),
        Language.EN,
    )
    assert lines == [
        'Removed 2 tasks the project already has: "A", "B".',
        'Removed 2 tasks repeated in the draft: "C", "D".',
    ]


# --- T2: the prompt asks for new work only ---


def test_prompt_v5_asks_updates_for_new_work_only():
    assert PROMPT_VERSION == "project_breakdown_v5"
    text = fill()
    assert "ONLY the new work" in text
    assert "EXACTLY that" in text


# --- T3: a same-title milestone is reused at Apply ---


def test_update_adds_tasks_to_the_existing_milestone():
    world = World()
    press_plan(world, draft_answer())  # "Thiết kế" + "Lập trình"
    first = apply(world)
    design_id = next(m.milestone_id for m in first.milestones if m.title == "Thiết kế")

    update = [
        {"title": " thiết  kế ", "deadline": None, "tasks": [task("Trang blog")]},
        {"title": "Kiểm thử", "deadline": None, "tasks": [task("Test", CODING)]},
    ]
    press_plan(world, draft_answer(update))
    second = apply(world)

    reused, new = second.milestones
    assert (reused.milestone_id, reused.reused) == (design_id, True)
    assert new.reused is False
    titles = [r.milestone.title for r in world.repo.new_milestones]
    assert titles == ["Thiết kế", "Lập trình", "Kiểm thử"]  # no second "Thiết kế"
    blog = next(r for r in world.repo.new_tasks if r.task.title == "Trang blog")
    assert blog.task.milestone_id == design_id
    orders = [r.milestone.order_index for r in world.repo.new_milestones]
    assert orders == [1, 2, 3]
