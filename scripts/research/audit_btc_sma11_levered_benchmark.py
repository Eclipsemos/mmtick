#!/usr/bin/env python3
"""Compare the frozen SMA11 candidate with a risk-matched 1.5x BTC benchmark."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as candidate

OUTPUT = Path("reports/experiments/btc_sma11_enter2_active150_hybrid/2026-09-03/levered-benchmark")
ACTIVE = Decimal("1.5")


def main() -> None:
    spot, futures, daily, target_indices, funding = candidate.load_hybrid_inputs()
    bars = spot + futures
    strategy_targets = candidate.map_targets(
        len(bars),
        target_indices,
        candidate.build_targets(daily, fast_period=11, enter_bear_days=2, active=ACTIVE),
    )
    benchmark_targets = constant_targets(len(bars), ACTIVE)
    bounds = candidate.periods(bars[-1].end_ms, spot[-1].end_ms)
    results = {}
    for name, period in bounds.items():
        one_x = candidate.benchmark(bars, *period)
        strategy = candidate.replay(bars, strategy_targets, funding, *period)
        matched = candidate.replay(bars, benchmark_targets, funding, *period)
        results[name] = {
            "strategy": candidate.public(strategy, one_x, *period),
            "one_x_buy_and_hold": one_x,
            "matched_1p5x_buy_and_hold": {
                "net_return": matched.net_return,
                "max_drawdown": matched.max_drawdown,
                "maximum_intrabar_leverage": matched.maximum_observed_futures_leverage,
                "liquidated": matched.liquidated,
                "fees": matched.total_fees,
                "funding": matched.total_funding,
            },
        }
        results[name]["strategy_vs_matched_excess"] = strategy.net_return - matched.net_return
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "strategy": "frozen SMA11/40 enter2-exit1 active1.5X, bear 0X",
            "matched_benchmark": "continuous 1.5X BTC target under identical replay mechanics",
            "shared_model": (
                "50% spot plus 50% isolated USD-M collateral, 2X futures opening control, "
                "<=3X effective-leverage protection, 10+5 bps costs, and historical Funding"
            ),
            "one_x_benchmark": "reported only for the original BTC B&H comparison",
        },
        "data": {"last": candidate.iso(bars[-1].end_ms), "bars": len(bars)},
        "results": results,
        "decision": {
            "beats_matched_all_splits": all(
                row["strategy_vs_matched_excess"] > 0 for row in results.values()
            ),
            "matched_benchmark_liquidated": any(
                row["matched_1p5x_buy_and_hold"]["liquidated"] for row in results.values()
            ),
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def constant_targets(length: int, exposure: Decimal):
    if length < 1 or exposure <= 0:
        raise ValueError("length and exposure must be positive")
    return (exposure,) + (None,) * (length - 1)


def render(payload) -> str:
    lines = [
        "# Frozen BTC SMA11 Hybrid Levered-Benchmark Audit",
        "",
        "将择时策略与持续 1.5X BTC 比较。双方使用相同抵押、成本、Funding 与盘中杠杆保护，"
        "以分离择时贡献和静态杠杆贡献。",
        "",
        "| 区间 | 策略 | 1X B&H | 持续1.5X | 策略-1.5X | 策略DD | 1.5X DD | 1.5X峰值杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["results"].items():
        strategy = row["strategy"]
        matched = row["matched_1p5x_buy_and_hold"]
        lines.append(
            f"| {name} | {strategy['strategy_return']:.2%} | "
            f"{row['one_x_buy_and_hold']['net_return']:.2%} | {matched['net_return']:.2%} | "
            f"{row['strategy_vs_matched_excess']:.2%} | {strategy['strategy_drawdown']:.2%} | "
            f"{matched['max_drawdown']:.2%} | {matched['maximum_intrabar_leverage']:.3f}X |"
        )
    decision = payload["decision"]
    lines += [
        "",
        "策略在所有分段超过风险匹配基准："
        f"{'是' if decision['beats_matched_all_splits'] else '否'}。",
        f"风险匹配基准发生强平：{'是' if decision['matched_benchmark_liquidated'] else '否'}。",
        "若持续 1.5X 基准发生强平，收益差不应解释为可实现的择时超额，而是风险控制机制的必要性。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
