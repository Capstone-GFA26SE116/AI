"""K-SiP v2 measures (plans/K-sip-v2.md)."""

from datetime import date

import pytest

from research.kbias_sip import design2 as d2
from research.kbias_sip.replay import Prediction


def preds(pairs: list[tuple[float, float]], k: float = 1.0) -> list[Prediction]:
    """Predictions from (predicted, actual) pairs."""
    return [
        Prediction(n, n, p, a, k, p, True) for n, (p, a) in enumerate(pairs, start=1)
    ]


def test_task_error_is_share_of_actual():
    assert d2.task_error(12, 10) == pytest.approx(0.2)
    assert d2.task_error(8, 10) == pytest.approx(0.2)


def test_block_error_uses_totals_and_drops_short_blocks():
    # 10 tasks: half 1h too long, half 1h too short → totals match.
    block = preds([(3, 2)] * 5 + [(1, 2)] * 5) + preds([(1, 1)] * 3)
    assert d2.block_errors(block) == [0.0]


def test_block_errors_after_skips_early_blocks():
    run = preds([(2, 1)] * 10 + [(1, 1)] * 10)
    assert d2.block_errors(run) == [1.0, 0.0]
    assert d2.block_errors(run, after=10) == [0.0]


def test_developer_passes_at_twenty_percent():
    user = {(1, "Dev"): preds([(10, 10 / 1.5)] * 10)}  # 50% off
    good = {(1, "Dev"): preds([(9, 10 / 1.5)] * 10)}  # 35% off → 30% better
    [r] = d2.compare_developers(user, good)
    assert r.passed and r.gain == pytest.approx(0.3)


def test_criterion_counts_developers_per_n():
    user = {(d, "Dev"): preds([(2, 1)] * 20) for d in range(6)}
    meta = {(d, "Dev"): preds([(2, 1)] * 10 + [(1, 1)] * 10) for d in range(6)}
    rows = d2.criterion(user, meta, steps=(0, 10))
    assert [(r.after, r.passed, r.eligible, r.met) for r in rows] == [
        (0, 6, 6, True),
        (10, 6, 6, True),
    ]


def test_days_to_reach():
    dates = {
        (1, "Dev"): [date(2020, 1, 1), date(2020, 1, 11)],
        (2, "Dev"): [date(2020, 1, 1)],
    }
    assert d2.days_to_reach(dates, 2) == (10, 1)


def test_k_gap_and_first_close():
    ref = {(1, "Dev"): preds([(1, 1)] * 3, k=1.0)}
    run = {
        (1, "Dev"): [
            Prediction(n, n, 1, 1, k, 1, True)
            for n, k in ((1, 1.5), (2, 1.2), (3, 1.01))
        ]
    }
    gaps = d2.k_gap_by_n(run, ref, max_n=3)
    assert gaps == pytest.approx([0.5, 0.2, 0.01])
    assert d2.first_close(gaps) == 3
