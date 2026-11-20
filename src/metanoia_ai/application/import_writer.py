"""FR-08c step 1: the LLM splits one file into projects (prompt, schema, checks)."""

from dataclasses import dataclass
from datetime import date
from string import Template

from pydantic import BaseModel, ValidationError

from metanoia_ai.application.llm_chat import CheckedLLMCaller, CheckedResult
from metanoia_ai.application.ports.llm_client import LLMClient
from metanoia_ai.application.project_chat_writer import LANGUAGE_NAMES, retry_hint
from metanoia_ai.application.reason_writer import BadAnswerError
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_import import (
    MAX_BRIEF_CHARS,
    MAX_IMPORT_PROJECTS,
    MAX_PROJECT_NAME_CHARS,
    ImportedProject,
    ImportInput,
    check_preview,
)
from metanoia_ai.domain.task_breakdown import BadDraftError
from metanoia_ai.prompts import load_prompt

IMPORT_PROMPT_VERSION = "project_import_v1"


class ImportProjectItem(BaseModel):
    name: str
    description: str = ""
    deadline: date | None
    source: str = ""
    brief: str


class ImportAnswer(BaseModel):
    """The JSON shape the LLM must return."""

    projects: list[ImportProjectItem]


@dataclass(frozen=True)
class ImportSplit:
    projects: tuple[ImportedProject, ...]
    dropped_deadlines: int  # past dates the AI gave, removed


def build_import_prompt(
    template: Template,
    file_name: str,
    document: str,
    today: date,
    language: Language,
) -> str:
    """File name and text are user data: the text is framed, never trusted."""
    return template.substitute(
        language_name=LANGUAGE_NAMES[language],
        max_projects=MAX_IMPORT_PROJECTS,
        max_name_chars=MAX_PROJECT_NAME_CHARS,
        max_brief_chars=MAX_BRIEF_CHARS,
        today=today.isoformat(),
        file_name=" ".join(file_name.replace('"', "'").split()),
        document=document.replace("</document", "</ document"),
    )


def check_import_answer(text: str, today: date) -> ImportSplit:
    """Parse and clean the LLM answer. Any problem → BadAnswerError."""
    try:
        answer = ImportAnswer.model_validate_json(text)
    except ValidationError as error:
        raise BadAnswerError("not the expected JSON") from error
    items = [
        ImportInput(p.name, p.brief, p.description, p.deadline, p.source)
        for p in answer.projects
    ]
    try:
        projects, dropped = check_preview(items, today)
    except BadDraftError as error:
        raise BadAnswerError(str(error)) from error
    return ImportSplit(projects, dropped)


class ImportWriter:
    def __init__(
        self,
        llm: LLMClient,
        *,
        max_retries: int,
        input_price_per_1m: float,
        output_price_per_1m: float,
        retry_budget_seconds: float,
    ) -> None:
        self.caller = CheckedLLMCaller(
            llm,
            max_retries=max_retries,
            input_price_per_1m=input_price_per_1m,
            output_price_per_1m=output_price_per_1m,
            retry_budget_seconds=retry_budget_seconds,
        )
        self.template = Template(load_prompt(IMPORT_PROMPT_VERSION))

    def render(
        self, file_name: str, document: str, today: date, language: Language
    ) -> str:
        return build_import_prompt(self.template, file_name, document, today, language)

    async def split(
        self, file_name: str, document: str, today: date, language: Language
    ) -> CheckedResult[ImportSplit]:
        return await self.caller.call(
            self.render(file_name, document, today, language),
            ImportAnswer.model_json_schema(),
            lambda text: check_import_answer(text, today),
            retry_hint,
        )
