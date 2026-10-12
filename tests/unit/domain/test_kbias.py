import pytest

from metanoia_ai.domain.kbias import (
    INITIAL_K_BIAS,
    K_MAX,
    K_MIN,
    clamp_k_bias,
    proposed_minutes,
    skip_reason,
    update_k_bias,
)
from metanoia_ai.domain.models import SkipReason, TaskType

FLEX = TaskType.FLEXIBLE


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


# --- skip_reason ---


def test_clean_flexible_task_has_no_skip_reason():
    assert skip_reason(FLEX, t_est=60, t_act=90) is None


def test_fixed_task_is_skipped():
    assert skip_reason(TaskType.FIXED, t_est=60, t_act=60) == SkipReason.FIXED_TASK


def test_fixed_task_is_skipped_even_without_estimate():
    # Fixed tasks have no estimate in the DB; the type check comes first.
    assert skip_reason(TaskType.FIXED, t_est=0, t_act=None) == SkipReason.FIXED_TASK


@pytest.mark.parametrize("t_act", [None, 0])
def test_missing_actual_time_is_skipped(t_act):
    assert skip_reason(FLEX, t_est=60, t_act=t_act) == SkipReason.NO_ACTUAL_TIME


@pytest.mark.parametrize("t_act", [301, 11])
def test_outliers_are_skipped(t_act):
    assert skip_reason(FLEX, t_est=60, t_act=t_act) == SkipReason.OUTLIER


@pytest.mark.parametrize("t_act", [300, 12])
def test_outlier_boundary_is_learned(t_act):
    assert skip_reason(FLEX, t_est=60, t_act=t_act) is None


def test_custom_outlier_ratio():
    result = skip_reason(FLEX, t_est=60, t_act=200, outlier_ratio=3)
    assert result == SkipReason.OUTLIER


@pytest.mark.parametrize("t_est", [0, -10])
def test_bad_estimate_raises(t_est):
    with pytest.raises(ValueError):
        skip_reason(FLEX, t_est=t_est, t_act=60)


# --- update_k_bias ---


def test_update_k_bias_ema():
    # 0.2 * (90 / 60) + 0.8 * 1.3 = 0.3 + 1.04 = 1.34
    assert update_k_bias(INITIAL_K_BIAS, t_est=60, t_act=90) == pytest.approx(1.34)


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
