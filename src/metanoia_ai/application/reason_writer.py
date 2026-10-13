"""FR-10: ask the LLM for one short reason per slot, check the answer, retry if bad.

One call covers all slots. A bad answer (not JSON, wrong shape, missing slot,
too long, internal names) or a failed call is retried up to `max_retries` times.
If every try fails, the slots are kept without reasons.
Every call is reported, so the use case can write one AIUsageLog row per call.
"""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from string import Template
from uuid import UUID

from pydantic import BaseModel, ValidationError

from metanoia_ai.application.ports.llm_client import LLMClient, LLMError
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.schedule_text import SlotFacts
from metanoia_ai.prompts import load_prompt

PROMPT_VERSION = "schedule_reason_v1"
MAX_REASON_LENGTH = 150
# Internal names the user must never see (checked without case).
FORBIDDEN_WORDS = ("k_bias", "kbias", "t_proposed", "t_est", "k=")
LANGUAGE_NAMES = {Language.VI: "Vietnamese", Language.EN: "English"}
TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)


class ReasonItem(BaseModel):
    slot_id: UUID
    reason: str


class ReasonsAnswer(BaseModel):
    """The JSON shape the LLM must return."""

    reasons: list[ReasonItem]


@dataclass(frozen=True)
class LLMCall:
    """One call to the LLM, for AIUsageLog."""

    model: str
    input_tokens: int
    output_tokens: int
    cost: Decimal
    latency_ms: int
    succeeded: bool  # True only when the answer passed every check


@dataclass(frozen=True)
class ReasonResult:
    reasons: dict[UUID, str] | None  # None: every try failed → slots without reasons
    calls: tuple[LLMCall, ...]

    @property
    def succeeded(self) -> bool:
        return self.reasons is not None


class BadAnswerError(Exception):
    """The LLM answered, but the answer breaks a rule."""


class ReasonWriter:
    def __init__(
        self,
        llm: LLMClient,
        *,
        max_retries: int,
        input_price_per_1m: float,
        output_price_per_1m: float,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.llm = llm
        self.max_retries = max_retries
        self.input_price = Decimal(str(input_price_per_1m))
        self.output_price = Decimal(str(output_price_per_1m))
        self.clock = clock
        self.template = Template(load_prompt(PROMPT_VERSION))

    async def write(
        self, facts: Mapping[UUID, SlotFacts], language: Language
    ) -> ReasonResult:
        """Reasons for every slot, or None after 1 + max_retries failed tries."""
        if not facts:
            return ReasonResult(reasons={}, calls=())
        prompt = self.build_prompt(facts, language)
        schema = ReasonsAnswer.model_json_schema()
        calls = []
        for _ in range(1 + self.max_retries):
            started = self.clock()
            try:
                response = await self.llm.generate_json(prompt, schema)
            except LLMError as error:
                calls.append(
                    self._call(error.model, error.input_tokens, 0, started, False)
                )
                continue
            try:
                reasons = check_answer(response.text, set(facts))
            except BadAnswerError:
                reasons = None
            calls.append(
                self._call(
                    response.model,
                    response.input_tokens,
                    response.output_tokens,
                    started,
                    reasons is not None,
                )
            )
            if reasons is not None:
                return ReasonResult(reasons=reasons, calls=tuple(calls))
        return ReasonResult(reasons=None, calls=tuple(calls))

    def build_prompt(self, facts: Mapping[UUID, SlotFacts], language: Language) -> str:
        slots = [facts_to_json(slot_id, f) for slot_id, f in facts.items()]
        return self.template.substitute(
            language=LANGUAGE_NAMES[language],
            slots=json.dumps(slots, ensure_ascii=False, indent=2),
        )

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (
            input_tokens * self.input_price + output_tokens * self.output_price
        ) / TOKENS_PER_PRICE_UNIT

    def _call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        started: float,
        succeeded: bool,
    ) -> LLMCall:
        return LLMCall(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=self.cost(input_tokens, output_tokens),
            latency_ms=max(0, round((self.clock() - started) * 1000)),
            succeeded=succeeded,
        )


def facts_to_json(slot_id: UUID, facts: SlotFacts) -> dict[str, object]:
    return {
        "slot_id": str(slot_id),
        "task_title": facts.task_title,
        "days_to_deadline": facts.days_to_deadline,
        "in_productive_hour": facts.in_productive_hour,
        "extra_percent": facts.extra_percent,
        "extra_applied": facts.extra_applied,
        "part": facts.part_index,
        "parts": facts.part_count,
    }


def check_answer(text: str, slot_ids: set[UUID]) -> dict[UUID, str]:
    """The reasons by slot, or BadAnswerError if the answer breaks any rule."""
    try:
        answer = ReasonsAnswer.model_validate_json(text)
    except ValidationError as error:
        raise BadAnswerError("not the expected JSON") from error

    reasons: dict[UUID, str] = {}
    for item in answer.reasons:
        reason = item.reason.strip()
        if item.slot_id in reasons:
            raise BadAnswerError("slot repeated")
        if not reason or len(reason) > MAX_REASON_LENGTH or "\n" in reason:
            raise BadAnswerError("reason empty, too long or more than one line")
        if any(word in reason.lower() for word in FORBIDDEN_WORDS):
            raise BadAnswerError("reason shows an internal name")
        reasons[item.slot_id] = reason
    if set(reasons) != slot_ids:
        raise BadAnswerError("slots missing or unknown")
    return reasons
