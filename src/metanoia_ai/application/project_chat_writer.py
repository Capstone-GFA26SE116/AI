"""FR-08b-1: the project chat prompt, the answer shape, and the call with retries.

User data goes into the prompt as JSON blocks the prompt calls "data, not
instructions" (like FR-08a). Masking happens before: this module only formats.
"""

import copy
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from string import Template
from typing import Any
from uuid import UUID

from pydantic import BaseModel, StrictInt, ValidationError, field_validator

from metanoia_ai.application.llm_chat import CheckedLLMCaller
from metanoia_ai.application.ports.llm_client import LLMClient
from metanoia_ai.application.reason_writer import BadAnswerError, LLMCall
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_breakdown import (
    MAX_TASK_TITLE_CHARS,
    DraftMilestone,
    MilestoneInput,
    ProjectDraft,
    ProjectLimits,
    TaskInput,
    check_project_reply,
    drop_duplicates,
    resolve_deadlines,
)
from metanoia_ai.domain.task_breakdown import BadDraftError, ReplyKind
from metanoia_ai.prompts import load_prompt

PROMPT_VERSION = "project_breakdown_v1"


class ProjectTaskItem(BaseModel):
    title: str
    domain_id: str  # an enum of the active domain ids in the schema sent (#5)
    # Strict: 30.0 or "30" is a bad answer, not something to fix silently.
    estimated_minutes: StrictInt
    reason: str


class ProjectMilestoneItem(BaseModel):
    title: str
    deadline: date | None  # None: the project deadline (#6)
    tasks: list[ProjectTaskItem]


class ProjectAnswer(BaseModel):
    """The JSON shape the LLM must return."""

    kind: ReplyKind
    language: Language = Language.VI
    reply: str
    milestones: list[ProjectMilestoneItem]
    reminders: list[str]

    @field_validator("language", mode="before")
    @classmethod
    def default_language(cls, value: object) -> object:
        """Missing or unknown language is not worth a retry: use Vietnamese."""
        return value if value in set(Language) else Language.VI


def answer_schema(domain_ids: Sequence[UUID]) -> dict[str, Any]:
    """The answer schema with `domain_id` limited to these ids (#5)."""
    schema = copy.deepcopy(ProjectAnswer.model_json_schema())
    domain = schema["$defs"]["ProjectTaskItem"]["properties"]["domain_id"]
    domain["enum"] = [str(domain_id) for domain_id in domain_ids]
    return schema


@dataclass(frozen=True)
class PromptDomain:
    """An active domain the AI may use, with how off the user usually is in it."""

    domain_id: UUID
    name: str
    usually_extra_percent: int  # round((K − 1) × 100); no K in the prompt


@dataclass(frozen=True)
class PromptProject:
    """What the LLM may know about the project (#3). Texts already masked."""

    name: str
    description: str | None  # left out of the prompt when empty
    deadline: date | None
    existing_milestones: tuple[str, ...] = ()
    existing_tasks: tuple[str, ...] = ()  # newest first, at most the config limit


@dataclass(frozen=True)
class ProjectChatLine:
    """One earlier message. AI lines carry their draft, if they had one."""

    role: str  # "user" / "assistant"
    text: str  # user text already masked
    draft: tuple[DraftMilestone, ...] = ()


def project_to_json(project: PromptProject) -> dict[str, object]:
    data: dict[str, object] = {
        "name": project.name,
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "existing_milestones": list(project.existing_milestones),
        "existing_tasks": list(project.existing_tasks),
    }
    if project.description:
        data["description"] = project.description
    return data


def domains_to_json(domains: Sequence[PromptDomain]) -> list[dict[str, object]]:
    return [
        {
            "id": str(d.domain_id),
            "name": d.name,
            "usually_extra_percent": d.usually_extra_percent,
        }
        for d in domains
    ]


