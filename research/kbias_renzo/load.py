"""Read and filter the Renzo Pomodoro dataset (plans/K-renzo.md §1).

Tasks come back as `SipTask` so the SiP replay and measures can be reused as they are:
one developer (1), one main domain ("all"), and the first @tag (client / project) as the
side-run domain. Times are pomodoros × 25 minutes.
"""

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from research.kbias_sip.load import SipTask

TASK_FILE = "all-tasks.csv"
POMODORO_MINUTES = 25
MAIN_DOMAIN = "all"
FIXED_TAG = "@meeting"  # meetings are Fixed tasks: never learned from


@dataclass(frozen=True)
class Funnel:
    rows: int
    with_numbers: int
    done: int
    positive: int
    not_meeting: int


def _number(value: str) -> float | None:
    return None if value in ("", "NA") else float(value)


def filter_rows(rows: list[dict[str, str]]) -> tuple[list[SipTask], Funnel]:
    """Keep finished, non-meeting tasks with positive estimate and actual, in file order."""
    with_numbers = [
        r
        for r in rows
        if _number(r["estimate"]) is not None and _number(r["actual"]) is not None
    ]
    done = [r for r in with_numbers if r["DONE"] == "1"]
    positive = [r for r in done if float(r["estimate"]) > 0 and float(r["actual"]) > 0]
    kept = [r for r in positive if FIXED_TAG not in r["X.words"].split(",")]
    tasks = [
        SipTask(
            task_number=i,
            developer_id=1,
            category=MAIN_DOMAIN,
            sub_category=r["X.words"].split(",")[0],
            t_est=float(r["estimate"]) * POMODORO_MINUTES,
            t_act=float(r["actual"]) * POMODORO_MINUTES,
            estimate_on=date.fromisoformat(r["date"]),
        )
        for i, r in enumerate(kept, start=1)
    ]
    tasks.sort(key=lambda t: (t.estimate_on, t.task_number))
    funnel = Funnel(len(rows), len(with_numbers), len(done), len(positive), len(kept))
    return tasks, funnel


def load_tasks(data_dir: Path) -> tuple[list[SipTask], Funnel]:
    with (data_dir / TASK_FILE).open(encoding="utf-8", newline="") as f:
        return filter_rows(list(csv.DictReader(f)))
