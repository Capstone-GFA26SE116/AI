"""Turn the work sessions of a task into one actual duration (T_act)."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from metanoia_ai.domain.models import (
    ActualTimeSource,
    ConfidenceTier,
    FocusSessionRecord,
    SkipReason,
)

# Lower number = less reliable. The task keeps the least reliable source it has.
SOURCE_RELIABILITY = {
    ActualTimeSource.AUTO_TIMEOUT: 0,
    ActualTimeSource.SELF_REPORTED: 1,
    ActualTimeSource.TIMER: 2,
}

TIER_BY_SOURCE = {
    ActualTimeSource.TIMER: ConfidenceTier.HIGH,
    ActualTimeSource.SELF_REPORTED: ConfidenceTier.MEDIUM,
    ActualTimeSource.AUTO_TIMEOUT: ConfidenceTier.LOW,
}


def confidence_tier(source: ActualTimeSource) -> ConfidenceTier:
    """How much a learning sample can be trusted, from its time source."""
    return TIER_BY_SOURCE[source]


@dataclass(frozen=True)
class ActualTime:
    """Total actual minutes of a task and the least reliable source used."""

    minutes: int
    source: ActualTimeSource


def round_minutes(seconds: float) -> int:
    """Round seconds to whole minutes; 30 seconds or more rounds up."""
    return math.floor(seconds / 60 + 0.5)


def summarize_actual_time(
    sessions: Sequence[FocusSessionRecord],
) -> ActualTime | SkipReason:
    """Sum all sessions of a task, or return why the time cannot be used."""
    if not sessions:
        return SkipReason.NO_ACTUAL_TIME
    if any(session.ended_at is None for session in sessions):
        return SkipReason.SESSION_RUNNING
    if any(not session.is_confirmed for session in sessions):
        return SkipReason.UNCONFIRMED_TIME

    total_seconds = sum(
        (session.ended_at - session.started_at).total_seconds() for session in sessions
    )
    minutes = round_minutes(total_seconds)
    if minutes <= 0:
        return SkipReason.NO_ACTUAL_TIME

    weakest = min(
        (session.source for session in sessions), key=SOURCE_RELIABILITY.__getitem__
    )
    return ActualTime(minutes=minutes, source=weakest)
