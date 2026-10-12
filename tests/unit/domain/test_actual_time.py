from datetime import UTC, datetime, timedelta

import pytest

from metanoia_ai.domain.actual_time import (
    ActualTime,
    confidence_tier,
    round_minutes,
    summarize_actual_time,
)
from metanoia_ai.domain.models import (
    ActualTimeSource,
    ConfidenceTier,
    FocusSessionRecord,
    SkipReason,
)

TIMER = ActualTimeSource.TIMER
SELF = ActualTimeSource.SELF_REPORTED
AUTO = ActualTimeSource.AUTO_TIMEOUT
TUESDAY_1PM = datetime(2026, 10, 13, 13, 0, tzinfo=UTC)


def session(
    minutes: float | None,
    source: ActualTimeSource = TIMER,
    confirmed: bool = True,
    start: datetime = TUESDAY_1PM,
) -> FocusSessionRecord:
    """A session lasting `minutes`; None means it is still running."""
    end = None if minutes is None else start + timedelta(minutes=minutes)
    return FocusSessionRecord(start, end, source, confirmed)


# --- round_minutes ---


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, 0), (29, 0), (30, 1), (89, 1), (90, 2), (5400, 90)],
)
def test_round_minutes(seconds, expected):
    assert round_minutes(seconds) == expected


# --- summarize_actual_time ---


def test_example_from_plan_timer_plus_self_report():
    # Tuesday 50' with the timer, Wednesday 40' self-reported.
    wednesday = TUESDAY_1PM + timedelta(days=1)
    result = summarize_actual_time([session(50), session(40, SELF, start=wednesday)])
    assert result == ActualTime(minutes=90, source=SELF)


def test_only_timer_sessions_keep_timer():
    assert summarize_actual_time([session(30), session(45)]) == ActualTime(75, TIMER)


def test_auto_timeout_is_the_weakest_source():
    result = summarize_actual_time([session(30), session(20, SELF), session(60, AUTO)])
    assert result == ActualTime(110, AUTO)


def test_seconds_are_summed_before_rounding():
    # 20.4' + 20.4' = 40.8' -> 41, not 20 + 20.
    assert summarize_actual_time([session(20.4), session(20.4)]).minutes == 41


def test_no_sessions_means_no_actual_time():
    assert summarize_actual_time([]) == SkipReason.NO_ACTUAL_TIME


def test_session_shorter_than_30_seconds_means_no_actual_time():
    assert summarize_actual_time([session(0.4)]) == SkipReason.NO_ACTUAL_TIME


def test_running_session_blocks_learning():
    assert summarize_actual_time([session(50), session(None)]) == (
        SkipReason.SESSION_RUNNING
    )


def test_unconfirmed_auto_timeout_blocks_learning():
    result = summarize_actual_time([session(50), session(180, AUTO, confirmed=False)])
    assert result == SkipReason.UNCONFIRMED_TIME


def test_confirmed_auto_timeout_is_used():
    result = summarize_actual_time([session(180, AUTO, confirmed=True)])
    assert result == ActualTime(180, AUTO)


# --- confidence_tier ---


@pytest.mark.parametrize(
    ("source", "tier"),
    [
        (TIMER, ConfidenceTier.HIGH),
        (SELF, ConfidenceTier.MEDIUM),
        (AUTO, ConfidenceTier.LOW),
    ],
)
def test_confidence_tier(source, tier):
    assert confidence_tier(source) == tier


def test_every_source_has_a_tier():
    # Adding a new source without a tier must fail here, not in production.
    for source in ActualTimeSource:
        assert isinstance(confidence_tier(source), ConfidenceTier)
