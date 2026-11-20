"""FR-08c: one file → several projects. Pure rules, no I/O.

Step 1 (preview): the AI splits the file into projects; `check_preview` cleans
what it returned. Step 2 (create): the user sends the edited list back;
`check_import` refuses anything that breaks a rule.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from metanoia_ai.domain.task_breakdown import BadDraftError, clean_title

MAX_IMPORT_PROJECTS = 20
MAX_PROJECT_NAME_CHARS = 150  # Project.Name is varchar(150)
MAX_DESCRIPTION_CHARS = 1000
MAX_SOURCE_CHARS = 200
MAX_BRIEF_CHARS = 6000


@dataclass(frozen=True)
class ImportInput:
    """One project as received (from the LLM or the user), not checked yet."""

    name: object
    brief: object
    description: object = None
    deadline: date | None = None
    source: object = None


@dataclass(frozen=True)
class ImportedProject:
    name: str
    brief: str  # this project's part of the file; the first message of its chat
    description: str | None = None
    deadline: date | None = None
    source: str | None = None  # where in the file, e.g. "Sheet Yêu cầu, dòng 2–14"


def _text(value: object, limit: int) -> str | None:
    """Trimmed one-line text cut to `limit`; empty → None."""
    if not isinstance(value, str):
        return None
    text = clean_title(value)[:limit].rstrip()
    return text or None


def _brief(value: object) -> str | None:
    """Keeps line breaks (rows of a sheet); cut to MAX_BRIEF_CHARS."""
    if not isinstance(value, str):
        return None
    lines = [" ".join(line.split()) for line in value.strip().splitlines()]
    text = "\n".join(line for line in lines if line)[:MAX_BRIEF_CHARS].rstrip()
    return text or None


def _check_count(items: Sequence[ImportInput]) -> None:
    if not 1 <= len(items) <= MAX_IMPORT_PROJECTS:
        raise BadDraftError(f"need 1-{MAX_IMPORT_PROJECTS} projects, got {len(items)}")


def check_preview(
    items: Sequence[ImportInput], today: date
) -> tuple[tuple[ImportedProject, ...], int]:
    """What the AI proposed, cleaned: long texts cut, past deadlines dropped.

    Returns the projects and how many past deadlines were dropped. Empty or
    repeated names, or an empty brief → BadDraftError (the answer is retried).
    """
    _check_count(items)
    projects: list[ImportedProject] = []
    seen: set[str] = set()
    dropped_dates = 0
    for number, item in enumerate(items, start=1):
        project = _checked(item, number, seen)
        if project.deadline is not None and project.deadline < today:
            project = ImportedProject(
                project.name, project.brief, project.description, None, project.source
            )
            dropped_dates += 1
        projects.append(project)
    return tuple(projects), dropped_dates


def check_import(
    items: Sequence[ImportInput], today: date
) -> tuple[ImportedProject, ...]:
    """The list the user wants created. Any broken rule → BadDraftError (422)."""
    _check_count(items)
    seen: set[str] = set()
    projects = []
    for number, item in enumerate(items, start=1):
        if (
            isinstance(item.name, str)
            and len(item.name.strip()) > MAX_PROJECT_NAME_CHARS
        ):
            raise BadDraftError(
                f"project {number}: name over {MAX_PROJECT_NAME_CHARS} chars"
            )
        project = _checked(item, number, seen)
        if project.deadline is not None and project.deadline < today:
            raise BadDraftError(f"project {number}: deadline in the past")
        projects.append(project)
    return tuple(projects)


def _checked(item: ImportInput, number: int, seen: set[str]) -> ImportedProject:
    where = f"project {number}"
    name = _text(item.name, MAX_PROJECT_NAME_CHARS)
    if name is None:
        raise BadDraftError(f"{where}: empty name")
    key = name.casefold()
    if key in seen:
        raise BadDraftError(f"{where}: same name twice")
    seen.add(key)
    brief = _brief(item.brief)
    if brief is None:
        raise BadDraftError(f"{where}: empty brief")
    return ImportedProject(
        name=name,
        brief=brief,
        description=_text(item.description, MAX_DESCRIPTION_CHARS),
        deadline=item.deadline,
        source=_text(item.source, MAX_SOURCE_CHARS),
    )


def projects_left(limit: int | None, used: int) -> int | None:
    """How many more projects the plan allows; None = no limit (like the Backend)."""
    if limit is None:
        return None
    return max(limit - used, 0)


FIRST_MESSAGE = {
    "vi": "Nội dung từ file {file}{where}:\n\n{brief}",
    "en": "From the file {file}{where}:\n\n{brief}",
}


def first_message(file_name: str, project: ImportedProject, language: str) -> str:
    """The brief as the first user message of the project's chat (FR-08c AC4)."""
    where = f" ({project.source})" if project.source else ""
    template = FIRST_MESSAGE.get(language, FIRST_MESSAGE["vi"])
    return template.format(file=file_name, where=where, brief=project.brief)
