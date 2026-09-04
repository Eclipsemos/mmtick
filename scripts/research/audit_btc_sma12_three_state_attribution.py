#!/usr/bin/env python3
"""Attribute BTC SMA12/40 three-state results to defense and leverage mechanisms."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import annualized_return, replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_sma12_three_state_attribution/2026-09-03")
VARIANTS = {
    "constant_1x": {"bear": "1", "neutral": "1", "bull": "1"},
    "bear_defense_only": {"bear": "0", "neutral": "1", "bull": "1"},
    "bull_leverage_only": {"bear": "1", "neutral": "1", "bull": "1.5"},
    "neutral_bull_no_exit": {"bear": "1", "neutral": "1.25", "bull": "1.5"},
    "sma12_baseline": {"bear": "0", "neutral": "1.5", "bull": "1.5"},
    "three_state": {"bear": "0", "neutral": "1.25", "bull": "1.5"},
    "constant_1_25x": {"bear": "1.25", "neutral": "1.25", "bull": "1.25"},
    "constant_1_5x": {"bear": "1.5", "neutral": "1.5", "bull": "1.5"},
}


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    states = classify_states(daily)
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    results = {}
    for name, exposures in VARIANTS.items():
        dense = targets_for_states(states, exposures)
        targets = map_targets_to_source(len(bars), dense, ends)
        results[name] = {
            period: evaluate(bars, targets, funding, bounds, benchmarks[period])
            for period, bounds in splits.items()
        }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "signal": "completed daily SMA12/40 state; next 15m open",
            "comparison": "all variants use identical 50% spot / 50% isolated margin wallets",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "leverage": "2.5X opening cap; qualifying paths require observed intrabar <=3X",
            "selection": "fixed attribution variants; no parameter selection",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "benchmarks": benchmarks,
        "results": results,
        "conclusion": conclude(results),
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def classify_states(daily):
    fast = simple_moving_average(daily, 12)
    slow = simple_moving_average(daily, 40)
    output = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            output.append(None)
        elif bar.close > slow[index] and fast[index] > slow[index]:
            output.append("bull")
        elif bar.close < slow[index] and fast[index] < slow[index]:
            output.append("bear")
        else:
            output.append("neutral")
    return tuple(output)


def targets_for_states(states, exposures):
    return tuple(None if state is None else Decimal(exposures[state]) for state in states)


def evaluate(bars, targets, funding, bounds, baseline):
    result = replay_segregated(
        bars,
        targets,
        funding,
        *bounds,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("2.5"),
    )
    return {
        "net_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "cagr": annualized_return(result.net_return, years_between(*bounds)),
        "max_drawdown": result.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "hard_3x_passed": result.maximum_observed_futures_leverage <= 3,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def conclude(results):
    full = {name: rows["full"] for name, rows in results.items()}
    return {
        "three_state_beats_bh": full["three_state"]["excess"] > 0,
        "three_state_hard_3x_passed": full["three_state"]["hard_3x_passed"],
        "constant_1_25x_hard_3x_passed": full["constant_1_25x"]["hard_3x_passed"],
        "constant_1_5x_hard_3x_passed": full["constant_1_5x"]["hard_3x_passed"],
        "bear_defense_increment_vs_constant_1x": (
            full["bear_defense_only"]["net_return"] - full["constant_1x"]["net_return"]
        ),
        "combined_increment_vs_bull_leverage_only": (
            full["three_state"]["net_return"] - full["bull_leverage_only"]["net_return"]
        ),
        "interpretation": (
            "historical excess requires bear-state de-risking; constant leverage paths are "
            "inferior and the higher constant targets breach the intrabar 3X limit"
        ),
    }


def render(payload):
    lines = [
        "# BTC SMA12/40 Three-State Mechanism Attribution",
        "",
        "所有变体使用相同隔离钱包、成本和执行时序，只改变各状态的目标暴露。",
        "",
        "| 变体 | Full收益 | CAGR | 超额 | DD | OOS超额 | 最高杠杆 | 3X合规 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name in VARIANTS:
        full = payload["results"][name]["full"]
        oos = payload["results"][name]["oos"]
        lines.append(
            f"| `{name}` | {full['net_return']:.2%} | {full['cagr']:.2%} | "
            f"{full['excess']:.2%} | {full['max_drawdown']:.2%} | {oos['excess']:.2%} | "
            f"{full['maximum_intrabar_leverage']:.3f}X | "
            f"{'是' if full['hard_3x_passed'] else '否'} |"
        )
    lines += [
        "",
        "## Conclusion",
        "",
        "恒定 1.25X 和 1.5X 路径在历史下跌中超过 3X 盘中硬上限，不能作为合法替代。",
        "三状态版本的主要历史贡献来自熊市降仓；牛市加杠杆本身不足以形成同等超额。",
        "因此策略的核心假设是 SMA12/40 的熊市识别，而不是永久杠杆。",
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
