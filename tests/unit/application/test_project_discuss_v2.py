"""FR-08b-5: the wider discussion prompt (plans/FR-08b-5.md T1-T3)."""

import json

import pytest

from metanoia_ai.application.project_chat_writer import (
    DISCUSS_PROMPT_VERSION,
    check_discuss_answer,
)
from metanoia_ai.application.reason_writer import BadAnswerError
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_discussion import MAX_REPLY_CHARS
from metanoia_ai.prompts import load_samples
from tests.unit.application.test_project_discuss_writer import answer, fill

# --- T1: the prompt ---


def test_the_chat_uses_the_companion_prompt():
    assert DISCUSS_PROMPT_VERSION == "project_discuss_v3"
    assert "Compare against existing_tasks" in fill()
    assert "the user must approve them" in fill()


def test_prompt_lists_the_kinds_of_help():
    text = fill()
    for kind in (
        "compare options",
        "emails or messages to the client",
        "Summarize or explain an attached document",
        "Explain a technical idea",
        "Advise on working with the client",
    ):
        assert kind in text


def test_prompt_still_forbids_the_plan_and_names_the_button():
    text = fill()
    assert "Do NOT make the plan here" in text
    assert '"Lên kế hoạch"' in text


def test_prompt_refuses_requests_unrelated_to_work():
    assert "nothing to do with work" in fill()


def test_prompt_asks_for_plain_text_of_limited_length():
    text = fill()
    assert "Plain text only" in text and "500 words" in text


# --- T2: the reply length ---


def test_a_long_reply_with_line_breaks_is_kept():
    email = "Chào anh,\n\n- Giá: 12 triệu\n- Bàn giao: 15/12\n\n" + "x" * 3900
    reply = check_discuss_answer(answer(reply=email[:MAX_REPLY_CHARS]), Language.VI)
    assert "\n- Giá: 12 triệu" in reply.text


def test_a_reply_over_the_limit_is_refused():
    with pytest.raises(BadAnswerError):
        check_discuss_answer(answer(reply="x" * (MAX_REPLY_CHARS + 1)), Language.VI)


# --- T3: the samples ---


def test_new_samples_say_what_to_expect():
    samples = json.loads(load_samples("project_discuss"))
    assert len(samples) == 16
    expects = [s["expect"] for s in samples if "expect" in s]
    assert len(expects) == 6
    assert set(expects) == {"helps", "no_plan", "refuses"}
