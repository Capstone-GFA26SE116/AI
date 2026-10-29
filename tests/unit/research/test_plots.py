from pathlib import Path

import pandas as pd

from research.kbias_sip.plots import plot_all, top_pairs
from research.kbias_sip.run import run
from tests.unit.research.test_run import small_dataset


def test_draws_the_three_charts(tmp_path: Path) -> None:
    out = tmp_path / "out"
    run(small_dataset(tmp_path), out, check=False)

    plot_all(out)

    for name in ("fig1_k_trajectory", "fig2_error_by_n", "fig3_sensitivity"):
        assert (out / f"{name}.png").stat().st_size > 10_000


def test_top_pairs_biggest_first() -> None:
    rows = [("category", "M", 1, "D")] * 2 + [("category", "M", 2, "D")] * 3
    rows += [("sub_category", "M", 3, "D")] * 9 + [("category", "B0", 4, "D")] * 9
    predictions = pd.DataFrame(rows, columns=["domain_mode", "method", "dev", "domain"])

    assert top_pairs(predictions) == [(2, "D"), (1, "D")]
