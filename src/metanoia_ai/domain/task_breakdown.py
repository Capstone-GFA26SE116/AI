"""FR-08a: split a big task into subtasks. Pure rules, no I/O."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

MAX_TITLE_CHARS = 200  # SubTask.Title is varchar(200)


class ReplyKind(StrEnum):
    """What the AI answered: a question back, or a draft list of subtasks."""

    QUESTION = "question"
    DRAFT = "draft"


@dataclass(frozen=True)
class SubtaskInput:
    """One subtask as received (from the LLM or the user), not checked yet."""

    title: str
    estimated_minutes: object  # must turn out to be an int
    reason: str | None = None


@dataclass(frozen=True)
class Subtask:
    title: str
    estimated_minutes: int
    order_index: int  # 1, 2, … in list order
    reason: str | None = None


@dataclass(frozen=True)
class Draft:
    subtasks: tuple[Subtask, ...]

    @property
    def total_minutes(self) -> int:
        return sum(s.estimated_minutes for s in self.subtasks)


@dataclass(frozen=True)
class DraftLimits:
    """How many subtasks, and how many minutes each. From config."""

    min_subtasks: int
    max_subtasks: int
    min_minutes: int
    max_minutes: int


class BadDraftError(ValueError):
    """The draft breaks a rule. The message says which (no user data in it)."""


def clean_title(title: str) -> str:
    """Trim and squeeze spaces: "  Viết   API " → "Viết API"."""
    return " ".join(title.split())


def check_draft(items: Sequence[SubtaskInput], limits: DraftLimits) -> Draft:
    """Check a list of subtasks (plan #9 for AI drafts, #19 for manual edits).

    Titles: not empty, at most 200 characters, no two the same (ignoring case
    and extra spaces). Minutes: whole numbers within the limits. Order = list order.
    """
    if not limits.min_subtasks <= len(items) <= limits.max_subtasks:
        raise BadDraftError(
            f"need {limits.min_subtasks}-{limits.max_subtasks} subtasks, got {len(items)}"
        )
    subtasks: list[Subtask] = []
    seen: set[str] = set()
    for number, item in enumerate(items, start=1):
        title = clean_title(item.title) if isinstance(item.title, str) else ""
        if not title:
            raise BadDraftError(f"subtask {number}: empty title")
        if len(title) > MAX_TITLE_CHARS:
            raise BadDraftError(f"subtask {number}: title over {MAX_TITLE_CHARS} chars")
        if title.casefold() in seen:
            raise BadDraftError(f"subtask {number}: same title as another subtask")
        seen.add(title.casefold())

        minutes = item.estimated_minutes
        # bool is an int in Python; 60.0 is not a whole-number answer either.
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise BadDraftError(f"subtask {number}: minutes must be a whole number")
        if not limits.min_minutes <= minutes <= limits.max_minutes:
            raise BadDraftError(
                f"subtask {number}: minutes must be "
                f"{limits.min_minutes}-{limits.max_minutes}, got {minutes}"
            )
        reason = clean_title(item.reason) if item.reason else None
        subtasks.append(Subtask(title, minutes, number, reason or None))
    return Draft(tuple(subtasks))


def check_reply(
    kind: ReplyKind, items: Sequence[SubtaskInput], limits: DraftLimits
) -> Draft | None:
    """An AI answer: a question has no subtasks (None); a draft passes check_draft."""
    if kind is ReplyKind.QUESTION:
        if items:
            raise BadDraftError("a question must not list subtasks")
        return None
    if not items:
        raise BadDraftError("a draft must list subtasks")
    return check_draft(items, limits)
