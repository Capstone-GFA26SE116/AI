import csv
from datetime import date
from pathlib import Path

from research.kbias_sip.load import SipTask
from research.kbias_sip.run import (
    drop_exact_ratio_one,
    format_overview,
    format_sensitivity,
    run,
)
from tests.unit.research.test_load import task_row, write_dataset


def small_dataset(folder: Path) -> Path:
    rows, dates = [], []
    for n in range(1, 25):
        dev = 58 if n % 2 else 42
        rows.append(task_row(n, dev, est=2, act=1 + (n % 5)))
        dates.append(f"{n},{n % 28 + 1:02d}-Nov-04,,\n")
    rows.append(task_row(99, 58, est=1, act=20))  # outlier, still predicted
    dates.append("99,05-Dec-04,,\n")
    return write_dataset(folder, rows, dates)


# T12
def test_two_runs_give_identical_files(tmp_path: Path) -> None:
    data = small_dataset(tmp_path)

    run(data, tmp_path / "a", check=False)
    run(data, tmp_path / "b", check=False)

    for name in ("summary.csv", "predictions.csv", "sensitivity.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (
            tmp_path / "b" / name
        ).read_bytes()


def test_writes_every_method_and_prediction(tmp_path: Path) -> None:
    summaries, _ = run(small_dataset(tmp_path), tmp_path / "out", check=False)

    with (tmp_path / "out" / "predictions.csv").open() as f:
        predictions = list(csv.DictReader(f))
    with (tmp_path / "out" / "summary.csv").open() as f:
        summary = list(csv.DictReader(f))

    assert len(predictions) == 2 * 6 * 25  # Category + SubCategory
    assert {r["domain_mode"] for r in summary} == {"category", "sub_category"}
    assert {r["method"] for r in summary} == {"B0", "B1", "B2", "B3", "M", "X1"}
    outlier = next(
        r for r in predictions if r["method"] == "M" and r["t_act"] == "20.0"
    )
    assert outlier["used_for_learning"] == "False"
    assert "P1_block_median" in format_overview(summaries)


def test_sensitivity_covers_every_experiment(tmp_path: Path) -> None:
    summaries, rows = run(small_dataset(tmp_path), tmp_path / "out", check=False)

    grid = {r.setting for r in rows if r.experiment == "alpha_k0"}
    assert len(grid) == 6
    main_m = next(
        r.value
        for r in summaries["M"]
        if r.metric == "P1_block_median" and r.n_group == "all"
    )
    default = next(
        r
        for r in rows
        if r.setting == "alpha=0.2 k0=1.3" and r.metric == "P1_block_median"
    )
    assert default.value == main_m
    assert {r.setting for r in rows if r.experiment == "domain"} == {
        "category",
        "sub_category",
    }
    dropped = [
        r for r in rows if r.experiment == "ratio_one" and r.setting == "dropped"
    ]
    assert {r.method for r in dropped} == {"B0", "B1", "B2", "B3", "M", "X1"}
    assert "alpha=0.1 k0=1.0" in format_sensitivity(rows)


def test_drop_exact_ratio_one() -> None:
    tasks = [
        SipTask(1, 58, "D", "Bug", 2.0, 2.0, date(2005, 1, 1)),
        SipTask(2, 58, "D", "Bug", 2.0, 2.5, date(2005, 1, 2)),
    ]

    assert [t.task_number for t in drop_exact_ratio_one(tasks)] == [2]