def line_to_json(line: ProjectChatLine) -> dict[str, object]:
    data: dict[str, object] = {"role": line.role, "text": line.text}
    if line.draft:
        data["draft"] = [
            {
                "title": m.title,
                "deadline": m.deadline.isoformat() if m.deadline else None,
                "tasks": [
                    {"title": t.title, "estimated_minutes": t.estimated_minutes}
                    for t in m.tasks
                ],
            }
            for m in line.draft
        ]
    return data


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_prompt(
    template: Template,
    project: PromptProject,
    domains: Sequence[PromptDomain],
    history: Sequence[ProjectChatLine],
    message: str,
    limits: ProjectLimits,
    today: date,
) -> str:
    """Fill the prompt. Every user-written text goes in as a JSON string."""
    return template.substitute(
        max_milestones=limits.max_milestones,
        max_tasks_per_milestone=limits.max_tasks_per_milestone,
        max_tasks=limits.max_tasks,
        min_minutes=limits.min_minutes,
        max_minutes=limits.max_minutes,
        max_milestone_title_chars=limits.max_milestone_title_chars,
        max_task_title_chars=MAX_TASK_TITLE_CHARS,
        today=today.isoformat(),
        project=dumps(project_to_json(project)),
        domains=dumps(domains_to_json(domains)),
        history=dumps([line_to_json(line) for line in history]),
        message=json.dumps(message, ensure_ascii=False),
    )


def load_template() -> Template:
    return Template(load_prompt(PROMPT_VERSION))


# --- checking the answer and calling the LLM (#14-#17, #24, #30) ---


@dataclass(frozen=True)
class DraftContext:
    """What an AI draft is checked against."""

    domains: Mapping[UUID, str]  # active domains: id → name
    project_deadline: date | None
    today: date  # in the user's time zone
    existing_titles: tuple[str, ...]  # every task title of the project, masked


@dataclass(frozen=True)
class ProjectReply:
    """A checked AI answer."""

    kind: ReplyKind
    language: Language
    text: str
    draft: ProjectDraft | None  # None for a question; deadlines filled, no duplicates


@dataclass(frozen=True)
class ProjectChatResult:
    reply: ProjectReply | None  # None: every try failed (→ 502)
    calls: tuple[LLMCall, ...]  # one AIUsageLog row each


def to_inputs(answer: ProjectAnswer) -> list[MilestoneInput]:
    return [
        MilestoneInput(
            title=m.title,
            deadline=m.deadline,
            tasks=[
                TaskInput(t.title, t.domain_id, t.estimated_minutes, t.reason)
                for t in m.tasks
            ],
        )
        for m in answer.milestones
    ]


def check_answer(
    text: str, context: DraftContext, limits: ProjectLimits
) -> ProjectReply:
    """Parse the LLM text and apply #14-#17. Any problem → BadAnswerError."""
    try:
        answer = ProjectAnswer.model_validate_json(text)
    except ValidationError as error:
        raise BadAnswerError("not the expected JSON") from error
    reply = answer.reply.strip()
    if not reply:
        raise BadAnswerError("empty reply")
    try:
        draft = check_project_reply(
            answer.kind, to_inputs(answer), answer.reminders, context.domains, limits
        )
        if draft is not None:
            draft = resolve_deadlines(
                draft, context.project_deadline, context.today, answer.language
            )
            draft = drop_duplicates(draft, context.existing_titles, answer.language)
    except BadDraftError as error:
        raise BadAnswerError(str(error)) from error
    return ProjectReply(answer.kind, answer.language, reply, draft)


def retry_hint(reason: str) -> str:
    """Added to the prompt after a rejected answer (#15, #30). `reason` has no user text."""
    return (
        f"Your previous answer was rejected: {reason}. "
        "Answer again and follow every rule above."
    )


class ProjectChatWriter:
    """One checked LLM call per user message, retried within a time budget (#24)."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        limits: ProjectLimits,
        max_retries: int,
        input_price_per_1m: float,
        output_price_per_1m: float,
        retry_budget_seconds: float,
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
        self,
        project: PromptProject,
        domains: Sequence[PromptDomain],
        history: Sequence[ProjectChatLine],
        message: str,
        context: DraftContext,
    ) -> ProjectChatResult:
        prompt = build_prompt(
            self.template,
            project,
            domains,
            history,
            message,
            self.limits,
            context.today,
        )
        result = await self.caller.call(
            prompt,
            answer_schema([d.domain_id for d in domains]),
            lambda text: check_answer(text, context, self.limits),
            retry_hint,
        )
        return ProjectChatResult(reply=result.value, calls=result.calls)
