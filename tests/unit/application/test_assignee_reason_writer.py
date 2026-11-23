"""FR-23 reason writer: answer checks, retries and the prompt (T7, T8, T10)."""

import asyncio
import json
from uuid import UUID

import pytest

from metanoia_ai.application.assignee_reason_writer import (
    PROMPT_VERSION,
    AssigneeReasonWriter,
    check_answer,
)
from metanoia_ai.application.ports.llm_client import LLMError
from metanoia_ai.application.reason_writer import BadAnswerError
from metanoia_ai.domain.assignee_ranking import ReasonFacts
from metanoia_ai.domain.models import Language
from metanoia_ai.prompts import load_prompt
from tests.unit.application.fakes import FakeLLMClient

A, B = UUID(int=1), UUID(int=2)
FACTS = {
    A: ReasonFacts(1, 2, False, True, 14.0, 0.0, True, True),
    B: ReasonFacts(2, 2, True, None, 3.5, 2.0, False, False),
}


def answer(*items: tuple[UUID, str]) -> str:
    return json.dumps(
        {"reasons": [{"member_id": str(i), "reason": r} for i, r in items]}
    )


def writer(llm: FakeLLMClient, retries: int = 1) -> AssigneeReasonWriter:
    return AssigneeReasonWriter(
        llm, max_retries=retries, input_price_per_1m=0.1, output_price_per_1m=0.4
    )


def test_good_answer_gives_a_reason_per_member():
    reasons = check_answer(answer((A, "Rảnh nhiều nhất."), (B, "Đang bận.")), {A, B})
    assert reasons == {A: "Rảnh nhiều nhất.", B: "Đang bận."}


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        answer((A, "ok")),  # B missing
        answer((A, "ok"), (B, "ok"), (UUID(int=3), "ok")),  # unknown member
        answer((A, "ok"), (A, "again"), (B, "ok")),  # repeated
        answer((A, ""), (B, "ok")),  # empty
        answer((A, "x" * 201), (B, "ok")),  # too long
        answer((A, "two\nlines"), (B, "ok")),
        answer((A, "free_hours is high"), (B, "ok")),  # field name
        answer((A, "K_bias is low"), (B, "ok")),  # shared internal name
    ],
)
def test_bad_answers_are_refused(text: str):
    with pytest.raises(BadAnswerError):
        check_answer(text, {A, B})


def test_retry_after_a_bad_answer_then_success():
    llm = FakeLLMClient("bad", answer((A, "Tốt."), (B, "Bận.")))
    result = asyncio.run(writer(llm).write("Task", FACTS, Language.VI))
    assert result.succeeded and result.reasons == {A: "Tốt.", B: "Bận."}
    assert [c.succeeded for c in result.calls] == [False, True]
    assert result.calls[1].cost > 0


def test_every_try_failing_keeps_no_reasons():
    llm = FakeLLMClient(LLMError("timeout", model="m", input_tokens=7), "bad")
    result = asyncio.run(writer(llm).write("Task", FACTS, Language.EN))
    assert result.reasons is None and not result.succeeded
    assert [c.input_tokens for c in result.calls] == [7, 100]


def test_no_members_means_no_call():
    llm = FakeLLMClient()
    result = asyncio.run(writer(llm).write("Task", {}, Language.VI))
    assert result.reasons == {} and llm.prompts == []


def test_prompt_lists_facts_language_and_title_only():
    prompt = writer(FakeLLMClient()).build_prompt("Design page", FACTS, Language.EN)
    assert "Write in: English" in prompt and "Task: Design page" in prompt
    assert f'"member_id": "{A}"' in prompt and '"fits": null' in prompt
    assert "$" not in prompt  # every placeholder filled
    assert load_prompt(PROMPT_VERSION)  # the file exists
