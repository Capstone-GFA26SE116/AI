"""Run K-Renzo (plans/K-renzo.md) with the K-SiP v2 measures and write results.md.

Usage:
uv run python -m research.kbias_renzo.run --data ~/data/renzo-pomodoro --out research/kbias_renzo/results
"""

import argparse
from pathlib import Path

from research.kbias_renzo.load import load_tasks
from research.kbias_sip import design2 as d2
from research.kbias_sip.run import drop_exact_ratio_one
from research.kbias_sip.run2 import (
    ALL_K0,
    DEFAULT_K0,
    big_pairs,
    fixed,
    metanoia,
    pct,
    table,
    user,
)

ALPHAS = (0.1, 0.2, 0.3)


def alpha_decision(block: dict[float, float], user_block: float) -> str:
    """Plan §0 rule, fixed before the run: keep 0.3 only if no worse than 0.2 and both beat A."""
    keep = (
        block[0.3] <= block[0.2] and block[0.2] < user_block and block[0.3] < user_block
    )
    return (
        "α = 0,3 ĐẠT luật (đề xuất đổi)"
        if keep
        else "α = 0,3 KHÔNG đạt luật (giữ α = 0,2)"
    )


def section_main(pairs) -> list[str]:
    a = d2.overall(user(pairs))
    b = d2.overall(fixed(pairs))
    cs = {alpha: d2.overall(metanoia(pairs, alpha=alpha)) for alpha in ALPHAS}
    rows = [
        [
            "A — tự đoán",
            pct(a["block_median"]),
            pct(a["task_median"]),
            pct(a["within_25"]),
            "—",
        ],
        [
            "B — luôn × 1,3",
            pct(b["block_median"]),
            pct(b["task_median"]),
            pct(b["within_25"]),
            pct(d2.gain(a["block_median"], b["block_median"])),
        ],
    ]
    for alpha, c in cs.items():
        rows.append(
            [
                f"C — METANOIA α = {alpha:g}",
                pct(c["block_median"]),
                pct(c["task_median"]),
                pct(c["within_25"]),
                pct(d2.gain(a["block_median"], c["block_median"])),
            ]
        )
    out = [
        "## 1. Bản chính: một người, một K (K₀ = 1,3)\n",
        table(
            [
                "Cách",
                "Sai số tổng giờ khối 10 task ↓",
                "Sai số từng task ↓",
                "Task sai ≤ 25% ↑",
                "Tổng giờ tốt hơn A",
            ],
            rows,
        ),
    ]
    block = {alpha: c["block_median"] for alpha, c in cs.items()}
    target = d2.gain(a["block_median"], block[0.2]) >= d2.TARGET_GAIN
    out.append(f"\n{a['blocks']} khối, {a['tasks']} task.\n")
    out.append(
        f"**Luật α (chốt trước):** {alpha_decision(block, a['block_median'])}.\n"
    )
    out.append(f"**Mục tiêu 20% (α = 0,2):** {'đạt' if target else 'không đạt'}.\n")

    pos = {
        "A": d2.block_error_by_position(user(pairs)),
        "C α 0,2": d2.block_error_by_position(metanoia(pairs)),
        "C α 0,3": d2.block_error_by_position(metanoia(pairs, alpha=0.3)),
    }
    labels = list(pos["A"])
    out.append("### Theo vị trí khối\n")
    out.append(
        table(
            ["Cách", *[f"task {l}" for l in labels]],
            [[k, *[pct(v[l]) for l in labels]] for k, v in pos.items()],
        )
    )
    return out


def section_k0(pairs) -> list[str]:
    a = d2.overall(user(pairs))["block_median"]
    ref = metanoia(pairs, DEFAULT_K0)
    rows = []
    for k0 in ALL_K0:
        c = metanoia(pairs, k0)
        gaps = d2.k_gap_by_n(c, ref)
        rows.append(
            [
                f"{k0:g}",
                pct(d2.overall(fixed(pairs, k0))["block_median"]),
                pct(d2.overall(c)["block_median"]),
                pct(d2.block_error_by_position(c)["1-10"]),
                "—" if k0 == DEFAULT_K0 else (d2.first_close(gaps) or "> 30"),
            ]
        )
    return [
        "\n## 2. K₀ khác nhau (α = 0,2)\n",
        table(
            [
                "K₀",
                "B: × K₀ không học",
                "C: METANOIA",
                "C ở 10 task đầu",
                "K về gần K₀=1,3 (< 5%) sau task thứ",
            ],
            rows,
        ),
        f"\nA (tự đoán): {pct(a)}.\n",
    ]


def section_side(tasks) -> list[str]:
    rows = []

    def add(name, prs, count_pairs=False):
        u = user(prs)
        a = d2.overall(u)
        row = [
            name,
            f"{len(prs)} cặp · {sum(len(v) for v in prs.values())} task",
            pct(a["block_median"]),
        ]
        for alpha in ALPHAS:
            row.append(pct(d2.overall(metanoia(prs, alpha=alpha))["block_median"]))
        if count_pairs:
            passed = 0
            for pair in prs:
                ua = d2.block_errors(u[pair])
                ca = d2.block_errors(metanoia({pair: prs[pair]})[pair])
                if (
                    ua
                    and ca
                    and d2.gain(d2.median_or_none(ua), d2.median_or_none(ca))
                    >= d2.TARGET_GAIN
                ):
                    passed += 1
            eligible = sum(1 for v in prs.values() if len(v) >= d2.BLOCK)
            row.append(f"{passed}/{eligible}")
        else:
            row.append("—")
        rows.append(row)

    add("Bỏ task actual = estimate", big_pairs(drop_exact_ratio_one(tasks), "category"))
    add(
        "Theo khách / dự án (nhãn đầu)",
        big_pairs(tasks, "sub_category"),
        count_pairs=True,
    )
    return [
        "\n## 3. Bản phụ (sai số tổng giờ)\n",
        table(
            [
                "Bản",
                "Dữ liệu",
                "A",
                "C α 0,1",
                "C α 0,2",
                "C α 0,3",
                "Cặp C (α 0,2) tốt hơn A ≥ 20%",
            ],
            rows,
        ),
    ]


def build_report(data_dir: Path) -> str:
    tasks, funnel = load_tasks(data_dir)
    pairs = big_pairs(tasks, "category")
    ratios = [t.t_act / t.t_est for t in tasks]
    faster = sum(r < 1 for r in ratios) / len(ratios)
    exact = sum(r == 1 for r in ratios) / len(ratios)
    first, last = tasks[0].estimate_on, tasks[-1].estimate_on
    return "\n".join(
        [
            "# K-Renzo — kết quả (plans/K-renzo.md)\n",
            (
                f"Lọc: {funnel.rows} dòng → có số {funnel.with_numbers} → xong {funnel.done} → "
                f"> 0 {funnel.positive} → bỏ @meeting **{funnel.not_meeting} task** ({first} → {last}). "
                f"Xong sớm hơn ước tính {pct(faster)}, đúng bằng {pct(exact)}, trễ {pct(1 - faster - exact)}.\n"
            ),
            *section_main(pairs),
            *section_k0(pairs),
            *section_side(tasks),
            (
                "\n*Nguồn dữ liệu: Renzo Pomodoro dataset, Renzo Borgatti / "
                "Derek M. Jones, github.com/Derek-Jones/renzo-pomodoro.*\n"
            ),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = build_report(args.data.expanduser())
    (args.out / "results.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
