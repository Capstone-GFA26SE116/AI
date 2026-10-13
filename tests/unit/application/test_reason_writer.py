import asyncio
import json
from decimal import Decimal
from uuid import UUID

import pytest

from metanoia_ai.application.ports.llm_client import LLMError
from metanoia_ai.application.reason_writer import (
    PROMPT_VERSION,
    BadAnswerError,
    ReasonWriter,
    check_answer,
)
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.schedule_text import SlotFacts
from metanoia_ai.prompts import load_prompt, load_samples
from tests.unit.application.fakes import FakeLLMClient

SLOT_1 = UUID(int=1)
SLOT_2 = UUID(int=2)
FACTS = SlotFacts("Write report", 2, True, 30, True, 1, 1)


def answer(*pairs: tuple[UUID, str]) -> str:
    return json.dumps({"reasons": [{"slot_id": str(s), "reason": r} for s, r in pairs]})


GOOD = answer((SLOT_1, "Due in 2 days, in your most focused hour."))


def writer(llm, max_retries=2, ticks=None) -> ReasonWriter:
    # Fake clock: +0.3 s per reading, so 3 fast tries all start inside the 2 s budget.
    times = iter(ticks or [i * 0.3 for i in range(20)])
    return ReasonWriter(
        llm,
        max_retries=max_retries,
        input_price_per_1m=0.1,
        output_price_per_1m=0.4,
        clock=lambda: next(times),
    )


def write(llm, facts=None, lang=Language.VI, **kwargs):
    return asyncio.run(writer(llm, **kwargs).write(facts or {SLOT_1: FACTS}, lang))


def test_good_answer_first_time():
    llm = FakeLLMClient(GOOD)
    result = write(llm)
    assert result.succeeded
    assert result.reasons == {SLOT_1: "Due in 2 days, in your most focused hour."}
    assert len(result.calls) == 1
    call = result.calls[0]
    assert call.succeeded is True
    assert (call.model, call.input_tokens, call.output_tokens) == (
        "fake-model",
        100,
        50,
    )
    assert call.latency_ms == 300


def test_t23_bad_shape_once_then_good():
    llm = FakeLLMClient('{"wrong": []}', GOOD)
    result = write(llm)
    assert result.succeeded
    assert [c.succeeded for c in result.calls] == [False, True]


def test_t24_three_failures_give_no_reasons():
    llm = FakeLLMClient(LLMError("timeout", model="m"), "not json", '{"reasons": []}')
    result = write(llm)
    assert result.reasons is None
    assert not result.succeeded
    assert [c.succeeded for c in result.calls] == [False, False, False]
    assert len(llm.prompts) == 3  # 1 try + 2 retries, then stop


def test_t25_internal_name_is_retried():
    bad = answer((SLOT_1, "Your K_bias is 1.3, so this takes longer."))
    result = write(FakeLLMClient(bad, GOOD))
    assert result.succeeded
    assert [c.succeeded for c in result.calls] == [False, True]


def test_t46_cost_from_tokens_and_prices():
    # 100 input × 0.1 + 50 output × 0.4 = 30, per 1M tokens → 0.00003
    result = write(FakeLLMClient(GOOD))
    assert result.calls[0].cost == Decimal("0.00003")


def test_failed_call_costs_only_the_input_tokens_it_reports():
    error = LLMError("timeout", model="m", input_tokens=1000)
    result = write(FakeLLMClient(error, GOOD))
    assert result.calls[0].cost == Decimal("0.0001")
    assert result.calls[0].model == "m"


def test_retries_can_be_turned_off():
    result = write(FakeLLMClient("bad", GOOD), max_retries=0)
    assert result.reasons is None
    assert len(result.calls) == 1


def test_slow_failure_is_not_retried():
    # First call times out after 10 s: retrying could take 10 s more (AC8).
    llm = FakeLLMClient(LLMError("timeout", model="m"), GOOD)
    result = write(llm, ticks=[0.0, 10.0, 10.0])
    assert result.reasons is None
    assert len(llm.prompts) == 1
    assert result.calls[0].latency_ms == 10_000


def test_retry_stops_once_the_budget_is_used():
    # Two fast bad answers take 1.2 s each: the 3rd try would start at 2.4 s ≥ 2 s.
    llm = FakeLLMClient("bad", "bad", GOOD)
    result = write(llm, ticks=[0.0, 1.2, 1.2, 2.4, 2.4])
    assert result.reasons is None
    assert len(llm.prompts) == 2


def test_fast_bad_answer_is_retried_within_the_budget():
    llm = FakeLLMClient("bad", GOOD)
    result = write(llm, ticks=[0.0, 1.9, 1.9, 2.5])
    assert result.succeeded
    assert [c.latency_ms for c in result.calls] == [1900, 600]


def test_no_slots_no_call():
    llm = FakeLLMClient()
    result = asyncio.run(writer(llm).write({}, Language.VI))
    assert result.reasons == {}
    assert result.calls == ()
    assert llm.prompts == []


# --- Checking the answer ---


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("no json", "not JSON"),
        (answer(), "missing slot"),
        (answer((SLOT_1, "ok"), (SLOT_2, "extra")), "unknown slot"),
        (answer((SLOT_1, "a"), (SLOT_1, "b")), "repeated slot"),
        (answer((SLOT_1, "   ")), "empty"),
        (answer((SLOT_1, "x" * 151)), "too long"),
        (answer((SLOT_1, "line one\nline two")), "two lines"),
        (answer((SLOT_1, "Needs T_proposed minutes")), "internal name"),
        (answer((SLOT_1, "Because k=1.3")), "internal name, lower case"),
    ],
)
def test_bad_answers_are_rejected(text, why):
    with pytest.raises(BadAnswerError):
        check_answer(text, {SLOT_1})


def test_reason_of_exactly_150_characters_is_ok():
    assert check_answer(answer((SLOT_1, "x" * 150)), {SLOT_1}) == {SLOT_1: "x" * 150}


# --- Prompt ---


def test_prompt_file_is_loaded_and_filled():
    llm = FakeLLMClient(GOOD)
    write(llm, lang=Language.EN)
    prompt = llm.prompts[0]
    assert "Write in: English" in prompt
    assert str(SLOT_1) in prompt
    assert '"task_title": "Write report"' in prompt
    assert "$" not in prompt  # every placeholder was filled
    assert llm.schemas[0]["required"] == ["reasons"]


def test_t29_prompt_asks_for_the_users_language():
    llm = FakeLLMClient(GOOD)
    write(llm, lang=Language.VI)
    assert "Write in: Vietnamese" in llm.prompts[0]


def test_vietnamese_titles_are_sent_as_text():
    llm = FakeLLMClient(GOOD)
    facts = SlotFacts("Viết báo cáo", 2, True, 30, True, 1, 1)
    write(llm, facts={SLOT_1: facts})
    assert "Viết báo cáo" in llm.prompts[0]


def test_prompt_version_file_exists():
    assert "slot_id" in load_prompt(PROMPT_VERSION)


def test_samples_are_valid_and_fill_the_prompt():
    samples = json.loads(load_samples("schedule_reason"))
    assert len(samples) == 10
    w = writer(FakeLLMClient())
    for sample in samples:
        facts = {
            UUID(s["slot_id"]): SlotFacts(
                s["task_title"],
                s["days_to_deadline"],
                s["in_productive_hour"],
                s["extra_percent"],
                s["extra_applied"],
                s["part"],
                s["parts"],
            )
            for s in sample["slots"]
        }
        prompt = w.build_prompt(facts, Language(sample["language"]))
        assert all(s["slot_id"] in prompt for s in sample["slots"])
