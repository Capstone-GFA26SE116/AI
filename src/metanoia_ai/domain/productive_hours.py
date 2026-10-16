"""FR-18 productive hours: a score in [0, 1] for each hour of the day. Pure code.

Start: the user's onboarding answer (blocks of hours they work well in).
Then (top plan only): learn from finished focus sessions, recomputed from scratch:
    score = (prior × W + good sessions) / (W + all sessions)
An hour with no session keeps its onboarding score.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from metanoia_ai.domain.memory_text import StopReason  # shared with FR-19
from metanoia_ai.domain.models import TaskType

HOURS = range(24)
SCORE_STEP = Decimal("0.001")  # UserCircadianProfile.Score is numeric(4,3)
# Prior of an hour the user never answered for (no onboarding yet, #7).
NEUTRAL_PRIOR = Decimal("0.500")


class OnboardingBlock(StrEnum):
    """A part of the day the user can pick at onboarding. Values are sent in the API."""

    MORNING = "morning"
    MIDDAY = "midday"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    LATE_NIGHT = "late_night"


# Start hours of each block, both ends included. Hours 2-5 are in no block.
ONBOARDING_BLOCKS: dict[OnboardingBlock, tuple[int, ...]] = {
    OnboardingBlock.MORNING: (6, 7, 8, 9, 10),
    OnboardingBlock.MIDDAY: (11, 12, 13),
    OnboardingBlock.AFTERNOON: (14, 15, 16, 17),
    OnboardingBlock.EVENING: (18, 19, 20, 21),
    OnboardingBlock.LATE_NIGHT: (22, 23, 0, 1),
}


class ScoreSource(StrEnum):
    """Where an hour's score comes from (UserCircadianProfile.Source)."""

    ONBOARDING = "onboarding"
    LEARNED = "learned"


class InvalidBlocksError(ValueError):
    """The onboarding answer is not 1-5 different, known blocks."""


@dataclass(frozen=True)
class FocusSample:
    """One focus session, with what FR-18 needs to decide if it can be learned from."""

    started_at: datetime
    ended_at: datetime | None  # None: still running
    stop_reason: StopReason | None  # None only while running
    is_confirmed: bool
    task_type: TaskType


@dataclass(frozen=True)
class HourScore:
    """The result for one hour of the day."""

    hour: int
    score: Decimal
    prior: Decimal  # the onboarding score of this hour
    source: ScoreSource


def round_score(value: Decimal) -> Decimal:
    """3 decimals, kept inside [0, 1]."""
    rounded = value.quantize(SCORE_STEP, rounding=ROUND_HALF_UP)
    return min(max(rounded, Decimal(0)), Decimal(1))


def parse_blocks(names: Sequence[str]) -> tuple[OnboardingBlock, ...]:
    """Check the onboarding answer: 1-5 known blocks, no repeats. Keeps the order."""
    if not 1 <= len(names) <= len(OnboardingBlock):
        raise InvalidBlocksError(f"choose 1 to {len(OnboardingBlock)} blocks")
    if len(set(names)) != len(names):
        raise InvalidBlocksError("a block is repeated")
    try:
        return tuple(OnboardingBlock(name) for name in names)
    except ValueError as error:
        raise InvalidBlocksError("unknown block") from error


def onboarding_scores(
    blocks: Sequence[str], selected: Decimal, unselected: Decimal
) -> dict[int, Decimal]:
    """The 24 prior scores: hours in a chosen block get `selected`, others `unselected`."""
    chosen = {h for block in parse_blocks(blocks) for h in ONBOARDING_BLOCKS[block]}
    return {
        hour: round_score(selected if hour in chosen else unselected) for hour in HOURS
    }


def is_learnable(sample: FocusSample, window_start: datetime) -> bool:
    """Finished, confirmed, on a Flexible task, started inside the window (#11)."""
    return (
        sample.ended_at is not None
        and sample.is_confirmed
        and sample.task_type is TaskType.FLEXIBLE
        and sample.started_at >= window_start
    )


def is_good_session(sample: FocusSample, min_minutes: int) -> bool:
    """Completed (any length), or stopped for "Other" after at least `min_minutes` (#10)."""
    if sample.ended_at is None:
        return False
    if sample.stop_reason is StopReason.COMPLETED:
        return True
    long_enough = sample.ended_at - sample.started_at >= timedelta(minutes=min_minutes)
    return sample.stop_reason is StopReason.OTHER and long_enough


def session_hour(started_at: datetime, time_zone: ZoneInfo) -> int:
    """Hour of day the session started, in the user's time zone (#12, #13)."""
    if started_at.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return started_at.astimezone(time_zone).hour


def learned_scores(
    priors: Mapping[int, Decimal],
    sessions: Iterable[FocusSample],
    time_zone: ZoneInfo,
    prior_weight: Decimal,
    min_minutes: int,
) -> list[HourScore]:
    """All 24 hours, from scratch. `sessions` must already be learnable (finished).

    An hour missing from `priors` uses NEUTRAL_PRIOR (#7).
    """
    if prior_weight <= 0:
        raise ValueError("prior_weight must be > 0")
    total = dict.fromkeys(HOURS, 0)
    good = dict.fromkeys(HOURS, 0)
    for sample in sessions:
        if sample.ended_at is None:
            raise ValueError("a running session cannot be learned from")
        hour = session_hour(sample.started_at, time_zone)
        total[hour] += 1
        good[hour] += is_good_session(sample, min_minutes)

    result = []
    for hour in HOURS:
        prior = round_score(priors.get(hour, NEUTRAL_PRIOR))
        if total[hour] == 0:
            result.append(HourScore(hour, prior, prior, ScoreSource.ONBOARDING))
            continue
        score = (prior * prior_weight + good[hour]) / (prior_weight + total[hour])
        result.append(HourScore(hour, round_score(score), prior, ScoreSource.LEARNED))
    return result
