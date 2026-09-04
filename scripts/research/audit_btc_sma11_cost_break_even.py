#!/usr/bin/env python3
"""Measure execution-cost break-even levels for the frozen BTC SMA11 candidate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as candidate

OUTPUT = Path("reports/experiments/btc_sma11_enter2_active150_hybrid/2026-09-03/cost-break-even")
TOTAL_SIDE_COST_BPS = (15, 30, 45, 60, 75, 90, 120)


def main() -> None:
    spot, futures, daily, target_indices, funding = candidate.load_hybrid_inputs()
    bars = spot + futures
    targets = candidate.map_targets(
        len(bars),
        target_indices,
        candidate.build_targets(daily, fast_period=11, enter_bear_days=2, active=Decimal("1.5")),
    )
    bounds = candidate.periods(bars[-1].end_ms, spot[-1].end_ms)
    benchmarks = {name: candidate.benchmark(bars, *period) for name, period in bounds.items()}
    results = {}
    for cost in TOTAL_SIDE_COST_BPS:
        fee_bps, slippage_bps = split_cost(cost)
        rows = {}
        for name, period in bounds.items():
            result = candidate.replay(
                bars,
                targets,
                funding,
                *period,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
            rows[name] = candidate.public(result, benchmarks[name], *period)
        results[str(cost)] = {
            "fee_bps": str(fee_bps),
            "slippage_bps": str(slippage_bps),
            "periods": rows,
        }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "candidate": "frozen SMA11/40 enter2-exit1 active1.5X",
            "variable": "total per-side execution cost only; Funding remains historical",
            "fee_slippage_ratio": "two thirds fee, one third slippage",
            "execution": "completed UTC daily signal; next 15m open",
            "hard_cap": "2X futures opening control and <=3X observed effective leverage",
            "selection": "no cost point is used to change the frozen candidate",
        },
        "data": {"last": candidate.iso(bars[-1].end_ms), "cost_levels_bps": TOTAL_SIDE_COST_BPS},
        "results": results,
        "break_even": break_even(results),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def split_cost(total_bps: int) -> tuple[Decimal, Decimal]:
    total = Decimal(total_bps)
    fee = total * Decimal(2) / Decimal(3)
    return fee, total - fee


def break_even(results: dict) -> dict:
    output = {}
    for name in ("research", "validation", "oos", "full"):
        passing = [
            int(cost) for cost, values in results.items() if values["periods"][name]["excess"] > 0
        ]
        output[name] = {
            "highest_tested_positive_cost_bps": max(passing) if passing else None,
            "first_tested_non_positive_cost_bps": next(
                (
                    int(cost)
                    for cost, values in results.items()
                    if values["periods"][name]["excess"] <= 0
                ),
                None,
            ),
        }
    return output


def render(payload) -> str:
    lines = [
        "# Frozen BTC SMA11 Hybrid Cost Break-Even Audit",
        "",
        "只提高每边执行成本，保留冻结信号、仓位、Funding、抵押和严格杠杆控制。",
        "",
        "| 每边总成本 | Research超额 | Validation超额 | OOS超额 | Full超额 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for cost, values in payload["results"].items():
        periods = values["periods"]
        lines.append(
            f"| {cost} bps | {periods['research']['excess']:.2%} | "
            f"{periods['validation']['excess']:.2%} | {periods['oos']['excess']:.2%} | "
            f"{periods['full']['excess']:.2%} |"
        )
    lines += ["", "## Tested Boundary", ""]
    for name, row in payload["break_even"].items():
        lines.append(
            f"- {name}: 最后一个正超额测试点 `"
            f"{row['highest_tested_positive_cost_bps']} bps`；"
            f"首个非正测试点 `{row['first_tested_non_positive_cost_bps']} bps`。"
        )
    lines += [
        "",
        "测试点给出区间，不等于精确可成交成本门槛；实际交易需要以前向实际费率和滑点复核。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
