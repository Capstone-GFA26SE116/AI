import math
from datetime import date, timedelta

import pytest

from metanoia_ai.domain.kbias import INITIAL_K_BIAS, K_MAX, K_MIN
from research.kbias_sip.load import SipTask
from research.kbias_sip.methods import (
    Constant,
    GeometricMean,
    LastRatio,
    LogEma,
    Metanoia,
    Method,
    all_methods,
)
from research.kbias_sip.replay import group_pairs, replay


def make_tasks(*ratios: float, t_est: float = 2.0) -> list[SipTask]:
    return [
        SipTask(
            n,
            58,
            "Development",
            "Bug",
            t_est,
            t_est * r,
            date(2005, 1, 1) + timedelta(days=n),
        )
        for n, r in enumerate(ratios, start=1)
    ]


def ks(method: Method, *ratios: float) -> list[float]:
    return [p.k for p in replay(make_tasks(*ratios), method)]


# T4
def test_metanoia_follows_theory_when_ratio_is_constant() -> None:
    predictions = replay(make_tasks(*[1.5] * 20), Metanoia())

    for p in predictions:
        learned = p.n - 1  # k for task n comes from tasks 1..n-1
        assert p.k == pytest.approx(1.5 - 0.2 * 0.8**learned, abs=1e-9)
        assert p.pred == pytest.approx(p.t_est * p.k)
        if learned >= 5:
            assert abs(p.k - 1.5) < 0.075


# T5
def test_prediction_uses_k_from_before_the_task() -> None:
    predictions = replay(make_tasks(1.0, 1.0, 4.0, 1.0), Metanoia())

    k_after_two = 0.8**2 * INITIAL_K_BIAS + (1 - 0.8**2) * 1.0
    assert predictions[2].k == pytest.approx(k_after_two)
    assert predictions[2].pred == pytest.approx(2.0 * k_after_two)
    assert predictions[3].k == pytest.approx(0.2 * 4.0 + 0.8 * k_after_two)


# T6
def test_outlier_is_predicted_but_not_learned() -> None:
    predictions = replay(make_tasks(1.0, 10.0, 1.0), Metanoia())

    outlier = predictions[1]
    assert outlier.pred == pytest.approx(2.0 * outlier.k)
    assert outlier.used_for_learning is False
    assert predictions[2].k == outlier.k
    assert predictions[0].used_for_learning is True


# T7
def test_k_never_goes_above_the_clamp() -> None:
    k_values = ks(Metanoia(), *[4.0] * 30)

    assert max(k_values) == K_MAX
    assert all(k <= K_MAX for k in k_values)


# T8
def test_constants_never_learn() -> None:
    assert ks(Constant("B0", 1.0), 2.0, 0.5) == [1.0, 1.0]
    assert ks(Constant("B1", 1.3), 2.0, 0.5) == [1.3, 1.3]


def test_geometric_mean_by_hand() -> None:
    assert ks(GeometricMean(), 2.0, 0.5, 1.5, 1.0) == pytest.approx(
        [1.3, 2.0, 1.0, 1.5 ** (1 / 3)]
    )


def test_last_ratio_by_hand() -> None:
    assert ks(LastRatio(), 2.0, 0.5, 1.5) == pytest.approx([1.3, 2.0, 0.5])


def test_baselines_are_clamped() -> None:
    assert ks(LastRatio(), 4.0, 0.25, 1.0) == pytest.approx([1.3, K_MAX, K_MIN])
    assert ks(GeometricMean(), 4.0, 4.0) == pytest.approx([1.3, K_MAX])


def test_metanoia_by_hand() -> None:
    assert ks(Metanoia(), 2.0, 0.5) == pytest.approx([1.3, 1.44])


def test_log_ema_by_hand() -> None:
    first = 2.0**0.2 * 1.3**0.8
    second = 0.5**0.2 * first**0.8
    assert ks(LogEma(), 2.0, 0.5, 1.0) == pytest.approx([1.3, first, second])


def test_log_ema_is_clamped() -> None:
    assert ks(LogEma(), *[4.0] * 40)[-1] == pytest.approx(K_MAX)
    assert ks(LogEma(), *[0.25] * 40)[-1] == pytest.approx(K_MIN)


def test_sensitivity_parameters() -> None:
    assert ks(Metanoia(alpha=0.1, k_bias=1.0), 2.0, 1.0) == pytest.approx([1.0, 1.1])
    assert ks(LogEma(alpha=0.3, log_k=0.0), 2.0, 1.0)[1] == pytest.approx(2.0**0.3)


def test_all_methods_codes() -> None:
    assert [m.code for m in all_methods()] == ["B0", "B1", "B2", "B3", "M", "X1"]


def test_group_pairs_keeps_time_order() -> None:
    tasks = make_tasks(1.0, 1.0, 1.0)
    other = SipTask(9, 42, "Management", "Support", 1.0, 1.0, date(2005, 1, 2))

    groups = group_pairs([tasks[0], other, tasks[1], tasks[2]], "category")

    assert list(groups) == [(42, "Management"), (58, "Development")]
    assert [t.task_number for t in groups[(58, "Development")]] == [1, 2, 3]
    assert math.isclose(groups[(42, "Management")][0].t_est, 1.0)
