"""Read the SiP CSV files, keep clean single-developer tasks and report the funnel (plan §1.3).

Usage: uv run python -m research.kbias_sip.load --data ~/data/SiP_dataset
"""

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

TASK_FILE = "Sip-task-info.csv"
DATES_FILE = "est-act-dates.csv"
ENCODING = "cp1252"  # SiP files are not UTF-8
DATE_FORMAT = "%d-%b-%y"  # e.g. 02-Nov-04
DONE_STATUSES = frozenset({"FINISHED", "COMPLETED", "RELEASED"})
MIN_TASKS_PER_PAIR = 10

# Category = main run, SubCategory = comparison run.
DOMAIN_MODES = ("category", "sub_category")


@dataclass(frozen=True)
class SipTask:
    """One finished task done by one developer. Hours, not minutes (only the ratio matters)."""

    task_number: int
    developer_id: int
    category: str
    sub_category: str
    t_est: float
    t_act: float
    estimate_on: date


@dataclass(frozen=True)
class Funnel:
    """How many rows/tasks are left after each filter step."""

    rows: int
    multi_dev_tasks: int
    single_dev_tasks: int
    done_tasks: int
    positive_estimate_tasks: int


# Expected numbers on the real dataset (plan §1.3, AC1).
EXPECTED_FUNNEL = Funnel(
    rows=12299,
    multi_dev_tasks=1384,
    single_dev_tasks=8882,
    done_tasks=7176,
    positive_estimate_tasks=7176,
)
EXPECTED_CATEGORY_PAIRS = (24, 7133)  # pairs with >= 10 tasks, tasks in them


def read_rows(data_dir: Path) -> pd.DataFrame:
    """Read both CSV files and join the estimate date onto each task row."""
    tasks = pd.read_csv(data_dir / TASK_FILE, encoding=ENCODING)
    dates = pd.read_csv(data_dir / DATES_FILE, encoding=ENCODING)
    # The dates file repeats a task once per developer row; the dates are the same.
    dates = dates.drop_duplicates("TaskNumber")[["TaskNumber", "EstimateOn"]]
    return tasks.merge(dates, on="TaskNumber", how="left", validate="many_to_one")


def filter_tasks(rows: pd.DataFrame) -> tuple[list[SipTask], Funnel]:
    """Keep single-developer, finished tasks with a positive estimate, oldest first."""
    devs_per_task = rows.groupby("TaskNumber")["DeveloperID"].nunique()
    single = rows[rows["TaskNumber"].isin(devs_per_task[devs_per_task == 1].index)]
    single = single.drop_duplicates("TaskNumber")
    done = single[single["StatusCode"].isin(DONE_STATUSES)]
    positive = done[done["HoursEstimate"] > 0]

    tasks = [
        SipTask(
            task_number=int(r.TaskNumber),
            developer_id=int(r.DeveloperID),
            category=str(r.Category),
            sub_category=str(r.SubCategory),
            t_est=float(r.HoursEstimate),
            t_act=float(r.HoursActual),
            estimate_on=pd.to_datetime(r.EstimateOn, format=DATE_FORMAT).date(),
        )
        for r in positive.itertuples(index=False)
    ]
    tasks.sort(key=lambda t: (t.estimate_on, t.task_number))
    funnel = Funnel(
        rows=len(rows),
        multi_dev_tasks=int((devs_per_task > 1).sum()),
        single_dev_tasks=len(single),
        done_tasks=len(done),
        positive_estimate_tasks=len(positive),
    )
    return tasks, funnel


def load_tasks(data_dir: Path) -> tuple[list[SipTask], Funnel]:
    """Read and filter the SiP dataset in one call."""
    return filter_tasks(read_rows(data_dir))


def domain_of(task: SipTask, domain_mode: str) -> str:
    """Return the task's domain: `category` (main run) or `sub_category`."""
    if domain_mode not in DOMAIN_MODES:
        raise ValueError(f"unknown domain mode: {domain_mode}")
    return getattr(task, domain_mode)


def count_pairs(
    tasks: list[SipTask], domain_mode: str, min_tasks: int = MIN_TASKS_PER_PAIR
) -> tuple[int, int, int]:
    """Return (all pairs, pairs with >= min_tasks, tasks in those pairs)."""
    sizes = Counter((t.developer_id, domain_of(t, domain_mode)) for t in tasks)
    big = [n for n in sizes.values() if n >= min_tasks]
    return len(sizes), len(big), sum(big)


def format_funnel(funnel: Funnel, tasks: list[SipTask]) -> str:
    """Return the funnel as printable lines."""
    lines = [
        f"All rows:                          {funnel.rows}",
        f"Tasks with > 1 developer (dropped): {funnel.multi_dev_tasks}",
        f"Single-developer tasks:            {funnel.single_dev_tasks}",
        f"Status in {sorted(DONE_STATUSES)}: {funnel.done_tasks}",
        f"Estimate > 0:                      {funnel.positive_estimate_tasks}",
    ]
    for mode in DOMAIN_MODES:
        pairs, big, in_big = count_pairs(tasks, mode)
        lines.append(
            f"Pairs (developer, {mode}): {pairs}, of which {big} have "
            f">= {MIN_TASKS_PER_PAIR} tasks ({in_big} tasks)"
        )
    return "\n".join(lines)


def check_funnel(funnel: Funnel, tasks: list[SipTask]) -> None:
    """Stop when the real dataset does not match the plan (AC1)."""
    pairs = count_pairs(tasks, "category")[1:]
    if funnel != EXPECTED_FUNNEL or pairs != EXPECTED_CATEGORY_PAIRS:
        raise SystemExit(
            f"Funnel does not match plan §1.3: got {funnel}, category pairs {pairs}; "
            f"expected {EXPECTED_FUNNEL}, {EXPECTED_CATEGORY_PAIRS}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the SiP data funnel.")
    parser.add_argument("--data", type=Path, required=True, help="SiP dataset folder")
    args = parser.parse_args()
    tasks, funnel = load_tasks(args.data.expanduser())
    print(format_funnel(funnel, tasks))
    check_funnel(funnel, tasks)
    print("Funnel matches plan §1.3.")


if __name__ == "__main__":
    main()
