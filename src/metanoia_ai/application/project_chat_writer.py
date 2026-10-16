"""FR-08b-1: the project chat prompt and the answer shape (schema sent to Gemini).

User data goes into the prompt as JSON blocks the prompt calls "data, not
instructions" (like FR-08a). Masking happens before: this module only formats.
"""

import copy
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from string import Template
from typing import Any
from uuid import UUID

from pydantic import BaseModel, StrictInt, field_validator

from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_breakdown import (
    MAX_TASK_TITLE_CHARS,
    DraftMilestone,
    ProjectLimits,
)
from metanoia_ai.domain.task_breakdown import ReplyKind
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
