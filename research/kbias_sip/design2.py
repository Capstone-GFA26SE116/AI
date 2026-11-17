"""K-SiP v2 measures (plans/K-sip-v2.md): errors in %, per-developer pass/fail, N to days.

Pure functions over replay output. A run is {pair: predictions}, pairs keyed (developer, domain).
"""

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from research.kbias_sip.replay import Pair, Prediction

BLOCK = 10
TARGET_GAIN = 0.20  # C must be at least 20% better than A
TARGET_DEVS = 6  # ... for at least 6 of the 11 developers
N_STEPS = (0, 10, 20, 50, 100)
K0_CLOSE = 0.05  # K counts as recovered when within 5% of the default run

Run = Mapping[Pair, Sequence[Prediction]]


def task_error(pred: float, actual: float) -> float:
    """|predicted − actual| ÷ actual: 0.2 means 20% off the real time."""
    return abs(pred - actual) / actual


def block_errors(predictions: Sequence[Prediction], after: int = 0) -> list[float]:
    """Error of the total hours of each full block of 10 consecutive tasks.

    Only blocks that start after task `after` are kept; a last block shorter than 10 is dropped.
    """
    errors = []
    for start in range(0, len(predictions) - BLOCK + 1, BLOCK):
        if start < after:
            continue
        block = predictions[start : start + BLOCK]
        errors.append(
            task_error(sum(p.pred for p in block), sum(p.t_act for p in block))
        )
    return errors


def median_or_none(values: Iterable[float]) -> float | None:
    values = list(values)
    return statistics.median(values) if values else None


def overall(run: Run) -> dict[str, float]:
    """Median and mean of block errors (main) and of per-task errors (secondary)."""
    blocks = [e for preds in run.values() for e in block_errors(preds)]
    tasks = [task_error(p.pred, p.t_act) for preds in run.values() for p in preds]
    return {
        "block_median": statistics.median(blocks),
        "block_mean": statistics.mean(blocks),
        "task_median": statistics.median(tasks),
        "task_mean": statistics.mean(tasks),
        "within_25": sum(e <= 0.25 for e in tasks) / len(tasks),
        "blocks": len(blocks),
        "tasks": len(tasks),
    }


def per_developer(run: Run, after: int = 0) -> dict[int, float]:
    """Median block error per developer over all their pairs (blocks after task `after`)."""
    by_dev: dict[int, list[float]] = defaultdict(list)
    for (dev, _domain), preds in run.items():
        by_dev[dev].extend(block_errors(preds, after))
    return {dev: statistics.median(e) for dev, e in sorted(by_dev.items()) if e}


def gain(baseline: float, method: float) -> float:
    """How much lower the method's error is, as a share of the baseline's (0.2 = 20% better)."""
    return (baseline - method) / baseline if baseline else 0.0


@dataclass(frozen=True)
class DevResult:
    dev: int
    user_error: float
    metanoia_error: float

    @property
    def gain(self) -> float:
        return gain(self.user_error, self.metanoia_error)

    @property
    def passed(self) -> bool:
        return self.gain >= TARGET_GAIN


def compare_developers(user: Run, metanoia: Run, after: int = 0) -> list[DevResult]:
    """Per-developer comparison of A (user) and C (METANOIA) on blocks after task `after`."""
    a = per_developer(user, after)
    c = per_developer(metanoia, after)
    return [DevResult(dev, a[dev], c[dev]) for dev in a if dev in c]


@dataclass(frozen=True)
class CriterionRow:
    after: int
    passed: int
    eligible: int

    @property
    def met(self) -> bool:
        return self.passed >= TARGET_DEVS


def criterion(
    user: Run, metanoia: Run, steps: Sequence[int] = N_STEPS
) -> list[CriterionRow]:
    """For each N: how many developers have C at least 20% better than A after N tasks."""
    rows = []
    for after in steps:
        results = compare_developers(user, metanoia, after)
        rows.append(CriterionRow(after, sum(r.passed for r in results), len(results)))
    return rows


def days_to_reach(
    dates_by_pair: Mapping[Pair, Sequence[date]], n: int
) -> tuple[float | None, int]:
    """Median days from a pair's first task to its n-th task, and how many pairs reach n."""
    spans = [(d[n - 1] - d[0]).days for d in dates_by_pair.values() if len(d) >= n]
    return median_or_none(spans), len(spans)


POSITION_GROUPS = ((1, 10), (11, 20), (21, 50), (51, 100), (101, None))


def block_error_by_position(run: Run) -> dict[str, float | None]:
    """Median block error by where the block sits in the pair (tasks 1–10, 11–20, …)."""
    out = {}
    for low, high in POSITION_GROUPS:
        errors = []
        for preds in run.values():
            for i, e in enumerate(block_errors(preds)):
                first = i * BLOCK + 1
                if first >= low and (high is None or first <= high):
                    errors.append(e)
        label = f"{low}-{high}" if high else f">{low - 1}"
        out[label] = median_or_none(errors)
    return out


def k_gap_by_n(run: Run, reference: Run, max_n: int = 30) -> list[float | None]:
    """Median |K_n − K_n(ref)| ÷ K_n(ref) at task n = 1..max_n (how fast a wrong K0 is forgotten)."""
    gaps: list[float | None] = []
    for n in range(1, max_n + 1):
        values = [
            abs(preds[n - 1].k - reference[pair][n - 1].k) / reference[pair][n - 1].k
            for pair, preds in run.items()
            if len(preds) >= n
        ]
        gaps.append(median_or_none(values))
    return gaps


def first_close(gaps: Sequence[float | None], limit: float = K0_CLOSE) -> int | None:
    """First task number n whose gap is under `limit` (1-based), or None."""
    for n, g in enumerate(gaps, start=1):
        if g is not None and g < limit:
            return n
    return None
