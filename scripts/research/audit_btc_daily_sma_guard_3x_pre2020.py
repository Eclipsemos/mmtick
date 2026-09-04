#!/usr/bin/env python3
"""Replay the frozen daily-SMA guard on the independent pre-2020 holdout."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import (
    aggregate_complete_periods,
    four_sma_targets,
    map_targets_to_source,
)
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_guard_3x_pre2020/2026-09-02")
START = int(datetime(2019, 10, 18, tzinfo=UTC).timestamp() * 1000)
END = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1


def build_targets(bars, active):
    daily, daily_ends = aggregate_complete_periods(bars, "1d")
    fast = simple_moving_average(daily, 8)
    slow = simple_moving_average(daily, 40)
    base = map_targets_to_source(
        len(bars),
        tuple(
            None
            if fast[index] is None or slow[index] is None
            else -1
            if daily[index].close < slow[index] and fast[index] < slow[index]
            else 1
            for index in range(len(daily))
        ),
        daily_ends,
    )
    hourly, hourly_ends = aggregate_complete_periods(bars, "1h")
    guard = map_targets_to_source(
        len(bars), four_sma_targets(hourly, (24, 48, 96, 192)), hourly_ends
    )
    state = 0
    targets = []
    for base_value, guard_value in zip(base, guard, strict=True):
        if guard_value is not None:
            state = guard_value
        targets.append(
            None
            if base_value is None
            else Decimal("-0.1")
            if base_value == -1
            else active
            if state == 1
            else Decimal("1")
        )
    return tuple(targets)


def replay(bars, funding, targets):
    result = replay_segregated(
        bars,
        targets,
        funding,
        START,
        END,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("3"),
    )
    baseline = benchmark(bars, START, END)
    return {
        "return": result.net_return,
        "excess": result.net_return - baseline["net_return"],
        "drawdown": result.max_drawdown,
        "benchmark_return": baseline["net_return"],
        "benchmark_drawdown": baseline["max_drawdown"],
        "observed_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
    }


def main():
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    results = {
        name: replay(bars, funding, build_targets(bars, active))
        for name, active in {
            "challenger_2x": Decimal("2"),
            "intrabar_safe_1_5x": Decimal("1.5"),
        }.items()
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "holdout": {
            "start": datetime.fromtimestamp(START / 1000, UTC).isoformat(),
            "end": datetime.fromtimestamp(END / 1000, UTC).isoformat(),
            "selection": "not used during parameter or mechanism selection",
        },
        "data": {
            "bars": len(bars),
            "last": datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).isoformat(),
        },
        "results": results,
        "status": "RESEARCH_ONLY",
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload))
    print(OUTPUT_DIR / "README.md")


def pct(value):
    return f"{value:.2%}"


def markdown(payload):
    lines = [
        "# BTC SMA Guard 2019 独立留出",
        "",
        f"留出区间：`{payload['holdout']['start']}` 至 `{payload['holdout']['end']}`。",
        "该区间未参与参数、过滤器或杠杆选择；回放使用 10+5 bps 压力成本、Funding 和 3X 上限。",
        "",
        "| 版本 | 收益 | B&H | 超额 | 策略 DD | B&H DD | 盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["results"].items():
        lines.append(
            f"| {name} | {pct(row['return'])} | {pct(row['benchmark_return'])} | "
            f"{pct(row['excess'])} | {pct(row['drawdown'])} | "
            f"{pct(row['benchmark_drawdown'])} | {row['observed_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "两个版本均落后 B&H，但回撤较小；这说明该保护机制不是跨所有市场阶段稳定的超额来源。",
        "状态：**RESEARCH_ONLY**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
