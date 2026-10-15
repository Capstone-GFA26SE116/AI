"""FR-19: the text of a task memory. Pure functions, no I/O."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from metanoia_ai.domain.actual_time import round_minutes

EMAIL_MASK = "[email]"
PHONE_MASK = "[phone]"
CLIENT_MASK = "[client]"

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 9 or more digits, optionally starting with "+", with at most one space, "."
# or "-" between digits. Dates like 14-10-2026 have only 8 digits, so they stay.
PHONE = re.compile(r"(?<![\w+])\+?\d(?:[ .\-]?\d){8,}(?!\d)")


class MemoryOutcome(StrEnum):
    """How the task ended. Written in the memory text as "Done" / "Skipped"."""

    DONE = "Done"
    SKIPPED = "Skipped"


class StopReason(StrEnum):
    """Why a focus session stopped (FocusSession.StopReason)."""

    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"
    OTHER = "other"


# Counted in the text, in this order. Completed is not a problem, so it is left out.
COUNTED_REASONS = (StopReason.INTERRUPTED, StopReason.BLOCKED, StopReason.OTHER)


@dataclass(frozen=True)
class MemorySession:
    """The parts of one focus session that go into a memory."""

    started_at: datetime
    ended_at: datetime | None  # None: still running
    stop_reason: StopReason | None
    note: str | None


@dataclass(frozen=True)
class MemoryDraft:
    """A memory ready to save: the text to embed and the numbers kept in columns."""

    content: str
    outcome: MemoryOutcome
    estimated_minutes: int
    actual_minutes: int


def mask_contact(text: str, client_name: str | None) -> str:
    """Hide emails, phone numbers and the project's client name.

    Emails go first, so a client name inside an email address cannot leave
    half of the address behind. The client name is matched as a whole word,
    ignoring case.
    """
    text = EMAIL.sub(EMAIL_MASK, text)
    text = PHONE.sub(PHONE_MASK, text)
    if client_name and client_name.strip():
        # Not \b: a name may start or end with "(" or "." where \b never matches.
        name = re.compile(
            rf"(?<!\w){re.escape(client_name.strip())}(?!\w)", re.IGNORECASE
        )
        text = name.sub(CLIENT_MASK, text)
    return text


def stop_reasons_text(sessions: Sequence[MemorySession]) -> str | None:
    """E.g. "Interrupted 2 times, blocked 1 time". None when nothing to say."""
    finished = [s for s in sessions if s.ended_at is not None]
    parts = []
    for reason in COUNTED_REASONS:
        count = sum(1 for s in finished if s.stop_reason is reason)
        if count:
            parts.append(f"{reason.value} {count} {'time' if count == 1 else 'times'}")
    if not parts:
        return None
    text = ", ".join(parts)
    return text[0].upper() + text[1:]


def session_notes(
    sessions: Sequence[MemorySession], client_name: str | None
) -> list[str]:
    """Non-empty notes, oldest first, with contact details hidden."""
    ordered = sorted(sessions, key=lambda s: s.started_at)
    return [
        mask_contact(s.note.strip(), client_name)
        for s in ordered
        if s.note and s.note.strip()
    ]


def end_sentence(text: str) -> str:
    """Add a full stop unless the text already ends a sentence."""
    return text if text.endswith((".", "!", "?")) else text + "."


def build_memory_text(
    title: str,
    domain_name: str,
    outcome: MemoryOutcome,
    sessions: Sequence[MemorySession],
    client_name: str | None,
    max_chars: int,
) -> str:
    """title: {title} | text: {domain}. {outcome}. {stop reasons}. Notes: {notes}

    Too long: drop the oldest notes first. The title is never cut, unless the
    text is still too long without any notes.
    """
    head = f"title: {mask_contact(title.strip(), client_name)} | text: "
    head += f"{domain_name}. {outcome.value}."
    reasons = stop_reasons_text(sessions)
    if reasons:
        head += f" {reasons}."

    notes = session_notes(sessions, client_name)
    while True:
        text = head + (f" Notes: {end_sentence('; '.join(notes))}" if notes else "")
        if len(text) <= max_chars or not notes:
            return text[:max_chars]
        notes.pop(0)


def build_memory_draft(
    title: str,
    domain_name: str,
    outcome: MemoryOutcome,
    estimated_minutes: int,
    sessions: Sequence[MemorySession],
    client_name: str | None,
    max_chars: int,
) -> MemoryDraft:
    """The memory of one finished or skipped task.

    Actual minutes: finished sessions only, summed then rounded; none gives 0.
    """
    seconds = sum(
        (s.ended_at - s.started_at).total_seconds()
        for s in sessions
        if s.ended_at is not None
    )
    return MemoryDraft(
        content=build_memory_text(
            title, domain_name, outcome, sessions, client_name, max_chars
        ),
        outcome=outcome,
        estimated_minutes=estimated_minutes,
        actual_minutes=round_minutes(seconds),
    )
