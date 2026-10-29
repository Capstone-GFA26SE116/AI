import csv
from pathlib import Path

from research.kbias_sip.run import format_overview, run
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

    for name in ("summary.csv", "predictions.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (
            tmp_path / "b" / name
        ).read_bytes()


def test_writes_every_method_and_prediction(tmp_path: Path) -> None:
    summaries = run(small_dataset(tmp_path), tmp_path / "out", check=False)

    with (tmp_path / "out" / "predictions.csv").open() as f:
        predictions = list(csv.DictReader(f))
    with (tmp_path / "out" / "summary.csv").open() as f:
        summary = list(csv.DictReader(f))

    assert len(predictions) == 6 * 25
    assert {r["method"] for r in summary} == {"B0", "B1", "B2", "B3", "M", "X1"}
    outlier = next(
        r for r in predictions if r["method"] == "M" and r["t_act"] == "20.0"
    )
    assert outlier["used_for_learning"] == "False"
    assert "P1_block_median" in format_overview(summaries)
