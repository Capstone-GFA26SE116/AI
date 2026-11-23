"""FR-24: give each task of a group plan draft to a member (pure rules, no LLM).

Tasks go in deadline order. For each one the members are ranked like FR-23, with the
draft tasks already given to them counted as extra work, so one member does not get
everything. The reason is written by code from the same numbers (no extra LLM call).
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from metanoia_ai.domain.assignee_ranking import (
    Candidate,
    RankedCandidate,
    format_moment,
    format_range,
    hours_text,
    rank_candidates,
)
from metanoia_ai.domain.memory_text import EMAIL_MASK, PHONE_MASK
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_breakdown import ProjectDraft

MASKS = re.compile(
    r"\s*(?:" + "|".join(re.escape(m) for m in (EMAIL_MASK, PHONE_MASK)) + r")"
)


@dataclass(frozen=True)
class PlanTask:
    """One task of the draft, as the assignment needs it."""

    key: str | None  # the draft key ("m1-t2")
    domain_id: UUID
    estimated_minutes: int
    deadline: datetime  # end of its milestone day


@dataclass(frozen=True)
class TaskAssignment:
    task_key: str | None
    user_id: UUID | None  # None: no Leader / Member to give it to
    fits: bool | None
    reason: str | None


# (task, minutes already given to each member in this draft) → candidates
CandidatesFor = Callable[[PlanTask, Mapping[UUID, int]], Sequence[Candidate]]


def assign_tasks(
    tasks: Sequence[PlanTask], candidates_for: CandidatesFor, language: Language
) -> list[TaskAssignment]:
    """One assignment per task, in the order of `tasks`."""
    given: dict[UUID, int] = {}
    by_key: dict[int, TaskAssignment] = {}
    order = sorted(range(len(tasks)), key=lambda i: tasks[i].deadline)
    for i in order:
        task = tasks[i]
        ranked = rank_candidates(candidates_for(task, dict(given)))
        if not ranked:
            by_key[i] = TaskAssignment(task.key, None, None, None)
            continue
        best = ranked[0]
        member = best.candidate.user_id
        load = best.candidate.needed_minutes or task.estimated_minutes
        given[member] = given.get(member, 0) + load
        by_key[i] = TaskAssignment(
            task.key, member, best.fits, assignment_reason(best, task, language)
        )
    return [by_key[i] for i in range(len(tasks))]


def assignment_reason(best: RankedCandidate, task: PlanTask, language: Language) -> str:
    """One line from the numbers: free hours, the first open time, the deadline."""
    c = best.candidate
    zone = c.time_zone
    free = hours_text(c.free_minutes, language)
    deadline = format_moment(task.deadline, zone, language)
    first = format_range(c.free_times[0], zone, language) if c.free_times else None
    if language is Language.VI:
        if best.fits is False:
            return (
                f"Không ai đủ giờ trước hạn {deadline}; thành viên này còn "
                f"nhiều giờ rảnh nhất ({free})."
            )
        text = f"Còn {free} rảnh trước hạn {deadline}"
        return f"{text}; lịch trống {first}." if first else f"{text}."
    if best.fits is False:
        return (
            f"Nobody has enough time before {deadline}; this member has the most "
            f"free time ({free})."
        )
    text = f"{free} free before {deadline}"
    return f"{text}; calendar open {first}." if first else f"{text}."


def without_masks(text: str) -> str:
    """'Gửi email cho chị Lan [phone]' → 'Gửi email cho chị Lan'.

    The LLM only sees masked contacts, so it may copy a mask into a title; the
    title becomes a real group task, where a mask makes no sense.
    """
    return " ".join(MASKS.sub("", text).split()) or text


def clean_titles(draft: ProjectDraft) -> ProjectDraft:
    """The draft with masks removed from milestone, task and subtask titles."""
    return replace(
        draft,
        milestones=tuple(
            replace(
                m,
                title=without_masks(m.title),
                tasks=tuple(
                    replace(
                        t,
                        title=without_masks(t.title),
                        subtasks=tuple(
                            replace(s, title=without_masks(s.title)) for s in t.subtasks
                        ),
                    )
                    for t in m.tasks
                ),
            )
            for m in draft.milestones
        ),
    )
