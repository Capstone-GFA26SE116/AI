"""FR-08b-8: reasons + subtasks in the API (plans/FR-08b-8.md T5)."""

from tests.unit.api.test_project_chat import (  # noqa: F401
    HEADERS,
    apply,
    client,
    send,
    world,
)
from tests.unit.application.test_apply_subtasks import PLAN
from tests.unit.application.test_project_chat import CODING, draft_answer


def test_draft_shows_reasons_and_subtasks(client, world):  # noqa: F811
    world.llm.answers.append(draft_answer(PLAN))
    draft = send(client, world).json()["ai_message"]["draft"]
    milestone = draft["milestones"][0]
    assert milestone["reason"] == "Giao diện đã duyệt nên code không phải làm lại"
    cart = milestone["tasks"][0]
    assert cart["estimated_minutes"] == 195
    assert cart["subtasks"][0] == {
        "title": "Thêm vào giỏ",
        "estimated_minutes": 90,
        "reason": "bước",
    }


def test_apply_takes_edited_subtasks(client, world):  # noqa: F811
    world.llm.answers.append(draft_answer(PLAN))
    message_id = send(client, world).json()["ai_message"]["draft"]["message_id"]
    body = {
        "message_id": message_id,
        "milestones": [
            {
                "key": "m1",
                "title": "Lập trình",
                "tasks": [
                    {
                        "key": "m1-t1",
                        "title": "Giỏ hàng",
                        "domain_id": str(CODING),
                        "estimated_minutes": 200,
                        "subtasks": [
                            {"title": "Một", "estimated_minutes": 100},
                            {"title": "Hai", "estimated_minutes": 100},
                        ],
                    }
                ],
            }
        ],
    }
    assert apply(client, world, body).status_code == 201
    [row] = world.repo.new_tasks
    assert [s.title for s in row.task.subtasks] == ["Một", "Hai"]


def test_apply_refuses_bad_subtasks(client, world):  # noqa: F811
    world.llm.answers.append(draft_answer(PLAN))
    message_id = send(client, world).json()["ai_message"]["draft"]["message_id"]
    body = {
        "message_id": message_id,
        "milestones": [
            {
                "title": "Lập trình",
                "tasks": [
                    {
                        "title": "Giỏ hàng",
                        "domain_id": str(CODING),
                        "estimated_minutes": 200,
                        "subtasks": [{"title": "Một", "estimated_minutes": 200}],
                    }
                ],
            }
        ],
    }
    assert apply(client, world, body).status_code == 422
