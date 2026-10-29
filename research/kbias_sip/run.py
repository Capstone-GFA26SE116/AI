"""Run the K-SiP replay and write the result CSVs (plan §2.1, §2.5, §2.6).

Usage:
uv run python -m research.kbias_sip.run --data ~/data/SiP_dataset --out research/kbias_sip/results
"""

import argparse
import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from research.kbias_sip.load import (
    DOMAIN_MODES,
    SipTask,
    check_funnel,
    count_pairs,
    format_funnel,
    load_tasks,
)
from research.kbias_sip.methods import Metanoia, Method, all_methods
from research.kbias_sip.metrics import ALL, SummaryRow, summarize
from research.kbias_sip.plots import plot_all
from research.kbias_sip.replay import Pair, Prediction, group_pairs, replay

MAIN_DOMAIN_MODE = "category"
PREDICTION_COLUMNS = [
    "domain_mode",
    "method",
    "dev",
    "domain",
    "n",
    "t_est",
    "t_act",
    "k",
    "pred",
    "used_for_learning",
]
SUMMARY_COLUMNS = ["domain_mode", "method", "metric", "n_group", "value", "count"]
SENSITIVITY_COLUMNS = ["experiment", "setting", "method", "metric", "value", "count"]

# Sensitivity grid for M (plan §2.5).
ALPHAS = (0.1, 0.2, 0.3)
INITIAL_KS = (1.0, 1.3)
SENSITIVITY_METRICS = ("P1_block_median", "S1_median_abs")

Results = dict[str, dict[Pair, list[Prediction]]]  # method code -> pair -> predictions


@dataclass(frozen=True)
class SensitivityRow:
    experiment: str  # alpha_k0 / domain / ratio_one
    setting: str
    method: str
    metric: str
    value: float
    count: int


def replay_all(
    tasks: Sequence[SipTask], domain_mode: str, methods: Sequence[Method]
) -> Results:
    """Replay every pair with every method: {method code: {pair: predictions}}."""
    pairs = group_pairs(tasks, domain_mode)
    return {
        m.code: {pair: replay(pair_tasks, m) for pair, pair_tasks in pairs.items()}
        for m in methods
    }


def summarize_all(results: Results) -> dict[str, list[SummaryRow]]:
    return {method: summarize(runs) for method, runs in results.items()}


def drop_exact_ratio_one(tasks: Sequence[SipTask]) -> list[SipTask]:
    """Remove tasks whose actual hours equal the estimate (likely copied, plan #8)."""
    return [t for t in tasks if t.t_act != t.t_est]


def _sensitivity_rows(
    experiment: str, setting: str, summaries: Mapping[str, list[SummaryRow]]
) -> list[SensitivityRow]:
    return [
        SensitivityRow(experiment, setting, method, r.metric, r.value, r.count)
        for method, rows in summaries.items()
        for r in rows
        if r.n_group == ALL and r.metric in SENSITIVITY_METRICS
    ]


def sensitivity(
    tasks: Sequence[SipTask],
    summaries_by_mode: Mapping[str, dict[str, list[SummaryRow]]],
) -> list[SensitivityRow]:
    """alpha x K0 grid for M, Category vs SubCategory, and without ratio == 1 (plan §2.5)."""
    rows = []
    for alpha in ALPHAS:
        for k0 in INITIAL_KS:
            method = Metanoia(alpha=alpha, k_bias=k0)
            results = replay_all(tasks, MAIN_DOMAIN_MODE, [method])
            rows += _sensitivity_rows(
                "alpha_k0", f"alpha={alpha} k0={k0}", summarize_all(results)
            )
    for mode, summaries in summaries_by_mode.items():
        rows += _sensitivity_rows("domain", mode, summaries)
    rows += _sensitivity_rows("ratio_one", "kept", summaries_by_mode[MAIN_DOMAIN_MODE])
    dropped = replay_all(drop_exact_ratio_one(tasks), MAIN_DOMAIN_MODE, all_methods())
    rows += _sensitivity_rows("ratio_one", "dropped", summarize_all(dropped))
    return rows


