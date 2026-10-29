"""The three result charts of plan §2.6, drawn from the result CSVs.

fig1: K of the biggest pairs (method M) with each pair's target G and the theory curve.
fig2: error by task position n, one line per method (P1 by block group, S1 by n group).
fig3: alpha x K0 heat maps for M (P1 and S1).
"""

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no window; also makes the output the same on every machine

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

from research.kbias_sip.metrics import BLOCK_GROUPS, N_GROUPS

MAIN_DOMAIN_MODE = "category"
TOP_PAIRS = 6
METHOD_ORDER = ["B0", "B1", "B2", "B3", "M", "X1"]
METHOD_NAMES = {
    "B0": "B0 Raw estimate",
    "B1": "B1 Fixed 1.3",
    "B2": "B2 Mean",
    "B3": "B3 Last",
    "M": "M METANOIA (EMA)",
    "X1": "X1 EMA on log",
}
# Categorical slots in fixed order (validated: adjacent CVD and normal-vision pass).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
MARKERS = ["o", "s", "^", "D", "v", "P"]  # second encoding, so colour is never alone
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_MUTED = "#52514e"
GRID = "#e4e3df"
NEUTRAL = "#8a8984"
SEQUENTIAL = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
DPI = 150
SOURCE_NOTE = (
    "Data: SiP dataset, Software in Partnership Ltd / Derek M. Jones (CC BY-NC-ND 4.0)"
)


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT_MUTED,
            "axes.titlecolor": TEXT,
            "axes.titlesize": 11,
            "axes.labelsize": 9,
            "axes.grid": True,
            "axes.axisbelow": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "xtick.color": TEXT_MUTED,
            "ytick.color": TEXT_MUTED,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.frameon": False,
            "legend.fontsize": 8,
            "lines.linewidth": 2,
            "lines.solid_capstyle": "round",
            "lines.solid_joinstyle": "round",
            "font.size": 9,
            "svg.hashsalt": "kbias-sip",
        }
    )


def _save(fig: plt.Figure, path: Path) -> None:
    fig.text(0.01, 0.005, SOURCE_NOTE, fontsize=7, color=TEXT_MUTED)
    fig.savefig(path, dpi=DPI, metadata={"Software": None})
    plt.close(fig)


def top_pairs(
    predictions: pd.DataFrame, count: int = TOP_PAIRS
) -> list[tuple[int, str]]:
    """The pairs with the most tasks (main domain mode), biggest first; ties by key."""
    rows = predictions[
        (predictions["domain_mode"] == MAIN_DOMAIN_MODE)
        & (predictions["method"] == "M")
    ]
    sizes = rows.groupby(["dev", "domain"]).size().reset_index(name="tasks")
    sizes = sizes.sort_values(["tasks", "dev", "domain"], ascending=[False, True, True])
    return [(int(r.dev), str(r.domain)) for r in sizes.head(count).itertuples()]


def plot_k_trajectory(
    predictions: pd.DataFrame, path: Path, alpha: float = 0.2
) -> None:
    """fig1: K of method M per task for the biggest pairs, with G and the theory curve."""
    _style()
    pairs = top_pairs(predictions)
    cols = 3
    fig, axes = plt.subplots(
        math.ceil(len(pairs) / cols), cols, figsize=(11, 6.2), squeeze=False
    )
    for ax, (dev, domain) in zip(axes.flat, pairs, strict=False):
        rows = predictions[
            (predictions["domain_mode"] == MAIN_DOMAIN_MODE)
            & (predictions["method"] == "M")
            & (predictions["dev"] == dev)
            & (predictions["domain"] == domain)
        ].sort_values("n")
        learned = rows[rows["used_for_learning"]]
        g = math.exp((learned["t_act"] / learned["t_est"]).map(math.log).mean())
        k0 = rows["k"].iloc[0]
        n = rows["n"]
        theory = g + (k0 - g) * (1 - alpha) ** (n - 1)
        ax.plot(n, rows["k"], color=SERIES[0], linewidth=1.2, label="K learned (M)")
        ax.plot(
            n,
            theory,
            color=SERIES[1],
            linewidth=2,
            label="Theory if ratio were constant",
        )
        ax.axhline(
            g,
            color=NEUTRAL,
            linewidth=1.5,
            linestyle=(0, (4, 3)),
            label="G (long-run ratio)",
        )
        ax.set_xscale("log")
        ax.set_title(f"Developer {dev} · {domain} ({len(rows)} tasks)", loc="left")
        ax.set_xlabel("Task position n (log scale)")
        ax.set_ylabel("K")
    for ax in list(axes.flat)[len(pairs) :]:
        ax.set_visible(False)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", ncol=3)
    fig.suptitle(
        "How K moves for the six biggest developer-domain pairs", x=0.01, ha="left"
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.94))
    _save(fig, path)


