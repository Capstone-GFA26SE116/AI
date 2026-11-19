"""FR-08b-3: a project chat message in "discuss" mode (plans/FR-08b-3.md T4, T5)."""

import json

import pytest

from metanoia_ai.application.ports.project_chat_repository import ChatRole
from metanoia_ai.application.project_chat_writer import DiscussAnswer
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
)
from metanoia_ai.domain.project_discussion import ChatMode
from tests.unit.application.fakes import FakeLLMClient
from tests.unit.application.test_project_chat import (
    QUESTION,
    World,
    draft_answer,
    run,
)

DISCUSS = json.dumps(
    {
        "reply": "Mình đã nắm phần chính. Khách có cần bản tiếng Anh không?",
        "clear": ["Landing page 3 phần", "Bàn giao 30/10"],
        "missing": ["Có cần bản tiếng Anh không"],
        "questions": ["Mấy vòng góp ý?"],
        "risks": ["Chưa có nội dung bảng giá"],
        "ready_to_plan": True,
    },
    ensure_ascii=False,
)


def discuss(world: World, llm, message="Brief của khách ACME"):
    return run(
        world.use_case(llm).execute(
            world.user, world.chat_id, message, mode=ChatMode.DISCUSS
        )
    )


def test_discussion_saves_a_checklist_and_no_draft():
    world = World()
    llm = FakeLLMClient(DISCUSS)

    turn = discuss(world, llm)

    ai = turn.ai_message
    assert ai.draft is None
    assert ai.notes is not None
    assert ai.notes.clear == ("Landing page 3 phần", "Bàn giao 30/10")
    assert not ai.notes.ready_to_plan  # something is still missing (plan #5)
    assert [m.role for m in world.messages()] == [ChatRole.USER, ChatRole.ASSISTANT]
    assert world.messages()[1].notes == ai.notes


def test_discussion_uses_its_own_prompt_and_schema():
    world = World()
    llm = FakeLLMClient(DISCUSS)

    discuss(world, llm)

    assert "Do NOT make the plan here" in llm.prompts[0]
    assert llm.schemas[0] == DiscussAnswer.model_json_schema()


def test_discussion_is_charged_and_logged_like_a_plan_message():
    world = World()

    discuss(world, FakeLLMClient(DISCUSS))

    assert world.calls_used == 1
    assert [e.quota_charged for _, _, e in world.quota.logs] == [True]


def test_failed_discussion_is_502_and_not_charged():
    world = World()

    with pytest.raises(AIUnavailableError):
        discuss(world, FakeLLMClient("bad", "still bad"))

    assert world.calls_used == 0
    assert world.messages() == []
    assert [e.quota_charged for _, _, e in world.quota.logs] == [False, False]


def test_discussion_masks_the_client_and_contacts():
    world = World()
    llm = FakeLLMClient(DISCUSS)

    discuss(world, llm, message="ACME muốn gửi file về ceo@acme.vn")

    assert "ceo@acme.vn" not in llm.prompts[0]
    assert "ACME" not in llm.prompts[0]


def test_checklist_reaches_the_plan_prompt_later():
    world = World()
    discuss(world, FakeLLMClient(DISCUSS))
    llm = FakeLLMClient(draft_answer())

    turn = world.send(llm, message="Lên kế hoạch")

    assert "Chưa có nội dung bảng giá" in llm.prompts[0]  # the earlier risks line
    assert turn.ai_message.draft is not None


def test_checklist_is_never_the_latest_draft():
    world = World()
    world.send(FakeLLMClient(draft_answer()), message="Lên kế hoạch")
    discuss(world, FakeLLMClient(DISCUSS), message="Khách xin thêm trang blog")

    latest = run(world.repo.latest_draft(world.user, world.chat_id))

    assert latest is not None
    assert latest.draft is not None
    assert latest.notes is None


def test_plan_is_still_the_default_mode():
    world = World()
    llm = FakeLLMClient(QUESTION)

    turn = world.send(llm)

    assert turn.ai_message.notes is None
    assert "Do NOT make the plan here" not in llm.prompts[0]
