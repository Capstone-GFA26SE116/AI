"""Run K-SiP v2 (plans/K-sip-v2.md) and write results_v2.md.

Usage:
uv run python -m research.kbias_sip.run2 --data ~/data/SiP_dataset --out research/kbias_sip/results
"""

import argparse
from collections.abc import Sequence
from pathlib import Path

from research.kbias_sip import design2 as d2
from research.kbias_sip.load import (
    MIN_TASKS_PER_PAIR,
    SipTask,
    check_funnel,
    load_tasks,
)
from research.kbias_sip.methods import Constant, Metanoia, Method
from research.kbias_sip.replay import Pair, Prediction, group_pairs, replay
from research.kbias_sip.run import drop_exact_ratio_one

DEFAULT_K0 = 1.3
ONBOARDING_K0 = (1.1, 1.25, 1.5, 1.8)  # Backend MeService.OverrunKBias
ALL_K0 = (1.0, *ONBOARDING_K0[:2], DEFAULT_K0, *ONBOARDING_K0[2:])


def big_pairs(tasks: Sequence[SipTask], domain_mode: str) -> dict[Pair, list[SipTask]]:
    """Pairs with at least 10 tasks, in time order."""
    return {
        pair: ts
        for pair, ts in group_pairs(tasks, domain_mode).items()
        if len(ts) >= MIN_TASKS_PER_PAIR
    }


def run_method(
    pairs: dict[Pair, list[SipTask]], method: Method
) -> dict[Pair, list[Prediction]]:
    return {pair: replay(ts, method) for pair, ts in pairs.items()}


def user(pairs):
    return run_method(pairs, Constant("A", 1.0))


def fixed(pairs, k0=DEFAULT_K0):
    return run_method(pairs, Constant("B", k0))


def metanoia(pairs, k0=DEFAULT_K0, alpha=0.2):
    return run_method(pairs, Metanoia(code="C", alpha=alpha, k_bias=k0))


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def overview_rows(pairs) -> tuple[list[list[str]], dict]:
    runs = {
        "A — người dùng tự đoán": user(pairs),
        "B — luôn × 1,3": fixed(pairs),
        "C — METANOIA": metanoia(pairs),
    }
    stats = {name: d2.overall(run) for name, run in runs.items()}
    rows = [
        [
            name,
            pct(s["block_median"]),
            pct(s["block_mean"]),
            pct(s["task_median"]),
            pct(s["task_mean"]),
            pct(s["within_25"]),
        ]
        for name, s in stats.items()
    ]
    return rows, stats


def section_main(pairs, dates) -> list[str]:
    out = []
    rows, stats = overview_rows(pairs)
    a, c = stats["A — người dùng tự đoán"], stats["C — METANOIA"]
    out.append("## 1. Kết quả chung (Category, K₀ = 1,3, α = 0,2)\n")
    out.append(
        table(
            [
                "Cách",
                "Sai số tổng giờ khối 10 task (trung vị) ↓",
                "… (trung bình) ↓",
                "Sai số từng task (trung vị) ↓",
                "… (trung bình) ↓",
                "Task sai ≤ 25% ↑",
            ],
            rows,
        )
    )
    out.append(
        f"\nC so với A: tổng giờ tốt hơn **{pct(d2.gain(a['block_median'], c['block_median']))}** (trung vị), "
        f"từng task {'tốt hơn' if c['task_median'] < a['task_median'] else 'tệ hơn'} "
        f"**{pct(abs(d2.gain(a['task_median'], c['task_median'])))}** (trung vị). "
        f"{c['blocks']} khối, {c['tasks']} task.\n"
    )

    u, m = user(pairs), metanoia(pairs)
    out.append("## 2. Theo từng developer (khối sau 0 task)\n")
    devs = d2.compare_developers(u, m)
    out.append(
        table(
            [
                "Developer",
                "A: sai số tổng giờ",
                "C: sai số tổng giờ",
                "C tốt hơn A",
                "Đạt (≥ 20%)",
            ],
            [
                [
                    r.dev,
                    pct(r.user_error),
                    pct(r.metanoia_error),
                    pct(r.gain),
                    "✅" if r.passed else "❌",
                ]
                for r in devs
            ],
        )
    )
    out.append(f"\n**{sum(r.passed for r in devs)}/{len(devs)} developer đạt.**\n")

    out.append("## 3. Tiêu chí: cần bao nhiêu task\n")
    crit = d2.criterion(u, m)
    rows = []
    for r in crit:
        days, n_pairs = d2.days_to_reach(dates, r.after) if r.after else (0, len(dates))
        rows.append(
            [
                f"sau {r.after} task",
                f"{r.passed}/{r.eligible}",
                "✅" if r.met else "❌",
                "—" if days is None else f"{days:.0f} ngày ({n_pairs} cặp)",
            ]
        )
    out.append(
        table(
            [
                "Chỉ tính khối",
                "Developer đạt",
                f"Đạt tiêu chí (≥ {d2.TARGET_DEVS})",
                "Thời gian tương ứng (trung vị)",
            ],
            rows,
        )
    )
    first = next((r.after for r in crit if r.met), None)
    out.append(
        f"\n**N nhỏ nhất đạt tiêu chí: {first if first is not None else 'không đạt ở mọi N'}.**\n"
    )

    out.append("## 4. Sai số tổng giờ theo vị trí khối\n")
    pos = {
        name: d2.block_error_by_position(run)
        for name, run in (("A", u), ("B", fixed(pairs)), ("C", m))
    }
    labels = list(pos["A"])
    out.append(
        table(
            ["Cách", *[f"task {l}" for l in labels]],
            [[k, *[pct(v[l]) for l in labels]] for k, v in pos.items()],
        )
    )
    return out


