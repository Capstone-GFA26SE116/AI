import math

import pytest

from research.kbias_sip.metrics import (
    ALL,
    ALL_BY_DEVELOPER,
    N_GROUPS,
    block_errors,
    distance_by_n,
    group_label,
    log_error,
    summarize,
    target_ratio,
    theory_gap,
    within_pred_level,
)
from research.kbias_sip.replay import Prediction


def pred(n: int, t_act: float, k: float, used: bool = True) -> Prediction:
    return Prediction(n, n, 1.0, t_act, k, 1.0 * k, used)


def run(*pairs: tuple[float, float]) -> list[Prediction]:
    """Predictions from (t_act, k) pairs, t_est = 1."""
    return [pred(n, act, k) for n, (act, k) in enumerate(pairs, start=1)]


def value(rows, metric: str, group: str = ALL) -> float:
    return next(r.value for r in rows if r.metric == metric and r.n_group == group)


# T9
def test_p1_uses_full_blocks_only() -> None:
    preds = run(*[(1.0, 2.0)] * 10, *[(1.0, 1.0)] * 10, *[(1.0, 3.0)] * 3)

    assert block_errors(preds) == [(1, pytest.approx(math.log(2))), (11, 0.0)]

    rows = summarize({(58, "Development"): preds})
    assert value(rows, "P1_block_median") == pytest.approx(math.log(2) / 2)
    assert value(rows, "P1_block_median", "1-10") == pytest.approx(math.log(2))
    assert value(rows, "P1_block_median", "11-20") == 0.0


def test_p1_sums_hours_before_the_log() -> None:
    # Over by 1h then under by 1h: each task is wrong, the block total is right.
    preds = run(*[(1.0, 2.0), (3.0, 2.0)] * 5)

    assert block_errors(preds) == [(1, pytest.approx(0.0))]


def test_task_errors_by_hand() -> None:
    p = pred(1, t_act=2.0, k=2.5)

    assert log_error(p) == pytest.approx(math.log(1.25))
    assert within_pred_level(p) is True
    assert within_pred_level(pred(1, t_act=2.0, k=2.6)) is False


def test_s_metrics_by_hand() -> None:
    preds = run(*[(1.0, 2.0)] * 5, *[(2.0, 1.0)] * 5)

    rows = summarize({(1, "D"): preds})

    assert value(rows, "S1_median_abs") == pytest.approx(math.log(2))
    assert value(rows, "S1_mean_abs") == pytest.approx(math.log(2))
    assert value(rows, "S2_mean") == pytest.approx(0.0)
    assert value(rows, "S3_pred25") == 0.0
    assert value(rows, "S2_mean", "6-10") == pytest.approx(-math.log(2))


# T10
def test_c1_distance_by_n() -> None:
    # Ratios 2 and 0.5 alternate, so G = 1 and d_n = |ln k_n|.
    ks = [1.5, 1.2, 1.05, 1.0, 1.02, 1.0, 1.0, 1.0, 1.0, 1.0]
    preds = [pred(n, 2.0 if n % 2 else 0.5, k) for n, k in enumerate(ks, start=1)]

    assert target_ratio(preds) == pytest.approx(1.0)
    distances = distance_by_n({(1, "D"): preds})
    assert distances[1] == [pytest.approx(math.log(1.5))]
    assert distances[3] == [pytest.approx(math.log(1.05))]

    rows = summarize({(1, "D"): preds})
    assert value(rows, "C1_median_distance", "1") == pytest.approx(math.log(1.5))
    assert value(rows, "C1_median_distance", "4-5") == pytest.approx(math.log(1.02) / 2)
    assert value(rows, "C1_median_distance", "6-10") == pytest.approx(0.0)


def test_target_ignores_tasks_not_learned() -> None:
    preds = [pred(1, 2.0, 1.0), pred(2, 10.0, 1.0, used=False)]

    assert target_ratio(preds) == pytest.approx(2.0)
    assert target_ratio([pred(1, 10.0, 1.0, used=False)]) is None


def test_theory_gap() -> None:
    assert theory_gap(0) == pytest.approx(0.2)
    assert theory_gap(5) == pytest.approx(0.2 * 0.8**5)
    assert theory_gap(5) < 0.075


# T11
def test_small_pairs_are_not_scored() -> None:
    small = {(1, "D"): run((1.0, 1.0)), (2, "D"): run(*[(1.0, 1.0)] * 9)}

    assert summarize(small) == []

    rows = summarize({**small, (3, "D"): run(*[(1.0, 2.0)] * 10)})
    assert next(r for r in rows if r.metric == "S1_median_abs").count == 10


def test_developer_median_does_not_let_one_developer_dominate() -> None:
    runs = {
        (1, "D"): run(*[(1.0, 1.0)] * 100),
        (2, "D"): run(*[(1.0, 2.0)] * 10),
        (3, "D"): run(*[(1.0, 2.0)] * 10),
    }

    rows = summarize(runs)

    assert value(rows, "S1_median_abs") == 0.0
    assert value(rows, "S1_median_abs", ALL_BY_DEVELOPER) == pytest.approx(math.log(2))
    assert value(rows, "P1_block_median", ALL_BY_DEVELOPER) == pytest.approx(
        math.log(2)
    )


def test_group_labels() -> None:
    assert [group_label(n, N_GROUPS) for n in (1, 4, 10, 11, 101, 5000)] == [
        "1",
        "4-5",
        "6-10",
        "11-20",
        ">100",
        ">100",
    ]
    with pytest.raises(ValueError):
        group_label(0, N_GROUPS)
