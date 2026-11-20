"""FR-08b-6: the plan remembers the discussion (plans/FR-08b-6.md T2-T4)."""

import json

from metanoia_ai.domain.project_discussion import ChatMode
from tests.unit.application.fakes import FakeLLMClient
from tests.unit.application.test_project_chat import USER, World, draft_answer, run
from tests.unit.application.test_project_chat_writer import fill as fill_plan


def notes_answer(reply="Ok", clear=("Trang chủ",), risks=()):
    return json.dumps(
        {
            "reply": reply,
            "clear": list(clear),
            "missing": [],
            "questions": [],
            "risks": list(risks),
            "ready_to_plan": True,
        },
        ensure_ascii=False,
    )


def say(world, text, answer, mode=ChatMode.DISCUSS, history=20):
    llm = FakeLLMClient(answer)
    run(
        world.use_case(llm, history=history).execute(
            USER, world.chat_id, text, mode=mode
        )
    )
    return llm


def agreed_block(prompt: str) -> str:
    start = prompt.index("DATA - agreed in the discussion")
    return prompt[start : prompt.index("DATA - earlier messages", start)]


# --- T2: the prompt ---


def test_plan_prompt_v3_has_an_agreed_block_and_its_rules():
    text = fill_plan()
    assert (
        "DATA - agreed in the discussion (the newest checklist, or null):\nnull" in text
    )
    assert "Every deliverable named there gets at least" in text


# --- T3: the newest checklist reaches the plan, even when its message is old ---


def test_checklist_of_an_old_message_still_reaches_the_plan():
    world = World()
    say(world, "Brief", notes_answer(clear=("Trang chủ", "Trang blog 3 bài")))
    for index in range(4):  # chatting on; these answers carry no checklist change
        say(
            world,
            f"Hỏi thêm {index}",
            notes_answer(clear=("Trang chủ", "Trang blog 3 bài")),
        )
    llm = say(world, "", draft_answer(), mode=ChatMode.PLAN, history=2)
    block = agreed_block(llm.prompts[0])
    assert "Trang blog 3 bài" in block
    assert "Hỏi thêm 0" not in llm.prompts[0]  # that message itself fell out


def test_plan_without_any_discussion_sends_null():
    world = World()
    llm = say(world, "Brief của khách", draft_answer(), mode=ChatMode.PLAN)
    assert "null" in agreed_block(llm.prompts[0])


def test_long_ai_answers_are_cut_for_the_plan_only():
    world = World()
    email = "Chào anh,\n" + "nội dung email " * 200 + "hết"
    say(world, "Viết email báo giá", notes_answer(reply=email))
    plan = say(world, "", draft_answer(), mode=ChatMode.PLAN)
    assert email not in plan.prompts[0]
    assert "Chào anh, nội dung email" in plan.prompts[0]
    discuss = say(world, "Sửa email trên ngắn lại", notes_answer())
    assert json.dumps(email, ensure_ascii=False)[1:-1] in discuss.prompts[0]


# --- T4: contact details in the checklist are masked ---


def test_client_name_and_email_in_the_checklist_are_masked():
    world = World()  # the client is "ACME"
    say(world, "Brief", notes_answer(clear=("Gửi báo giá cho ACME qua ceo@acme.vn",)))
    llm = say(world, "", draft_answer(), mode=ChatMode.PLAN)
    block = agreed_block(llm.prompts[0])
    assert "ACME" not in block and "ceo@acme.vn" not in block
    assert "[client]" in block
