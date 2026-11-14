"""Is the AI right and trustworthy? Three numbers for Problem #6 (plans/accuracy-metrics.md).

M1 — the estimate corrected with K is closer to the real time than the user's own guess.
M2 — how many proposals are approved, and how many approved slots stay unedited.
M3 — proposed slots never overlap each other or Fixed tasks; slots after the deadline.

Pure functions: no DB, so they are tested with hand-made rows.
"""

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median

MIN_SAMPLES = 10  # fewer than this: print the numbers, but flag them as too few
OFF_CALENDAR = frozenset({"Proposed", "Cancelled"})  # never approved, or thrown away


# --- M1: estimate accuracy ---


@dataclass(frozen=True)
class LearnedTask:
    """One KBiasUpdateLog row: the user's guess, the real time and K before learning."""

    estimated_minutes: int
    actual_minutes: int
    old_k: float


@dataclass(frozen=True)
class EstimateAccuracy:
    samples: int
    median_user_error: float | None  # |act − est| / act
    median_ai_error: float | None  # |act − est × K| / act
    ai_closer_share: float | None  # share of tasks where est × K was strictly closer
    too_few: bool


def relative_error(predicted: float, actual: float) -> float:
    return abs(actual - predicted) / actual


def estimate_accuracy(tasks: Sequence[LearnedTask]) -> EstimateAccuracy:
    if not tasks:
        return EstimateAccuracy(0, None, None, None, too_few=True)
    user = [relative_error(t.estimated_minutes, t.actual_minutes) for t in tasks]
    ai = [
        relative_error(t.estimated_minutes * t.old_k, t.actual_minutes) for t in tasks
    ]
    closer = sum(a < u for a, u in zip(ai, user, strict=True))
    return EstimateAccuracy(
        samples=len(tasks),
        median_user_error=median(user),
        median_ai_error=median(ai),
        ai_closer_share=closer / len(tasks),
        too_few=len(tasks) < MIN_SAMPLES,
    )


# --- M2: approval ---


@dataclass(frozen=True)
class ProposedSlot:
    """A slot the AI proposed, with what happened to it afterwards."""

    proposal_id: str
    proposal_created_at: datetime
    status: str
    start: datetime
    end: datetime
    deadline: datetime | None
    edited: bool  # moved / resized by the user (see plan §1 #2)


@dataclass(frozen=True)
class ApprovalSummary:
    proposals_by_status: dict[str, int]
    approved_slots: int  # slots of approved proposals that reached the calendar
    unedited_slots: int
    unedited_share: float | None
    too_few: bool


def approval_summary(
    proposal_statuses: Sequence[str],
    slots: Sequence[ProposedSlot],
    approved_proposals: set[str],
) -> ApprovalSummary:
    kept = [
        s
        for s in slots
        if s.proposal_id in approved_proposals and s.status not in OFF_CALENDAR
    ]
    unedited = sum(not s.edited for s in kept)
    return ApprovalSummary(
        proposals_by_status=dict(Counter(proposal_statuses)),
        approved_slots=len(kept),
        unedited_slots=unedited,
        unedited_share=unedited / len(kept) if kept else None,
        too_few=len(kept) < MIN_SAMPLES,
    )


# --- M3: validity ---


@dataclass(frozen=True)
class FixedTask:
    start: datetime
    end: datetime
    created_at: datetime


@dataclass(frozen=True)
class Validity:
    checked_slots: int  # unedited proposed slots (edited ones show the user's choice)
    overlapping_slots: int  # overlap another slot of the same proposal
    fixed_overlaps: int  # overlap a Fixed task that existed when proposed
    after_deadline: int  # end after the task deadline (allowed with a warning, FR-10)


def overlaps(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    return a_start < b_end and b_start < a_end


def validity(slots: Sequence[ProposedSlot], fixed: Sequence[FixedTask]) -> Validity:
    checked = [s for s in slots if not s.edited]
    by_proposal: dict[str, list[ProposedSlot]] = defaultdict(list)
    for slot in checked:
        by_proposal[slot.proposal_id].append(slot)

    overlapping = 0
    for group in by_proposal.values():
        for slot in group:
            if any(
                other is not slot
                and overlaps(slot.start, slot.end, other.start, other.end)
                for other in group
            ):
                overlapping += 1

    fixed_overlaps = sum(
        any(
            f.created_at <= s.proposal_created_at
            and overlaps(s.start, s.end, f.start, f.end)
            for f in fixed
        )
        for s in checked
    )
    after_deadline = sum(s.deadline is not None and s.end > s.deadline for s in checked)
    return Validity(len(checked), overlapping, fixed_overlaps, after_deadline)
