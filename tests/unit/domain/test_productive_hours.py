from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.productive_hours import (
    NEUTRAL_PRIOR,
    ONBOARDING_BLOCKS,
    FocusSample,
    HourScore,
    InvalidBlocksError,
    OnboardingBlock,
    ScoreSource,
    StopReason,
    is_good_session,
    is_learnable,
    learned_scores,
    onboarding_scores,
    parse_blocks,
    round_score,
    session_hour,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")
SELECTED = Decimal("0.8")
UNSELECTED = Decimal("0.3")
W = Decimal(5)
MIN_MINUTES = 25
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def at_vn_hour(hour: int, day: int = 15) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=VN)


def sample(
    minutes: float | None = 60,
    reason: StopReason | None = StopReason.COMPLETED,
    start: datetime = NOW - timedelta(days=1),
    confirmed: bool = True,
    task_type: TaskType = TaskType.FLEXIBLE,
) -> FocusSample:
    """A session lasting `minutes`; None means it is still running."""
    end = None if minutes is None else start + timedelta(minutes=minutes)
    return FocusSample(start, end, reason if end else None, confirmed, task_type)


def learn(priors, sessions):
    return {h.hour: h for h in learned_scores(priors, sessions, VN, W, MIN_MINUTES)}


# --- T1: onboarding scores ---


def test_blocks_cover_the_planned_hours():
    assert ONBOARDING_BLOCKS[OnboardingBlock.MORNING] == (6, 7, 8, 9, 10)
    covered = [h for hours in ONBOARDING_BLOCKS.values() for h in hours]
    assert len(covered) == len(set(covered))  # no hour in two blocks
    assert set(range(24)) - set(covered) == {2, 3, 4, 5}


def test_morning_only():
    scores = onboarding_scores(["morning"], SELECTED, UNSELECTED)
    assert sorted(scores) == list(range(24))
    assert {h for h, s in scores.items() if s == Decimal("0.800")} == {6, 7, 8, 9, 10}
    assert all(
        s == Decimal("0.300") for h, s in scores.items() if h not in range(6, 11)
    )


def test_late_night_crosses_midnight():
    scores = onboarding_scores(["late_night"], SELECTED, UNSELECTED)
    assert {h for h, s in scores.items() if s == SELECTED} == {22, 23, 0, 1}


def test_hours_2_to_5_are_never_selected():
    every_block = [b.value for b in OnboardingBlock]
    scores = onboarding_scores(every_block, SELECTED, UNSELECTED)
    assert [scores[h] for h in (2, 3, 4, 5)] == [UNSELECTED] * 4
    assert sum(s == SELECTED for s in scores.values()) == 20


def test_several_blocks():
    scores = onboarding_scores(["evening", "morning"], SELECTED, UNSELECTED)
    selected = {h for h, s in scores.items() if s == SELECTED}
    assert selected == {6, 7, 8, 9, 10, 18, 19, 20, 21}


def test_scores_are_rounded_to_3_decimals():
    scores = onboarding_scores(["morning"], Decimal("0.83333"), Decimal("0.1"))
    assert scores[6] == Decimal("0.833")


# --- T2: bad answers ---


@pytest.mark.parametrize(
    "names",
    [
        pytest.param([], id="empty"),
        pytest.param(
            ["morning", "midday", "afternoon", "evening", "late_night", "morning"],
            id="six",
        ),
        pytest.param(["morning", "morning"], id="repeated"),
        pytest.param(["night"], id="unknown"),
        pytest.param(["Morning"], id="wrong-case"),
    ],
)
def test_bad_blocks_are_rejected(names):
    with pytest.raises(InvalidBlocksError):
        parse_blocks(names)
    with pytest.raises(InvalidBlocksError):
        onboarding_scores(names, SELECTED, UNSELECTED)


def test_parse_keeps_order():
    assert parse_blocks(["evening", "morning"]) == (
        OnboardingBlock.EVENING,
        OnboardingBlock.MORNING,
    )


def test_all_five_blocks_are_allowed():
    assert len(parse_blocks([b.value for b in OnboardingBlock])) == 5


# --- T3: good session ---


@pytest.mark.parametrize(
    ("minutes", "reason", "good"),
    [
        (3, StopReason.COMPLETED, True),
        (25, StopReason.OTHER, True),
        (24, StopReason.OTHER, False),
        (24.9, StopReason.OTHER, False),
        (60, StopReason.INTERRUPTED, False),
        (60, StopReason.BLOCKED, False),
    ],
)
def test_is_good_session(minutes, reason, good):
    assert is_good_session(sample(minutes, reason), MIN_MINUTES) is good


def test_running_session_is_not_good():
    assert is_good_session(sample(None), MIN_MINUTES) is False


# --- is_learnable (#11, AC4) ---