def section_k0(pairs) -> list[str]:
    out = ["## 5. K₀ khác nhau (mức onboarding)\n"]
    u = user(pairs)
    ref = metanoia(pairs, DEFAULT_K0)
    a_stats = d2.overall(u)
    rows = []
    for k0 in ALL_K0:
        c = metanoia(pairs, k0)
        b = fixed(pairs, k0)
        c_stats, b_stats = d2.overall(c), d2.overall(b)
        first_block = d2.block_error_by_position(c)["1-10"]
        devs = d2.compare_developers(u, c)
        gaps = d2.k_gap_by_n(c, ref)
        close = "—" if k0 == DEFAULT_K0 else (d2.first_close(gaps) or "> 30")
        rows.append(
            [
                f"{k0:g}",
                pct(b_stats["block_median"]),
                pct(c_stats["block_median"]),
                pct(first_block),
                pct(d2.gain(a_stats["block_median"], c_stats["block_median"])),
                f"{sum(r.passed for r in devs)}/{len(devs)}",
                close,
            ]
        )
    out.append(
        table(
            [
                "K₀",
                "B: × K₀ không học",
                "C: METANOIA",
                "C ở 10 task đầu",
                "C tốt hơn A",
                "Developer đạt",
                "K về gần K₀=1,3 (< 5%) sau task thứ",
            ],
            rows,
        )
    )
    out.append(f"\nA (người dùng tự đoán): {pct(a_stats['block_median'])}.\n")
    return out


def section_sensitivity(tasks, pairs) -> list[str]:
    out = ["## 6. Bản chạy phụ\n"]
    rows = []

    def add(name, prs, alpha=0.2):
        u, c = user(prs), metanoia(prs, alpha=alpha)
        a_s, c_s = d2.overall(u), d2.overall(c)
        devs = d2.compare_developers(u, c)
        n_tasks = sum(len(v) for v in prs.values())
        rows.append(
            [
                name,
                f"{len(prs)} cặp · {n_tasks} task",
                pct(a_s["block_median"]),
                pct(c_s["block_median"]),
                pct(d2.gain(a_s["block_median"], c_s["block_median"])),
                pct(a_s["task_median"]),
                pct(c_s["task_median"]),
                f"{sum(r.passed for r in devs)}/{len(devs)}",
            ]
        )

    add("Bản chính (Category)", pairs)
    add(
        "Bỏ task giờ thật = ước tính",
        big_pairs(drop_exact_ratio_one(tasks), "category"),
    )
    add("Loại việc hẹp (SubCategory)", big_pairs(tasks, "sub_category"))
    add("α = 0,1", pairs, alpha=0.1)
    add("α = 0,3", pairs, alpha=0.3)
    out.append(
        table(
            [
                "Bản",
                "Dữ liệu",
                "A tổng giờ",
                "C tổng giờ",
                "C tốt hơn A",
                "A từng task",
                "C từng task",
                "Developer đạt",
            ],
            rows,
        )
    )
    return out


def build_report(data_dir: Path) -> str:
    tasks, funnel = load_tasks(data_dir)
    check_funnel(funnel, tasks)
    pairs = big_pairs(tasks, "category")
    dates = {pair: [t.estimate_on for t in ts] for pair, ts in pairs.items()}
    devs = {dev for dev, _ in pairs}
    ratios = [t.t_act / t.t_est for ts in pairs.values() for t in ts]
    faster = sum(r < 1 for r in ratios) / len(ratios)
    exact = sum(r == 1 for r in ratios) / len(ratios)
    parts = [
        "# K-SiP v2 — kết quả (plans/K-sip-v2.md)\n",
        (
            f"Dữ liệu: {len(pairs)} cặp (developer, Category), {len(ratios)} task, "
            f"{len(devs)} developer. Xong sớm hơn ước tính {pct(faster)}, "
            f"đúng bằng {pct(exact)}, trễ {pct(1 - faster - exact)}.\n"
        ),
        (
            "Sai số = |dự đoán − thật| ÷ thật. Chỉ số chính: sai số tổng giờ của "
            "khối 10 task liên tiếp (trung vị). Tiêu chí đạt: C tốt hơn A "
            f"≥ {d2.TARGET_GAIN:.0%} ở ≥ {d2.TARGET_DEVS} developer.\n"
        ),
        *section_main(pairs, dates),
        *section_k0(pairs),
        *section_sensitivity(tasks, pairs),
        "\n*Nguồn dữ liệu: SiP dataset, Software in Partnership Ltd / Derek M. Jones, CC BY-NC-ND 4.0.*\n",
    ]
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = build_report(args.data.expanduser())
    (args.out / "results_v2.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
