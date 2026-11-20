"""FR-08b-4: buttons send "plan" without text; the chat goes on after Apply
(plans/FR-08b-4.md T1, T3, T4)."""

from dataclasses import replace

import pytest

from metanoia_ai.application.ports.project_chat_repository import ConversationStatus
from metanoia_ai.application.use_cases.apply_project_breakdown import (
    ApplyProjectBreakdown,
)
from metanoia_ai.application.use_cases.start_project_chat import (
    ConversationAppliedError,
)
from metanoia_ai.domain.project_breakdown import ProjectLimits
from metanoia_ai.domain.project_discussion import ChatMode
from tests.unit.application.fakes import FakeLLMClient
from tests.unit.application.test_project_chat import (
    CODING,
    NOW,
    QUESTION,
    USER,
    World,
    draft_answer,
    run,
    task,
)
from tests.unit.application.test_project_discuss import DISCUSS

MANUAL = ProjectLimits(8, 15, 40, 5, 960, 150)


def press_plan(world, answer):
    return run(
        world.use_case(FakeLLMClient(answer)).execute(
            USER, world.chat_id, "", mode=ChatMode.PLAN
        )
    )


def apply_latest(world):
    latest = run(world.repo.latest_draft(USER, world.chat_id))
    use_case = ApplyProjectBreakdown(world.repo, MANUAL, lambda: NOW)
    return run(use_case.execute(USER, world.chat_id, latest.message_id)), latest


# --- T1: the plan button sends no text ---


def test_first_plan_button_is_saved_as_len_ke_hoach():
    world = World()
    turn = press_plan(world, draft_answer())
    assert turn.user_message.content == "Lên kế hoạch"
    assert turn.ai_message.draft is not None


def test_plan_button_after_a_draft_is_update_plan():
    world = World()
    press_plan(world, draft_answer())
    turn = press_plan(world, QUESTION)
    assert turn.user_message.content == "Cập nhật kế hoạch"


def test_plan_button_in_an_english_chat():
    world = World()
    run(
        world.use_case(FakeLLMClient(DISCUSS)).execute(
            USER,
            world.chat_id,
            "The client wants a landing page by Friday",
            mode=ChatMode.DISCUSS,
        )
    )
    turn = press_plan(world, draft_answer())
    assert turn.user_message.content == "Plan"


def test_plan_button_in_an_empty_chat_follows_the_project_language():
    world = World()  # the project description is Vietnamese
    assert press_plan(world, QUESTION).user_message.content == "Lên kế hoạch"


def test_plan_prompt_sees_the_whole_discussion():
    world = World()
    run(
        world.use_case(FakeLLMClient(DISCUSS)).execute(
            USER, world.chat_id, "Brief của khách", mode=ChatMode.DISCUSS
        )
    )
    llm = FakeLLMClient(draft_answer())
    run(world.use_case(llm).execute(USER, world.chat_id, "", mode=ChatMode.PLAN))
    assert "Brief của khách" in llm.prompts[0]
    assert "Chưa có nội dung bảng giá" in llm.prompts[0]  # the earlier checklist


# --- T3-T4: the chat goes on after Apply; each draft is applied once ---


def test_apply_discuss_update_apply_again():
    world = World()
    press_plan(world, draft_answer())
    first, first_draft = apply_latest(world)
    assert [m.title for m in first.milestones] == ["Thiết kế", "Lập trình"]

    # Still open: talk, then press "Cập nhật kế hoạch".
    run(
        world.use_case(FakeLLMClient(DISCUSS)).execute(
            USER, world.chat_id, "Khách xin thêm trang blog", mode=ChatMode.DISCUSS
        )
    )
    blog = [
        {"title": "Trang blog", "deadline": None, "tasks": [task("Làm blog", CODING)]}
    ]
    press_plan(world, draft_answer(blog))
    second, second_draft = apply_latest(world)

    assert second_draft.message_id != first_draft.message_id
    assert [m.title for m in second.milestones] == ["Trang blog"]
    applied = [m for m in world.messages() if m.is_applied]
    assert [m.message_id for m in applied] == [
        first_draft.message_id,
        second_draft.message_id,
    ]


def test_the_same_draft_cannot_be_applied_twice():
    world = World()
    press_plan(world, draft_answer())
    apply_latest(world)
    with pytest.raises(ConversationAppliedError):
        apply_latest(world)


def test_repeated_tasks_in_an_update_are_skipped():
    world = World()
    press_plan(world, draft_answer())
    apply_latest(world)
    # The AI lists an old task again next to a new one: the old one is dropped.
    again = [
        {
            "title": "Bổ sung",
            "deadline": None,
            "tasks": [task("Wireframe"), task("Làm blog", CODING)],
        }
    ]
    turn = press_plan(world, draft_answer(again))
    titles = [t.title for m in turn.ai_message.draft.draft.milestones for t in m.tasks]
    assert titles == ["Làm blog"]


def test_closed_chat_takes_no_apply():
    world = World()
    press_plan(world, draft_answer())
    chat = world.repo.chats[world.chat_id]
    chat.conversation = replace(chat.conversation, status=ConversationStatus.CLOSED)
    with pytest.raises(ConversationAppliedError):
        apply_latest(world)
