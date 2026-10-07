import pytest

from metanoia_ai.domain.kbias import (
    INITIAL_K_BIAS,
    K_MAX,
    K_MIN,
    clamp_k_bias,
    proposed_minutes,
    should_learn,
    update_k_bias,
)
from metanoia_ai.domain.models import ActualTimeSource, TaskType

FLEX = TaskType.FLEXIBLE
TIMER = ActualTimeSource.TIMER


# --- clamp_k_bias ---


@pytest.mark.parametrize(
    ("k", "expected"),
    [(0.1, K_MIN), (0.5, 0.5), (1.3, 1.3), (3.0, 3.0), (9.0, K_MAX)],
)
def test_clamp_k_bias(k, expected):
    assert clamp_k_bias(k) == expected


# --- proposed_minutes ---


def test_proposed_minutes_example_from_spec():
    assert proposed_minutes(60, 1.3) == pytest.approx(78)


def test_proposed_minutes_adds_buffer():
    assert proposed_minutes(60, 1.3, buffer=10) == pytest.approx(88)


def test_proposed_minutes_uses_clamped_k():
    assert proposed_minutes(60, 10.0) == pytest.approx(180)


@pytest.mark.parametrize(("t_est", "buffer"), [(0, 0), (-5, 0), (60, -1)])
def test_proposed_minutes_rejects_bad_input(t_est, buffer):
    with pytest.raises(ValueError):
        proposed_minutes(t_est, 1.25, buffer)


# --- should_learn ---


def test_learns_from_normal_flexible_task():
    assert should_learn(FLEX, t_est=60, t_act=90, source=TIMER)


def test_skips_fixed_task():
    assert not should_learn(TaskType.FIXED, t_est=60, t_act=60, source=TIMER)


def test_skips_unknown_actual_time():
    assert not should_learn(FLEX, t_est=60, t_act=None, source=TIMER)


def test_skips_unconfirmed_auto_timeout():
    source = ActualTimeSource.AUTO_TIMEOUT
    assert not should_learn(FLEX, t_est=60, t_act=180, source=source)


def test_learns_from_confirmed_auto_timeout():
    source = ActualTimeSource.AUTO_TIMEOUT
    assert should_learn(FLEX, t_est=60, t_act=180, source=source, user_confirmed=True)


def test_learns_from_self_reported():
    source = ActualTimeSource.SELF_REPORTED
    assert should_learn(FLEX, t_est=60, t_act=75, source=source)


@pytest.mark.parametrize("t_act", [301, 11])
def test_skips_outliers(t_act):
    assert not should_learn(FLEX, t_est=60, t_act=t_act, source=TIMER)


@pytest.mark.parametrize("t_act", [300, 12])
def test_learns_at_outlier_boundary(t_act):
    assert should_learn(FLEX, t_est=60, t_act=t_act, source=TIMER)


def test_custom_outlier_ratio():
    assert not should_learn(FLEX, t_est=60, t_act=200, source=TIMER, outlier_ratio=3)


@pytest.mark.parametrize(("t_est", "t_act"), [(0, 60), (60, 0)])
def test_skips_zero_durations(t_est, t_act):
    assert not should_learn(FLEX, t_est=t_est, t_act=t_act, source=TIMER)


# --- update_k_bias ---


def test_update_k_bias_ema():
    # 0.3 * (90 / 60) + 0.7 * 1.25 = 0.45 + 0.875 = 1.325
    assert update_k_bias(INITIAL_K_BIAS, t_est=60, t_act=90) == pytest.approx(1.325)


def test_update_k_bias_custom_alpha():
    # 0.5 * 2.0 + 0.5 * 1.0 = 1.5
    assert update_k_bias(1.0, t_est=60, t_act=120, alpha=0.5) == pytest.approx(1.5)


def test_update_k_bias_clamps_high():
    assert update_k_bias(3.0, t_est=10, t_act=100) == K_MAX


def test_update_k_bias_clamps_low():
    assert update_k_bias(0.5, t_est=100, t_act=10) == K_MIN


@pytest.mark.parametrize("alpha", [0, -0.1, 1.5])
def test_update_k_bias_rejects_bad_alpha(alpha):
    with pytest.raises(ValueError):
        update_k_bias(1.25, t_est=60, t_act=60, alpha=alpha)


@pytest.mark.parametrize(("t_est", "t_act"), [(0, 60), (60, 0)])
def test_update_k_bias_rejects_zero_durations(t_est, t_act):
    with pytest.raises(ValueError):
        update_k_bias(1.25, t_est=t_est, t_act=t_act)