def test_learnable_session():
    assert is_learnable(sample(), NOW - timedelta(days=90))


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(sample(None), id="running"),
        pytest.param(sample(confirmed=False), id="unconfirmed"),
        pytest.param(sample(task_type=TaskType.FIXED), id="fixed-task"),
        pytest.param(sample(start=NOW - timedelta(days=91)), id="too-old"),
    ],
)
def test_not_learnable(bad):
    assert not is_learnable(bad, NOW - timedelta(days=90))


def test_window_start_is_included():
    start = NOW - timedelta(days=90)
    assert is_learnable(sample(start=start), start)


# --- T4: session hour ---


def test_session_hour_uses_user_time_zone():
    assert session_hour(datetime(2026, 10, 16, 1, 30, tzinfo=UTC), VN) == 8


def test_session_hour_needs_time_zone():
    naive = datetime(2026, 10, 16, 1, 30)  # noqa: DTZ001 - no time zone on purpose
    with pytest.raises(ValueError, match="timezone-aware"):
        session_hour(naive, VN)


# --- T5: learned scores ---


def test_plan_examples():
    priors = onboarding_scores(["morning"], SELECTED, UNSELECTED)
    hour_9 = [sample(30, StopReason.COMPLETED, at_vn_hour(9))] + [
        sample(30, StopReason.INTERRUPTED, at_vn_hour(9, day=d)) for d in (12, 13, 14)
    ]
    hour_21 = [
        sample(30, StopReason.COMPLETED, at_vn_hour(21, day=10 + d)) for d in range(6)
    ]

    result = learn(priors, hour_9 + hour_21)

    assert result[9].score == Decimal("0.556")
    assert result[9].source is ScoreSource.LEARNED
    assert result[21].score == Decimal("0.682")
    assert result[21].prior == Decimal("0.300")


def test_hour_without_sessions_keeps_prior():
    priors = onboarding_scores(["morning"], SELECTED, UNSELECTED)
    result = learn(priors, [sample(30, StopReason.COMPLETED, at_vn_hour(9))])
    assert len(result) == 24
    assert result[8] == HourScore(8, SELECTED, SELECTED, ScoreSource.ONBOARDING)
    assert result[20].score == UNSELECTED
    assert result[20].source is ScoreSource.ONBOARDING


def test_missing_prior_is_neutral():
    result = learn({}, [sample(30, StopReason.INTERRUPTED, at_vn_hour(9))])
    assert result[9].prior == NEUTRAL_PRIOR
    assert result[9].score == Decimal("0.417")  # (0.5×5 + 0) / 6
    assert result[3].score == NEUTRAL_PRIOR


def test_session_counts_for_its_start_hour():
    # 08:50 → 09:40: counts for hour 8 only (#12).
    start = datetime(2026, 10, 15, 8, 50, tzinfo=VN)
    result = learn({}, [sample(50, StopReason.COMPLETED, start)])
    assert result[8].source is ScoreSource.LEARNED
    assert result[9].source is ScoreSource.ONBOARDING


def test_more_sessions_weigh_more():
    priors = onboarding_scores(["morning"], SELECTED, UNSELECTED)
    few = learn(priors, [sample(30, StopReason.COMPLETED, at_vn_hour(21))])
    many = learn(
        priors,
        [sample(30, StopReason.COMPLETED, at_vn_hour(21, day=d)) for d in range(1, 21)],
    )
    assert UNSELECTED < few[21].score < many[21].score


def test_same_input_same_result():
    priors = onboarding_scores(["evening"], SELECTED, UNSELECTED)
    sessions = [sample(30, StopReason.OTHER, at_vn_hour(h)) for h in (9, 19, 19)]
    assert learn(priors, sessions) == learn(priors, sessions)


def test_running_session_is_refused():
    with pytest.raises(ValueError, match="running"):
        learn({}, [sample(None)])


def test_weight_must_be_positive():
    with pytest.raises(ValueError, match="prior_weight"):
        learned_scores({}, [], VN, Decimal(0), MIN_MINUTES)


# --- T6: many sessions ---


def test_many_good_sessions_get_close_to_1():
    base = at_vn_hour(21)
    sessions = [
        sample(
            30,
            StopReason.COMPLETED,
            base - timedelta(days=i % 80) + timedelta(seconds=i),
        )
        for i in range(500)
    ]
    result = learn(onboarding_scores(["morning"], SELECTED, UNSELECTED), sessions)
    assert result[21].score == Decimal("0.993")  # (1.5 + 500) / 505
    assert result[21].score <= 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.5555", "0.556"), ("1.0004", "1.000"), ("1.2", "1"), ("-0.1", "0")],
)
def test_round_score(value, expected):
    assert round_score(Decimal(value)) == Decimal(expected)
