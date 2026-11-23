"""FR-23: ask the LLM for one short reason per ranked member, check it, retry if bad.

Same pattern as the FR-10 reason writer: one call for all members, a bad answer or
a failed call is retried (`max_retries`), every call is reported for AIUsageLog,
and if every try fails the ranking is kept without reasons.
"""

import json
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from string import Template
from uuid import UUID

from pydantic import BaseModel, ValidationError

from metanoia_ai.application.ports.llm_client import LLMClient, LLMError
from metanoia_ai.application.reason_writer import (
    LANGUAGE_NAMES,
    TOKENS_PER_PRICE_UNIT,
    BadAnswerError,
    LLMCall,
    ReasonResult,
)
from metanoia_ai.domain.assignee_ranking import ReasonFacts
from metanoia_ai.domain.forbidden_terms import has_internal_name
from metanoia_ai.domain.models import Language
from metanoia_ai.prompts import load_prompt

PROMPT_VERSION = "assignee_reason_v1"
MAX_REASON_LENGTH = 200
# Fact names the user must never see (plus the shared internal names).
FORBIDDEN_WORDS = (
    "busy_at_fixed_time",
    "free_hours",
    "group_work_hours",
    "most_free_time",
    "least_group_work",
    "member_id",
)


class AssigneeReasonItem(BaseModel):
    member_id: UUID
    reason: str


class AssigneeReasonsAnswer(BaseModel):
    """The JSON shape the LLM must return."""

    reasons: list[AssigneeReasonItem]


class AssigneeReasonWriter:
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
        self, task_title: str, facts: Mapping[UUID, ReasonFacts], language: Language
    ) -> ReasonResult:
        """A reason per member, or reasons=None when every try failed."""
        if not facts:
            return ReasonResult(reasons={}, calls=())
        prompt = self.build_prompt(task_title, facts, language)
        schema = AssigneeReasonsAnswer.model_json_schema()
        calls: list[LLMCall] = []
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

    def build_prompt(
        self, task_title: str, facts: Mapping[UUID, ReasonFacts], language: Language
    ) -> str:
        members = [facts_to_json(member_id, f) for member_id, f in facts.items()]
        return self.template.substitute(
            language=LANGUAGE_NAMES[language],
            task_title=task_title,
            members=json.dumps(members, ensure_ascii=False, indent=2),
        )

    def _call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        started: float,
        succeeded: bool,
    ) -> LLMCall:
        cost = (
            input_tokens * self.input_price + output_tokens * self.output_price
        ) / TOKENS_PER_PRICE_UNIT
        return LLMCall(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            latency_ms=max(0, round((self.clock() - started) * 1000)),
            succeeded=succeeded,
        )


def facts_to_json(member_id: UUID, facts: ReasonFacts) -> dict[str, object]:
    return {
        "member_id": str(member_id),
        "rank": facts.rank,
        "candidates": facts.candidates,
        "busy_at_fixed_time": facts.busy_at_fixed_time,
        "fits": facts.fits,
        "free_hours": facts.free_hours,
        "group_work_hours": facts.group_work_hours,
        "most_free_time": facts.most_free_time,
        "least_group_work": facts.least_group_work,
    }


def check_answer(text: str, member_ids: set[UUID]) -> dict[UUID, str]:
    """The reasons by member, or BadAnswerError if the answer breaks any rule."""
    try:
        answer = AssigneeReasonsAnswer.model_validate_json(text)
    except ValidationError as error:
        raise BadAnswerError("not the expected JSON") from error

    reasons: dict[UUID, str] = {}
    for item in answer.reasons:
        reason = item.reason.strip()
        if item.member_id in reasons:
            raise BadAnswerError("member repeated")
        if not reason or len(reason) > MAX_REASON_LENGTH or "\n" in reason:
            raise BadAnswerError("reason empty, too long or more than one line")
        if has_internal_name(reason, FORBIDDEN_WORDS):
            raise BadAnswerError("reason shows an internal name")
        reasons[item.member_id] = reason
    if set(reasons) != member_ids:
        raise BadAnswerError("members missing or unknown")
    return reasons
