"""Accuracy and convergence metrics of plan §2.4.

C1 is reported as the median distance |ln K - ln G| per n group, without a single
"converged at n" number: K keeps moving around G because of noisy ratios (plan §5).

Error of one prediction: e = ln(pred / t_act). Positive = over-estimate, negative = under.
Only pairs with >= MIN_TASKS_PER_PAIR tasks (all tasks, outliers included) are scored.
"""

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from statistics import mean, median

from research.kbias_sip.load import MIN_TASKS_PER_PAIR
from research.kbias_sip.replay import Pair, Prediction

BLOCK_SIZE = 10
PRED_LEVEL = 0.25

NGroup = tuple[str, int, float]  # (label, first n, last n)
N_GROUPS: tuple[NGroup, ...] = (
    ("1", 1, 1),
    ("2", 2, 2),
    ("3", 3, 3),
    ("4-5", 4, 5),
    ("6-10", 6, 10),
    ("11-20", 11, 20),
    ("21-50", 21, 50),
    ("51-100", 51, 100),
    (">100", 101, math.inf),
)
# P1 uses blocks of 10 tasks, so its groups follow block borders (plan decision 2026-10-29).
BLOCK_GROUPS: tuple[NGroup, ...] = (
    ("1-10", 1, 10),
    ("11-20", 11, 20),
    ("21-50", 21, 50),
    ("51-100", 51, 100),
    (">100", 101, math.inf),
)
ALL = "all"
ALL_BY_DEVELOPER = "all_dev_median"  # median of per-developer values


@dataclass(frozen=True)
class SummaryRow:
    """One number of the summary: metric x n group, and how many items it is built on."""

    metric: str
    n_group: str
    value: float
    count: int


def log_error(p: Prediction) -> float:
    return math.log(p.pred / p.t_act)


def within_pred_level(p: Prediction, level: float = PRED_LEVEL) -> bool:
    return abs(p.pred / p.t_act - 1) <= level


def block_errors(
    predictions: Sequence[Prediction], size: int = BLOCK_SIZE
) -> list[tuple[int, float]]:
    """Return (first n of block, |ln(sum pred / sum t_act)|) per full block; a short last block is dropped."""
    blocks = []
    for start in range(0, len(predictions) - size + 1, size):
        block = predictions[start : start + size]
        total_pred = sum(p.pred for p in block)
        total_act = sum(p.t_act for p in block)
        blocks.append((block[0].n, abs(math.log(total_pred / total_act))))
    return blocks


def group_label(n: int, groups: Sequence[NGroup]) -> str:
    for label, first, last in groups:
        if first <= n <= last:
            return label
    raise ValueError(f"n must be >= 1, got {n}")


def scored_pairs(
    runs: Mapping[Pair, Sequence[Prediction]], min_tasks: int = MIN_TASKS_PER_PAIR
) -> dict[Pair, Sequence[Prediction]]:
    """Keep pairs with at least min_tasks tasks."""
    return {pair: preds for pair, preds in runs.items() if len(preds) >= min_tasks}


def target_ratio(predictions: Sequence[Prediction]) -> float | None:
    """G: geometric mean of every learned ratio of the pair (looks ahead; describe only)."""
    logs = [math.log(p.t_act / p.t_est) for p in predictions if p.used_for_learning]
    return math.exp(mean(logs)) if logs else None


def distance_by_n(
    runs: Mapping[Pair, Sequence[Prediction]],
) -> dict[int, list[float]]:
    """d_n = |ln k_n - ln G| for every pair, keyed by task position n."""
    distances: dict[int, list[float]] = defaultdict(list)
    for preds in runs.values():
        g = target_ratio(preds)
        if g is None:
            continue
        for p in preds:
            distances[p.n].append(abs(math.log(p.k) - math.log(g)))
    return dict(sorted(distances.items()))


def theory_gap(n: int, alpha: float = 0.2, start_gap: float = 0.2) -> float:
    """C1-theory: gap left after n tasks when the ratio never changes: start_gap x (1 - alpha)^n."""
    return start_gap * (1 - alpha) ** n


def _task_metrics(preds: Sequence[Prediction]) -> dict[str, float]:
    errors = [log_error(p) for p in preds]
    return {
        "S1_median_abs": median(abs(e) for e in errors),
        "S1_mean_abs": mean(abs(e) for e in errors),
        "S2_mean": mean(errors),
        "S3_pred25": mean(1.0 if within_pred_level(p) else 0.0 for p in preds),
    }


def _rows_by_group(
    items: Sequence[tuple[int, Prediction]],
    groups: Sequence[NGroup],
    compute: Callable[[Sequence[Prediction]], dict[str, float]],
) -> list[SummaryRow]:
    grouped: dict[str, list[Prediction]] = defaultdict(list)
    for n, p in items:
        grouped[group_label(n, groups)].append(p)
    rows = []
    for label, _, _ in groups:
        if grouped[label]:
            for metric, value in compute(grouped[label]).items():
                rows.append(SummaryRow(metric, label, value, len(grouped[label])))
    return rows


def summarize(runs: Mapping[Pair, Sequence[Prediction]]) -> list[SummaryRow]:
    """Every metric of plan §2.4 for one method, overall and per n group."""
    runs = scored_pairs(runs)
    if not runs:
        return []
    preds = [p for pair_preds in runs.values() for p in pair_preds]
    blocks = [b for pair_preds in runs.values() for b in block_errors(pair_preds)]
    rows = [
        SummaryRow("P1_block_median", ALL, median(v for _, v in blocks), len(blocks))
    ]
    for label, first, last in BLOCK_GROUPS:
        values = [v for n, v in blocks if first <= n <= last]
        if values:
            rows.append(
                SummaryRow("P1_block_median", label, median(values), len(values))
            )

    rows += [SummaryRow(m, ALL, v, len(preds)) for m, v in _task_metrics(preds).items()]
    rows += _rows_by_group([(p.n, p) for p in preds], N_GROUPS, _task_metrics)
    rows += _developer_rows(runs)

    distances = distance_by_n(runs)
    all_d = [d for ds in distances.values() for d in ds]
    rows.append(SummaryRow("C1_median_distance", ALL, median(all_d), len(all_d)))
    for label, first, last in N_GROUPS:
        values = [d for n, ds in distances.items() if first <= n <= last for d in ds]
        if values:
            rows.append(
                SummaryRow("C1_median_distance", label, median(values), len(values))
            )
    return rows


def _developer_rows(runs: Mapping[Pair, Sequence[Prediction]]) -> list[SummaryRow]:
    """Median over developers of each developer's own value, so one big developer cannot dominate."""
    by_dev: dict[int, list[Sequence[Prediction]]] = defaultdict(list)
    for (dev, _), preds in runs.items():
        by_dev[dev].append(preds)
    per_dev: dict[str, list[float]] = defaultdict(list)
    for pair_runs in by_dev.values():
        blocks = [v for preds in pair_runs for _, v in block_errors(preds)]
        if blocks:
            per_dev["P1_block_median"].append(median(blocks))
        flat = [p for preds in pair_runs for p in preds]
        for metric, value in _task_metrics(flat).items():
            per_dev[metric].append(value)
    return [
        SummaryRow(metric, ALL_BY_DEVELOPER, median(values), len(values))
        for metric, values in per_dev.items()
    ]
