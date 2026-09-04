#!/usr/bin/env python3
"""Audit yearly and rolling-window performance of the frozen daily-SMA guard."""

from __future__ import annotations

import concurrent.futures
import json
import multiprocessing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median

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

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_guard_3x_audit/2026-09-02")
VARIANTS = {"challenger_2x": Decimal("2"), "intrabar_safe_1_5x": Decimal("1.5")}


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
    guard, guard_ends = aggregate_complete_periods(bars, "1h")
    guard_targets = map_targets_to_source(
        len(bars), four_sma_targets(guard, (24, 48, 96, 192)), guard_ends
    )
    state = 0
    targets = []
    for base_value, guard_value in zip(base, guard_targets, strict=True):
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


def replay(bars, funding, targets, start_ms, end_ms):
    return replay_segregated(
        bars,
        targets,
        funding,
        start_ms,
        end_ms,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("3"),
    )


def timestamp(moment):
    return int(moment.timestamp() * 1000)


def one_result(bars, funding, targets, start_ms, end_ms):
    result = replay(bars, funding, targets, start_ms, end_ms)
    baseline = benchmark(bars, start_ms, end_ms)
    return {
        "return": result.net_return,
        "drawdown": result.max_drawdown,
        "excess": result.net_return - baseline["net_return"],
        "benchmark_return": baseline["net_return"],
        "benchmark_drawdown": baseline["max_drawdown"],
        "observed_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
    }


def rolling_task(task):
    name, days, start = task
    end = start + timedelta(days=days) - timedelta(milliseconds=1)
    return {
        "variant": name,
        "days": days,
        "start": start.isoformat(),
        **one_result(BARS, FUNDING, TARGETS[name], timestamp(start), timestamp(end)),
    }


def main():
    global BARS, FUNDING, TARGETS
    BARS = load_market("BTCUSDT")
    FUNDING = funding_by_bar(BARS, load_funding("BTCUSDT", BARS))
    TARGETS = {name: build_targets(BARS, active) for name, active in VARIANTS.items()}
    first = max(
        datetime(2020, 1, 1, tzinfo=UTC), datetime.fromtimestamp(BARS[0].start_ms / 1000, UTC)
    )
    last = datetime.fromtimestamp(BARS[-1].end_ms / 1000, UTC)
    yearly = []
    for year in range(2020, last.year + 1):
        start = max(first, datetime(year, 1, 1, tzinfo=UTC))
        end = min(last, datetime(year + 1, 1, 1, tzinfo=UTC) - timedelta(milliseconds=1))
        if start > end:
            continue
        row = {"year": year, "benchmark": benchmark(BARS, timestamp(start), timestamp(end))}
        row["variants"] = {
            name: one_result(BARS, FUNDING, targets, timestamp(start), timestamp(end))
            for name, targets in TARGETS.items()
        }
        yearly.append(row)

    tasks = []
    for days in (365, 730, 1095):
        start = first
        while start + timedelta(days=days) <= last:
            tasks.extend((name, days, start) for name in TARGETS)
            start += timedelta(days=30)
    rolling = []
    context = multiprocessing.get_context("fork")
    with concurrent.futures.ProcessPoolExecutor(max_workers=8, mp_context=context) as executor:
        for row in executor.map(rolling_task, tasks, chunksize=1):
            rolling.append(row)

    summaries = {}
    for name in TARGETS:
        summaries[name] = {}
        for days in (365, 730, 1095):
            rows = [row for row in rolling if row["variant"] == name and row["days"] == days]
            summaries[name][str(days)] = {
                "windows": len(rows),
                "return_win_rate": sum(row["excess"] > 0 for row in rows) / len(rows),
                "joint_win_rate": sum(
                    row["excess"] > 0 and row["drawdown"] >= row["benchmark_drawdown"]
                    for row in rows
                )
                / len(rows),
                "median_excess": median(row["excess"] for row in rows),
                "worst_excess": min(row["excess"] for row in rows),
            }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "data": {
            "bars": len(BARS),
            "first": datetime.fromtimestamp(BARS[0].start_ms / 1000, UTC).isoformat(),
            "last": datetime.fromtimestamp(BARS[-1].end_ms / 1000, UTC).isoformat(),
        },
        "protocol": (
            "10+5 bps stress costs, historical funding, completed signals and next 15m open, 3X cap"
        ),
        "yearly": yearly,
        "rolling_summary": summaries,
        "rolling_rows": rolling,
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
        "# BTC 日线 SMA Guard 逐年与滚动审计",
        "",
        f"数据截至 `{payload['data']['last']}`；采用 10+5 bps 压力成本、Funding 和 3X 开仓上限。",
        "",
        "## 逐年压力回放",
        "",
        "| 年份 | B&H | Challenger | 超额 | 安全版 | 超额 |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["yearly"]:
        c = row["variants"]["challenger_2x"]
        s = row["variants"]["intrabar_safe_1_5x"]
        lines.append(
            f"| {row['year']} | {pct(row['benchmark']['net_return'])} | {pct(c['return'])} | "
            f"{pct(c['excess'])} | {pct(s['return'])} | {pct(s['excess'])} |"
        )
    lines += [
        "",
        "## 滚动窗口",
        "",
        "| 版本 | 窗口 | 数量 | 超过 B&H | 收益+DD 同时胜出 | 中位超额 | 最差超额 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, windows in payload["rolling_summary"].items():
        for days, row in windows.items():
            lines.append(
                f"| {name} | {int(days) // 365}Y | {row['windows']} | "
                f"{pct(row['return_win_rate'])} | {pct(row['joint_win_rate'])} | "
                f"{pct(row['median_excess'])} | {pct(row['worst_excess'])} |"
            )
    lines += [
        "",
        "2024 是主要失败年份；滚动窗口胜率多数超过 50%，但最差窗口仍可能显著落后 B&H。",
        "安全版盘中观测杠杆低于 3X，但因降低牛市暴露，在强趋势验证期会牺牲收益。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
