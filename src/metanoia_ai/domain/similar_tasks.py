"""FR-09: find similar past tasks. Pure functions, no I/O."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from metanoia_ai.domain.memory_text import (
    CLIENT_MASK,
    EMAIL_MASK,
    PHONE_MASK,
    mask_contact,
)
from metanoia_ai.domain.models import EstimateSource

# Gemini Embedding 2 uses prefixes instead of task_type (FR-19 stores "title: … | text: …").
QUERY_PREFIX = "task: search result | query: "
MASKS = (EMAIL_MASK, PHONE_MASK, CLIENT_MASK)
# Letters and digits only (any language); "_" is not a letter.
WORD = re.compile(r"[^\W_]+")
MIN_TERM_LENGTH = 2
SCORE_DECIMALS = 4  # plan #17
RATIO_DECIMALS = 2


class SimilarOutcome(StrEnum):
    """How the past task ended (DB: Completed → done, NotDone → skipped)."""

    DONE = "done"
    SKIPPED = "skipped"


class MatchSource(StrEnum):
    """Which search found the task."""

    VECTOR = "vector"
    TEXT = "text"


@dataclass(frozen=True)
class PastTask:
    """One past task found by a search branch, best match first."""

    task_id: UUID
    title: str
    domain_name: str
    outcome: SimilarOutcome
    estimated_minutes: int
    actual_minutes: int
    finished_at: datetime | None  # Task.CompletedAt; may be empty
    estimate_source: EstimateSource = EstimateSource.USER

    @property
    def exact_ratio(self) -> float | None:
        """T_act / T_est, not rounded. None when it says nothing about the user:
        skipped, no time measured, or the estimate was the AI's (FR-17b)."""
        if (
            self.outcome is not SimilarOutcome.DONE
            or self.actual_minutes <= 0
            or self.estimate_source is EstimateSource.AI
        ):
            return None
        return self.actual_minutes / self.estimated_minutes


@dataclass(frozen=True)
class SimilarTask:
    """A result: the past task, its RRF score and the searches that found it."""

    task: PastTask
    score: float  # rounded to 4 decimals
    matched_by: tuple[MatchSource, ...]

    @property
    def ratio(self) -> float | None:
        """T_act / T_est, 2 decimals. None for skipped, unmeasured or AI-estimated tasks."""
        ratio = self.task.exact_ratio
        return None if ratio is None else round(ratio, RATIO_DECIMALS)


@dataclass(frozen=True)
class SimilarSummary:
    count: int
    done_count: int
    average_ratio: float | None  # None when no result has a ratio


def build_search_query(title: str, domain_name: str, client_name: str | None) -> str:
    """The text to embed: masked like the stored memories, so "[client]" matches on both sides."""
    return f"{QUERY_PREFIX}{mask_contact(title, client_name)}. {domain_name}"


def search_terms(title: str, domain_name: str, client_name: str | None) -> str | None:
    """Full-text query "word1 | word2 | …" (OR), or None when no word is left.

    Built from the masked title and the domain name only (not the prefix).
    Mask tokens and 1-letter words are dropped; only letters and digits stay,
    so the result is always safe for to_tsquery.
    """
    text = f"{mask_contact(title, client_name)} {domain_name}"
    for mask in MASKS:
        text = text.replace(mask, " ")
    words: list[str] = []
    for word in WORD.findall(text.lower()):
        if len(word) >= MIN_TERM_LENGTH and word not in words:
            words.append(word)
    return " | ".join(words) or None


def rrf_merge(
    vector: Sequence[PastTask], text: Sequence[PastTask], k: int, limit: int
) -> list[SimilarTask]:
    """Reciprocal Rank Fusion: score = sum of 1 / (k + rank), rank starts at 1.

    A task in both lists adds both scores. Ties (#16): tasks with a finish time
    first, newer first, then by task id so the order is always the same.
    """
    scores: dict[UUID, float] = {}
    sources: dict[UUID, list[MatchSource]] = {}
    tasks: dict[UUID, PastTask] = {}
    for source, found in ((MatchSource.VECTOR, vector), (MatchSource.TEXT, text)):
        for rank, task in enumerate(found, start=1):
            scores[task.task_id] = scores.get(task.task_id, 0.0) + 1 / (k + rank)
            sources.setdefault(task.task_id, []).append(source)
            tasks.setdefault(task.task_id, task)

    def order(task_id: UUID) -> tuple:
        finished = tasks[task_id].finished_at
        newest_first = -finished.timestamp() if finished else 0.0
        return (-scores[task_id], finished is None, newest_first, str(task_id))

    return [
        SimilarTask(
            task=tasks[task_id],
            score=round(scores[task_id], SCORE_DECIMALS),
            matched_by=tuple(sources[task_id]),
        )
        for task_id in sorted(scores, key=order)[:limit]
    ]


def summarize_similar(results: Sequence[SimilarTask]) -> SimilarSummary:
    """How many results, how many done, and their average T_act / T_est.

    The average uses the exact ratios of done tasks with measured time and the
    user's own estimate, then rounds to 2 decimals. None when no result has a ratio.
    """
    done = [r.task for r in results if r.task.outcome is SimilarOutcome.DONE]
    ratios = [t.exact_ratio for t in done if t.exact_ratio is not None]
    average = round(sum(ratios) / len(ratios), RATIO_DECIMALS) if ratios else None
    return SimilarSummary(
        count=len(results), done_count=len(done), average_ratio=average
    )
