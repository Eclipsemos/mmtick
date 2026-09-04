#!/usr/bin/env python3
"""Audit the frozen daily SMA candidate across rolling windows."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median

from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_rolling_3x/2026-09-02")
EVALUATION_START = datetime(2020, 1, 1, tzinfo=UTC)
STEP_DAYS = 30
WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1_095))
FAST = 8
SLOW = 40
BEAR_EXPOSURE = Decimal("-0.1")
BULL_EXPOSURE = Decimal("1.5")
SPOT_CAP = Decimal("0.5")
LEVERAGE_CAP = Decimal("2")


def utc_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def build_targets(bars):
    daily, ends = aggregate_complete_periods(bars, "1d")
    fast = simple_moving_average(daily, FAST)
    slow = simple_moving_average(daily, SLOW)
    dense = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            dense.append(None)
        elif bar.close < slow[index] and fast[index] < slow[index]:
            dense.append(BEAR_EXPOSURE)
        else:
            dense.append(BULL_EXPOSURE)
    return map_targets_to_source(len(bars), tuple(dense), ends), len(daily)


def replay(bars, targets, funding, start_ms, end_ms):
    return replay_segregated(
        bars,
        targets,
        funding,
        start_ms,
        end_ms,
        spot_cap=SPOT_CAP,
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=LEVERAGE_CAP,
    )


def evaluate_windows(bars, targets, funding, window_days):
    rows = []
    start = max(
        datetime.fromtimestamp(bars[0].start_ms / 1000, UTC),
        EVALUATION_START,
    )
    last_end = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC)
    while start + timedelta(days=window_days) <= last_end:
        end = start + timedelta(days=window_days) - timedelta(milliseconds=1)
        start_ms, end_ms = utc_ms(start), utc_ms(end)
        result = replay(bars, targets, funding, start_ms, end_ms)
        baseline = benchmark(bars, start_ms, end_ms)
        rows.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "strategy_return": result.net_return,
                "benchmark_return": baseline["net_return"],
                "excess": result.net_return - baseline["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": baseline["max_drawdown"],
                "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                "liquidated": result.liquidated,
            }
        )
        start += timedelta(days=STEP_DAYS)
    return rows


def summarize(rows):
    excess = [row["excess"] for row in rows]
    return {
        "total_windows": len(rows),
        "return_win_rate": ratio(row["excess"] > 0 for row in rows),
        "return_and_drawdown_win_rate": ratio(
            row["excess"] > 0 and row["strategy_drawdown"] >= row["benchmark_drawdown"]
            for row in rows
        ),
        "median_excess": median(excess),
        "worst_excess": min(excess),
        "best_excess": max(excess),
        "liquidations": sum(row["liquidated"] for row in rows),
        "maximum_intrabar_leverage": max(row["maximum_intrabar_leverage"] for row in rows),
    }


def ratio(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def pct(value):
    return f"{value:.2%}"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    targets, daily_bars = build_targets(bars)
    windows = {}
    for label, days in WINDOWS:
        rows = evaluate_windows(bars, targets, funding, days)
        windows[label] = {"summary": summarize(rows), "rows": rows}
        print(f"{label}: {len(rows)} windows", flush=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "frozen_candidate": {
            "symbol": "BTCUSDT",
            "signal": "daily SMA 8/40",
            "bull_exposure": str(BULL_EXPOSURE),
            "bear_exposure": str(BEAR_EXPOSURE),
            "spot_cap": str(SPOT_CAP),
            "leverage_cap": str(LEVERAGE_CAP),
        },
        "protocol": {
            "step_days": STEP_DAYS,
            "windows": {label: days for label, days in WINDOWS},
            "costs": "10 bps fee + 5 bps slippage",
            "funding": "historical funding on actual futures notional",
            "selection": "none; parameters are frozen",
        },
        "data": {"bars": len(bars), "daily_bars": daily_bars, "last": bars[-1].end_ms},
        "windows": windows,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload))
    print(OUTPUT_DIR / "README.md")


def markdown(payload):
    lines = [
        "# BTC 日线 SMA 8/40 滚动窗口审计（2X执行上限）",
        "",
        "固定参数、不重新选参；每 30 天移动起点，检验 1 年、2 年和 3 年窗口。",
        "",
        "| 窗口 | 数量 | 超过B&H | 收益+DD胜出 | 中位超额 | 最差超额 | 盘中最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, value in payload["windows"].items():
        summary = value["summary"]
        lines.append(
            f"| {label} | {summary['total_windows']} | "
            f"{pct(summary['return_win_rate'])} | "
            f"{pct(summary['return_and_drawdown_win_rate'])} | "
            f"{pct(summary['median_excess'])} | {pct(summary['worst_excess'])} | "
            f"{summary['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "收益+DD胜出要求策略收益高于 B&H 且最大回撤不差于 B&H。",
        "该审计不证明统计显著性；仍需要冻结后的新鲜 forward observation。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
