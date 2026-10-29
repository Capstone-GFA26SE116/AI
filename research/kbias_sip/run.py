"""Run the K-SiP replay and write the result CSVs (plan §2.1, §2.6).

Usage:
uv run python -m research.kbias_sip.run --data ~/data/SiP_dataset --out research/kbias_sip/results
"""

import argparse
import csv
from collections.abc import Sequence
from pathlib import Path

from research.kbias_sip.load import (
    SipTask,
    check_funnel,
    format_funnel,
    load_tasks,
)
from research.kbias_sip.methods import Method, all_methods
from research.kbias_sip.metrics import ALL, SummaryRow, summarize
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


def replay_all(
    tasks: Sequence[SipTask], domain_mode: str, methods: Sequence[Method]
) -> dict[str, dict[Pair, list[Prediction]]]:
    """Replay every pair with every method: {method code: {pair: predictions}}."""
    pairs = group_pairs(tasks, domain_mode)
    return {
        m.code: {pair: replay(pair_tasks, m) for pair, pair_tasks in pairs.items()}
        for m in methods
    }


def write_predictions(
    path: Path, domain_mode: str, results: dict[str, dict[Pair, list[Prediction]]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(PREDICTION_COLUMNS)
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
    path: Path, domain_mode: str, summaries: dict[str, list[SummaryRow]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(SUMMARY_COLUMNS)
        for method, rows in summaries.items():
            for r in rows:
                writer.writerow(
                    [domain_mode, method, r.metric, r.n_group, r.value, r.count]
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


def run(
    data_dir: Path, out_dir: Path, check: bool = True
) -> dict[str, list[SummaryRow]]:
    """Load, replay the main run (Category) with every method, write the CSVs."""
    tasks, funnel = load_tasks(data_dir)
    print(format_funnel(funnel, tasks))
    if check:
        check_funnel(funnel, tasks)

    results = replay_all(tasks, MAIN_DOMAIN_MODE, all_methods())
    summaries = {method: summarize(runs) for method, runs in results.items()}

    out_dir.mkdir(parents=True, exist_ok=True)
    write_predictions(out_dir / "predictions.csv", MAIN_DOMAIN_MODE, results)
    write_summary(out_dir / "summary.csv", MAIN_DOMAIN_MODE, summaries)
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay K_bias learning on SiP.")
    parser.add_argument("--data", type=Path, required=True, help="SiP dataset folder")
    parser.add_argument("--out", type=Path, required=True, help="results folder")
    args = parser.parse_args()
    summaries = run(args.data.expanduser(), args.out.expanduser())
    print(format_overview(summaries))


if __name__ == "__main__":
    main()
