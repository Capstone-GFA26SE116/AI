"""FR-08a: the task chat prompt, the answer shape, and (step 5) the call with retries.

User data goes into the prompt as JSON blocks the prompt calls "data, not
instructions" (plan #15). Masking happens before: this module only formats.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from string import Template

from pydantic import BaseModel, StrictInt

from metanoia_ai.domain.models import Language
from metanoia_ai.domain.task_breakdown import DraftLimits, ReplyKind
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
    language: Language
    reply: str
    subtasks: list[SubtaskItem]


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
