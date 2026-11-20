"""FR-08b-3: talk the project through with the AI before planning. Pure rules, no I/O.

The AI answers a discussion message with a reply and a short checklist: what is
clear, what is still missing, and the risks it sees. No milestones, no tasks.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from metanoia_ai.domain.forbidden_terms import has_internal_name
from metanoia_ai.domain.task_breakdown import BadDraftError

MAX_NOTE_LINES = 8  # per list
MAX_NOTE_CHARS = 200  # per line
MAX_REPLY_CHARS = 4000  # FR-08b-5: room for an email or a comparison


class ChatMode(StrEnum):
    """What a project chat message asks for. Values are sent in the API."""

    DISCUSS = "discuss"  # talk it through: a checklist, never a plan
    PLAN = "plan"  # FR-08b-1: a question or a draft (the default)


@dataclass(frozen=True)
class DiscussionNotes:
    """The checklist of one discussion answer."""

    clear: tuple[str, ...]  # what the AI understood
    missing: tuple[str, ...]  # unknown AND blocks the plan: deliverables, deadline
    risks: tuple[str, ...]  # what could go wrong
    ready_to_plan: bool  # the AI's hint; the user may plan anyway
    questions: tuple[str, ...] = ()  # worth asking, but the plan can be made without


def clean_lines(lines: Iterable[object], name: str) -> tuple[str, ...]:
    """Trimmed, non-empty lines. Too many, too long or internal names → BadDraftError."""
    kept = []
    for line in lines:
        if not isinstance(line, str):
            raise BadDraftError(f"{name}: every line must be text")
        text = " ".join(line.split())
        if not text:
            continue
        if len(text) > MAX_NOTE_CHARS:
            raise BadDraftError(
                f"{name}: a line is longer than {MAX_NOTE_CHARS} characters"
            )
        if has_internal_name(text):
            raise BadDraftError(f"{name}: shows an internal name")
        kept.append(text)
    if len(kept) > MAX_NOTE_LINES:
        raise BadDraftError(f"{name}: more than {MAX_NOTE_LINES} lines")
    return tuple(kept)


def check_notes(
    clear: Iterable[object],
    missing: Iterable[object],
    risks: Iterable[object],
    ready_to_plan: bool,
    questions: Iterable[object] = (),
) -> DiscussionNotes:
    """The checked checklist. Something blocking still missing → not ready (plan #5)."""
    missing_lines = clean_lines(missing, "missing")
    return DiscussionNotes(
        clear=clean_lines(clear, "clear"),
        missing=missing_lines,
        risks=clean_lines(risks, "risks"),
        ready_to_plan=ready_to_plan and not missing_lines,
        questions=clean_lines(questions, "questions"),
    )
