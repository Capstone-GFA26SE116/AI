"""FR-08b-8: drafts keep reasons + subtasks; Apply writes the subtasks (T3, T4)."""

import json
from datetime import date
from uuid import UUID

from metanoia_ai.application.ports.project_chat_repository import SavedProjectDraft
from metanoia_ai.application.use_cases.apply_project_breakdown import (
    ApplyProjectBreakdown,
)
from metanoia_ai.domain.project_breakdown import (
    DomainNumbers,
    DraftMilestone,
    DraftSubtask,
    DraftTask,
    MilestoneInput,
    ProjectDraft,
    ProjectNumbers,
    SubtaskInput,
    TaskInput,
)
from metanoia_ai.infrastructure.db.project_chat_repository import (
    draft_from_json,
    draft_to_json,
)
from tests.unit.application.test_project_chat import (
    CODING,
    DESIGN,
    NOW,
    USER,
    World,
    draft_answer,
    run,
    task,
)
from tests.unit.application.test_project_chat_continuous import MANUAL, press_plan


def big(title, steps):
    data = task(title, CODING, 999)
    data["subtasks"] = [
        {"title": t, "estimated_minutes": m, "reason": "bước"} for t, m in steps
    ]
    return data


PLAN = [
    {
        "title": "Lập trình",
        "reason": "Giao diện đã duyệt nên code không phải làm lại",
        "deadline": None,
        "tasks": [
            big(
                "Giỏ hàng", [("Thêm vào giỏ", 90), ("Tính tiền", 60), ("Kiểm thử", 45)]
            ),
            task("Footer", DESIGN, 60),
        ],
    }
]


def apply(world, edited=None):
    latest = run(world.repo.latest_draft(USER, world.chat_id))
    use_case = ApplyProjectBreakdown(world.repo, MANUAL, lambda: NOW)
    return run(use_case.execute(USER, world.chat_id, latest.message_id, edited))


def subtasks_of(world, title):
    row = next(r for r in world.repo.new_tasks if r.task.title == title)
    return [(s.title, s.estimated_minutes, s.order_index) for s in row.task.subtasks]


# --- T3: the draft JSON keeps both, old drafts still load ---


def test_draft_json_round_trip_keeps_reasons_and_subtasks():
    saved = SavedProjectDraft(
        ProjectDraft(
            (
                DraftMilestone(
                    "m1",
                    "Lập trình",
                    date(2026, 10, 22),
                    (
                        DraftTask(
                            "m1-t1",
                            "Giỏ hàng",
                            UUID(int=2),
                            "Coding",
                            150,
                            "r",
                            (DraftSubtask("A", 90, "x"), DraftSubtask("B", 60)),
                        ),
                    ),
                    "Vì sao",
                ),
            )
        ),
        ProjectNumbers((DomainNumbers("Coding", 150, 225),), 150, 225, 6, False, None),
    )
    back = draft_from_json(draft_to_json(saved))
    assert back.draft == saved.draft


def test_old_drafts_without_the_fields_still_load():
    data = json.loads(
        draft_to_json(
            SavedProjectDraft(
                ProjectDraft(
                    (
                        DraftMilestone(
                            "m1",
                            "A",
                            date(2026, 10, 22),
                            (DraftTask("m1-t1", "T", UUID(int=1), "Design", 60),),
                        ),
                    )
                ),
                ProjectNumbers((), 60, 78, None, False, None),
            )
        )
    )
    del data["milestones"][0]["reason"]
    del data["milestones"][0]["tasks"][0]["subtasks"]
    old = draft_from_json(json.dumps(data)).draft
    assert old.milestones[0].reason is None and old.tasks[0].subtasks == ()


# --- T4: Apply writes the subtasks ---


def test_apply_writes_subtasks_in_order():
    world = World()
    press_plan(world, draft_answer(PLAN))
    result = apply(world)
    assert subtasks_of(world, "Giỏ hàng") == [
        ("Thêm vào giỏ", 90, 1),
        ("Tính tiền", 60, 2),
        ("Kiểm thử", 45, 3),
    ]
    assert subtasks_of(world, "Footer") == []
    created = {t.title: t.estimated_minutes for m in result.milestones for t in m.tasks}
    assert created["Giỏ hàng"] == 195  # the sum of its subtasks


def edited(subtasks):
    return [
        MilestoneInput(
            "Lập trình",
            None,
            [TaskInput("Giỏ hàng", CODING, 240, key="m1-t1", subtasks=subtasks)],
            key="m1",
        )
    ]


def test_edit_without_subtasks_keeps_the_drafts():
    world = World()
    press_plan(world, draft_answer(PLAN))
    apply(world, edited(None))
    assert [t for t, _, _ in subtasks_of(world, "Giỏ hàng")] == [
        "Thêm vào giỏ",
        "Tính tiền",
        "Kiểm thử",
    ]
    row = next(r for r in world.repo.new_tasks if r.task.title == "Giỏ hàng")
    assert row.task.estimated_minutes == 240  # the user's number stays


def test_edit_with_subtasks_or_none_uses_what_was_sent():
    world = World()
    press_plan(world, draft_answer(PLAN))
    apply(world, edited([SubtaskInput("Một", 120), SubtaskInput("Hai", 120)]))
    assert subtasks_of(world, "Giỏ hàng") == [("Một", 120, 1), ("Hai", 120, 2)]

    other = World()
    press_plan(other, draft_answer(PLAN))
    apply(other, edited([]))
    assert subtasks_of(other, "Giỏ hàng") == []