def _method_lines(ax: plt.Axes, table: pd.DataFrame, groups: list[str]) -> None:
    x = range(len(groups))
    for i, method in enumerate(METHOD_ORDER):
        if method not in table.index:
            continue
        values = [table.loc[method].get(g, math.nan) for g in groups]
        ax.plot(
            x,
            values,
            color=SERIES[i],
            marker=MARKERS[i],
            markersize=5,
            markeredgecolor=SURFACE,
            markeredgewidth=1.5,
            linewidth=2.6 if method == "M" else 1.6,
            label=METHOD_NAMES[method],
        )
    ax.set_xticks(list(x), groups)
    ax.set_ylim(bottom=0)


def plot_error_by_n(summary: pd.DataFrame, path: Path) -> None:
    """fig2: P1 per block group and S1 per n group, one line per method."""
    _style()
    main = summary[summary["domain_mode"] == MAIN_DOMAIN_MODE]
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.4))
    p1 = main[main["metric"] == "P1_block_median"].pivot(
        index="method", columns="n_group", values="value"
    )
    s1 = main[main["metric"] == "S1_median_abs"].pivot(
        index="method", columns="n_group", values="value"
    )
    _method_lines(left, p1, [g[0] for g in BLOCK_GROUPS])
    left.set_title("P1 · error of total hours per 10-task block (main)", loc="left")
    left.set_xlabel("Task position n (10-task blocks)")
    left.set_ylabel("Median |ln(predicted / actual)|")
    _method_lines(right, s1, [g[0] for g in N_GROUPS])
    right.set_title("S1 · error per task", loc="left")
    right.set_xlabel("Task position n")
    handles, labels = left.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.01, 0.93), ncol=6)
    fig.suptitle(
        "Prediction error by how many tasks were already learned (lower is better)",
        x=0.01,
        ha="left",
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.86))
    _save(fig, path)


def plot_sensitivity(sensitivity: pd.DataFrame, path: Path) -> None:
    """fig3: alpha x K0 heat maps of P1 and S1 for method M."""
    _style()
    grid = sensitivity[sensitivity["experiment"] == "alpha_k0"].copy()
    parts = grid["setting"].str.extract(r"alpha=(?P<alpha>[\d.]+) k0=(?P<k0>[\d.]+)")
    grid = grid.join(parts)
    cmap = LinearSegmentedColormap.from_list("blue", SEQUENTIAL)
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6))
    titles = {
        "P1_block_median": "P1 · total hours (main)",
        "S1_median_abs": "S1 · per task",
    }
    for ax, (metric, title) in zip(axes, titles.items(), strict=True):
        table = grid[grid["metric"] == metric].pivot(
            index="alpha", columns="k0", values="value"
        )
        image = ax.imshow(table.values, cmap=cmap, aspect="auto")
        low, high = table.values.min(), table.values.max()
        for (row, col), value in pd.DataFrame(table.values).stack().items():
            dark = (value - low) / ((high - low) or 1) > 0.5
            ax.text(
                col,
                row,
                f"{value:.3f}",
                ha="center",
                va="center",
                fontsize=10,
                color=SURFACE if dark else TEXT,
            )
        ax.set_xticks(range(len(table.columns)), [f"K0 = {c}" for c in table.columns])
        ax.set_yticks(range(len(table.index)), [f"α = {a}" for a in table.index])
        ax.grid(False)
        ax.set_title(title, loc="left")
        fig.colorbar(image, ax=ax, shrink=0.8)
    fig.suptitle(
        "Method M: effect of learning rate α and starting K0 (lower is better)",
        x=0.01,
        ha="left",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.92))
    _save(fig, path)


def plot_all(out_dir: Path) -> None:
    """Draw the three charts from the CSVs in out_dir."""
    predictions = pd.read_csv(out_dir / "predictions.csv")
    summary = pd.read_csv(
        out_dir / "summary.csv", keep_default_na=False, na_values=[""]
    )
    sensitivity = pd.read_csv(out_dir / "sensitivity.csv")
    plot_k_trajectory(predictions, out_dir / "fig1_k_trajectory.png")
    plot_error_by_n(summary, out_dir / "fig2_error_by_n.png")
    plot_sensitivity(sensitivity, out_dir / "fig3_sensitivity.png")
