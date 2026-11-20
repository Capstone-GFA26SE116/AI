"""FR-08b-8: the plan answer and prompt (plans/FR-08b-8.md T2)."""

import json
from uuid import UUID

from metanoia_ai.application.project_chat_writer import PROMPT_VERSION, check_answer
from tests.unit.application.test_project_chat_writer import CONTEXT, LIMITS, fill


def test_prompt_v4_states_the_five_rules():
    assert PROMPT_VERSION.startswith("project_breakdown_v")  # v4 rules kept in v5
    text = fill()
    assert "$" not in text
    for rule in (
        "result you can check",
        "exactly the whole task",
        "one focus session: 15 to 120 minutes",
        "natural steps",
        "risky parts",
    ):
        assert rule in text
    assert 'Milestone "reason"' in text


def test_answer_with_reasons_and_subtasks_becomes_a_draft():
    milestones = [
        {
            "title": "Thiết kế",
            "reason": "Khách duyệt giao diện trước khi code",
            "deadline": None,
            "tasks": [
                {
                    "title": "UI trang chủ",
                    "domain_id": str(UUID(int=1)),
                    "estimated_minutes": 999,  # ignored: the subtasks decide
                    "reason": "r",
                    "subtasks": [
                        {
                            "title": "Phác bố cục",
                            "estimated_minutes": 60,
                            "reason": "1",
                        },
                        {
                            "title": "Vẽ chi tiết",
                            "estimated_minutes": 120,
                            "reason": "3",
                        },
                    ],
                }
            ],
        }
    ]
    text = json.dumps(
        {"kind": "draft", "reply": "Ok", "milestones": milestones, "reminders": []}
    )
    draft = check_answer(text, CONTEXT, LIMITS).draft
    milestone = draft.milestones[0]
    assert milestone.reason == "Khách duyệt giao diện trước khi code"
    task = milestone.tasks[0]
    assert task.estimated_minutes == 180
    assert [s.estimated_minutes for s in task.subtasks] == [60, 120]


def test_old_answers_without_the_new_fields_still_work():
    from tests.unit.application.test_project_chat_writer import answer

    draft = check_answer(answer(), CONTEXT, LIMITS).draft
    assert draft.milestones[0].reason is None
    assert all(t.subtasks == () for t in draft.tasks)
