#!/usr/bin/env python3
"""Separate BTC timing edge from leverage using spot-only daily SMA hysteresis."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_market, split_periods

from mastermind_tick.bar_research import evaluate_targets
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_spot_timing_baseline/2026-09-03")
CONFIGS = ((10, 40, 2, 1), (10, 40, 3, 1), (12, 40, 2, 1), (12, 40, 3, 1))
COSTS = (("default", Decimal("10"), Decimal("5")), ("stress", Decimal("50"), Decimal("25")))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    daily, ends = aggregate_complete_periods(bars, "1d")
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    rows = []
    for fast, slow, enter, exit_days in CONFIGS:
        dense = targets(daily, fast, slow, enter, exit_days)
        mapped = map_targets_to_source(len(bars), dense, ends)
        metrics = {}
        for name, bounds in splits.items():
            metrics[name] = {}
            for label, fee, slip in COSTS:
                result = evaluate_targets(
                    bars,
                    mapped,
                    start_ms=bounds[0],
                    end_ms=bounds[1],
                    fee_bps=fee,
                    slippage_bps=slip,
                    funding=[[] for _ in bars],
                )
                metrics[name][label] = {
                    "net_return": result.net_return,
                    "benchmark_return": benchmarks[name]["net_return"],
                    "excess": result.net_return - benchmarks[name]["net_return"],
                    "max_drawdown": result.max_drawdown,
                    "trades": result.completed_trades,
                    "fees": result.total_fees,
                }
        rows.append(
            {
                "id": f"spot-sma{fast}/{slow}-enter{enter}-exit{exit_days}",
                "metrics": metrics,
                "development_worst_excess": min(
                    metrics[name][cost]["excess"]
                    for name in ("research", "validation")
                    for cost in ("default", "stress")
                ),
            }
        )
    rows.sort(key=lambda row: row["development_worst_excess"], reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "protocol": {
            "position": "100% spot when active; 0% cash when bearish",
            "signal": "completed UTC daily SMA; next 15m open",
            "costs": "10+5 bps default and 50+25 bps stress per side",
            "purpose": "diagnostic separation of timing signal from leverage and funding",
            "selection": "Research/Validation only; OOS reported, not selected",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "benchmarks": benchmarks,
        "results": rows,
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def targets(daily, fast_period: int, slow_period: int, enter: int, exit_days: int):
    fast = simple_moving_average(daily, fast_period)
    slow = simple_moving_average(daily, slow_period)
    state = None
    bear_count = recovery_count = 0
    output = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            output.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= enter:
            state = "bear"
        elif state == "bear" and recovery_count >= exit_days:
            state = "active"
        output.append(0 if state == "bear" else 1)
    return tuple(output)


def render(payload):
    lines = [
        "# BTC Spot Timing Baseline",
        "",
        "100% 现货多头/现金的日线 SMA 迟滞，剥离杠杆和 Funding 对收益的贡献。",
        "",
        "| 配置 | 开发最差 | Research压力 | Validation压力 | OOS默认 | Full默认 | Full DD |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        m = row["metrics"]
        lines.append(
            f"| `{row['id']}` | {row['development_worst_excess']:.2%} | "
            f"{m['research']['stress']['excess']:.2%} | "
            f"{m['validation']['stress']['excess']:.2%} | "
            f"{m['oos']['default']['excess']:.2%} | {m['full']['default']['excess']:.2%} | "
            f"{m['full']['default']['max_drawdown']:.2%} |"
        )
    lines += ["", "状态：**RESEARCH_ONLY**。", ""]
    return "\n".join(lines)


def iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
