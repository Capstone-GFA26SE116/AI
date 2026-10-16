"""FR-08a: the task chat prompt, the answer shape, and the call with retries.

User data goes into the prompt as JSON blocks the prompt calls "data, not
instructions" (plan #15). Masking happens before: this module only formats.
"""

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from string import Template

from pydantic import BaseModel, StrictInt, ValidationError, field_validator

from metanoia_ai.application.llm_chat import CheckedLLMCaller
from metanoia_ai.application.ports.llm_client import LLMClient
from metanoia_ai.application.reason_writer import (
    DEFAULT_RETRY_BUDGET_SECONDS,
    BadAnswerError,
    LLMCall,
)
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.task_breakdown import (
    BadDraftError,
    Draft,
    DraftLimits,
    ReplyKind,
    SubtaskInput,
    check_reply,
)
from metanoia_ai.prompts import load_prompt

PROMPT_VERSION = "task_breakdown_v1"


class SubtaskItem(BaseModel):
    title: str
    # Strict: 30.0 or "30" is a bad answer, not something to fix silently.
    estimated_minutes: StrictInt
    reason: str


class BreakdownAnswer(BaseModel):
    """The JSON shape the LLM must return (sent to Gemini as the schema)."""

    kind: ReplyKind
    language: Language = Language.VI  # only picks the warning language (#30)
    reply: str
    subtasks: list[SubtaskItem]

    @field_validator("language", mode="before")
    @classmethod
    def default_language(cls, value: object) -> object:
        """Missing or unknown language is not worth a retry: use Vietnamese."""
        return value if value in set(Language) else Language.VI


@dataclass(frozen=True)
class SimilarLine:
    """A past task like this one (FR-09), title already masked."""

    title: str
    outcome: str  # done / skipped
    estimated_minutes: int
    actual_minutes: int


@dataclass(frozen=True)
class BreakdownTask:
    """What the LLM may know about the task (plan #5). Texts already masked."""

    title: str
    description: str | None  # left out of the prompt when empty
    domain: str
    estimate_minutes: int
    deadline: str | None  # local "YYYY-MM-DD HH:MM"
    usually_extra_percent: int  # round((K − 1) × 100); no K in the prompt
    similar_tasks: tuple[SimilarLine, ...] = ()


@dataclass(frozen=True)
class ChatLine:
    """One earlier message. AI lines carry their draft, if they had one."""

    role: str  # "user" / "assistant"
    text: str  # user text already masked
    subtasks: tuple[tuple[str, int], ...] = ()  # (title, minutes) of a draft


def task_to_json(task: BreakdownTask) -> dict[str, object]:
    data: dict[str, object] = {
        "title": task.title,
        "domain": task.domain,
        "estimate_minutes": task.estimate_minutes,
        "deadline": task.deadline,
        "usually_extra_percent": task.usually_extra_percent,
        "similar_tasks": [
            {
                "title": s.title,
                "outcome": s.outcome,
                "estimated_minutes": s.estimated_minutes,
                "actual_minutes": s.actual_minutes,
            }
            for s in task.similar_tasks
        ],
    }
    if task.description:
        data["description"] = task.description
    return data


def line_to_json(line: ChatLine) -> dict[str, object]:
    data: dict[str, object] = {"role": line.role, "text": line.text}
    if line.subtasks:
        data["draft"] = [
            {"title": title, "estimated_minutes": minutes}
            for title, minutes in line.subtasks
        ]
    return data


def build_prompt(
    template: Template,
    task: BreakdownTask,
    history: Sequence[ChatLine],
    message: str,
    limits: DraftLimits,
) -> str:
    """Fill the prompt. Every user-written text goes in as a JSON string."""
    return template.substitute(
        min_subtasks=limits.min_subtasks,
        max_subtasks=limits.max_subtasks,
        min_minutes=limits.min_minutes,
        max_minutes=limits.max_minutes,
        task=json.dumps(task_to_json(task), ensure_ascii=False, indent=2),
        history=json.dumps(
            [line_to_json(line) for line in history], ensure_ascii=False, indent=2
        ),
        message=json.dumps(message, ensure_ascii=False),
    )


def load_template() -> Template:
    return Template(load_prompt(PROMPT_VERSION))


@dataclass(frozen=True)
class ChatReply:
    """A checked AI answer."""

    kind: ReplyKind
    language: Language
    text: str
    draft: Draft | None  # None for a question


@dataclass(frozen=True)
class ChatResult:
    reply: ChatReply | None  # None: every try failed (plan #10 → 502)
    calls: tuple[LLMCall, ...]  # one AIUsageLog row each


def check_answer(text: str, limits: DraftLimits) -> ChatReply:
    """Parse the LLM text and apply plan #9. Any problem → BadAnswerError."""
    try:
        answer = BreakdownAnswer.model_validate_json(text)
    except ValidationError as error:
        raise BadAnswerError("not the expected JSON") from error
    reply = answer.reply.strip()
    if not reply:
        raise BadAnswerError("empty reply")
    items = [
        SubtaskInput(item.title, item.estimated_minutes, item.reason)
        for item in answer.subtasks
    ]
    try:
        draft = check_reply(answer.kind, items, limits)
    except BadDraftError as error:
        raise BadAnswerError(str(error)) from error
    return ChatReply(answer.kind, answer.language, reply, draft)


class ChatWriter:
    """One LLM call per user message, checked; retried like FR-10 (plan #10, #24).

    A bad answer or a failed call is retried up to `max_retries` times, but only
    while less than `retry_budget_seconds` has passed since the first try
    (CheckedLLMCaller, shared with the project chat).
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        limits: DraftLimits,
        max_retries: int,
        input_price_per_1m: float,
        output_price_per_1m: float,
        retry_budget_seconds: float = DEFAULT_RETRY_BUDGET_SECONDS,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.limits = limits
        self.caller = CheckedLLMCaller(
            llm,
            max_retries=max_retries,
            input_price_per_1m=input_price_per_1m,
            output_price_per_1m=output_price_per_1m,
            retry_budget_seconds=retry_budget_seconds,
            clock=clock,
        )
        self.template = load_template()

    async def write(
        self, task: BreakdownTask, history: Sequence[ChatLine], message: str
    ) -> ChatResult:
        prompt = build_prompt(self.template, task, history, message, self.limits)
        result = await self.caller.call(
            prompt,
            BreakdownAnswer.model_json_schema(),
            lambda text: check_answer(text, self.limits),
        )
        return ChatResult(reply=result.value, calls=result.calls)