def write_predictions(path: Path, results_by_mode: Mapping[str, Results]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(PREDICTION_COLUMNS)
        for domain_mode, results in results_by_mode.items():
            for method, runs in results.items():
                for (dev, domain), preds in runs.items():
                    for p in preds:
                        writer.writerow(
                            [
                                domain_mode,
                                method,
                                dev,
                                domain,
                                p.n,
                                p.t_est,
                                p.t_act,
                                p.k,
                                p.pred,
                                p.used_for_learning,
                            ]
                        )


def write_summary(
    path: Path, summaries_by_mode: Mapping[str, dict[str, list[SummaryRow]]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(SUMMARY_COLUMNS)
        for domain_mode, summaries in summaries_by_mode.items():
            for method, rows in summaries.items():
                for r in rows:
                    writer.writerow(
                        [domain_mode, method, r.metric, r.n_group, r.value, r.count]
                    )


def write_sensitivity(path: Path, rows: Sequence[SensitivityRow]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(SENSITIVITY_COLUMNS)
        for r in rows:
            writer.writerow(
                [r.experiment, r.setting, r.method, r.metric, r.value, r.count]
            )


def format_overview(summaries: dict[str, list[SummaryRow]]) -> str:
    """One line per method with the overall value of each metric."""
    metrics = [r.metric for r in next(iter(summaries.values())) if r.n_group == ALL]
    lines = ["method " + " ".join(f"{m:>18}" for m in metrics)]
    for method, rows in summaries.items():
        overall = {r.metric: r.value for r in rows if r.n_group == ALL}
        lines.append(
            f"{method:<6} " + " ".join(f"{overall[m]:>18.3f}" for m in metrics)
        )
    return "\n".join(lines)


def format_sensitivity(rows: Sequence[SensitivityRow]) -> str:
    """One line per (experiment, setting, method): P1 and S1."""
    values: dict[tuple[str, str, str], dict[str, float]] = {}
    for r in rows:
        values.setdefault((r.experiment, r.setting, r.method), {})[r.metric] = r.value
    lines = [f"{'experiment':<10} {'setting':<18} {'method':<6} {'P1':>7} {'S1':>7}"]
    for (experiment, setting, method), v in values.items():
        lines.append(
            f"{experiment:<10} {setting:<18} {method:<6} "
            f"{v['P1_block_median']:>7.3f} {v['S1_median_abs']:>7.3f}"
        )
    return "\n".join(lines)


def run(
    data_dir: Path, out_dir: Path, check: bool = True
) -> tuple[dict[str, list[SummaryRow]], list[SensitivityRow]]:
    """Load, replay every domain mode with every method, run the sensitivity checks, write CSVs.

    Returns the main (Category) summaries and the sensitivity rows.
    """
    tasks, funnel = load_tasks(data_dir)
    print(format_funnel(funnel, tasks))
    if check:
        check_funnel(funnel, tasks)
    pairs, big, in_big = count_pairs(drop_exact_ratio_one(tasks), MAIN_DOMAIN_MODE)
    print(
        f"Without ratio == 1: {pairs} category pairs, {big} with >= 10 tasks ({in_big} tasks)"
    )

    results_by_mode = {
        mode: replay_all(tasks, mode, all_methods()) for mode in DOMAIN_MODES
    }
    summaries_by_mode = {
        mode: summarize_all(results) for mode, results in results_by_mode.items()
    }
    sensitivity_rows = sensitivity(tasks, summaries_by_mode)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_predictions(out_dir / "predictions.csv", results_by_mode)
    write_summary(out_dir / "summary.csv", summaries_by_mode)
    write_sensitivity(out_dir / "sensitivity.csv", sensitivity_rows)
    return summaries_by_mode[MAIN_DOMAIN_MODE], sensitivity_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay K_bias learning on SiP.")
    parser.add_argument("--data", type=Path, required=True, help="SiP dataset folder")
    parser.add_argument("--out", type=Path, required=True, help="results folder")
    args = parser.parse_args()
    summaries, sensitivity_rows = run(args.data.expanduser(), args.out.expanduser())
    print(format_overview(summaries))
    print(format_sensitivity(sensitivity_rows))
    plot_all(args.out.expanduser())


if __name__ == "__main__":
    main()
