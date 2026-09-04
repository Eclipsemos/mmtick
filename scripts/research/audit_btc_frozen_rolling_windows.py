#!/usr/bin/env python3
"""Audit the frozen BTC candidate across rolling multi-year windows."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median

from research_btc_dynamic_exposure import benchmark, replay_dynamic_incremental
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

PERIODS = (26, 52, 104, 208)
BULL_EXPOSURE = Decimal("1.5")
FUNDING_THRESHOLD = Decimal("0.0001")
EVALUATION_START = datetime(2020, 1, 1, tzinfo=UTC)
STEP_DAYS = 30
WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1_095))


def main() -> None:
    output_dir = Path("reports/experiments/btc_frozen_rolling_windows/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    regime = map_targets_to_source(
        len(bars),
        three_state_targets(aggregate, PERIODS, Decimal("0"), BULL_EXPOSURE),
        ends,
    )
    targets = funding_aware_targets(regime, funding, BULL_EXPOSURE, FUNDING_THRESHOLD)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "frozen_config": {
            "timeframe": "4h",
            "sma_periods": PERIODS,
            "bear_exposure": "0",
            "neutral_exposure": "1",
            "bull_exposure": str(BULL_EXPOSURE),
            "funding_threshold": str(FUNDING_THRESHOLD),
        },
        "protocol": {
            "step_days": STEP_DAYS,
            "windows": {label: days for label, days in WINDOWS},
            "base_cost": "5 bps fee + 2 bps slippage on changed notional",
            "stress_cost": "10 bps fee + 5 bps slippage on changed notional",
            "funding": "charged only to exposure above 1x",
            "selection": "none; frozen candidate is used in every window",
        },
        "windows": {},
    }
    for label, window_days in WINDOWS:
        rows = evaluate_windows(bars, funding, targets, window_days)
        payload["windows"][label] = {
            "summary": summarize(rows),
            "rows": rows,
        }
        print(f"{label}: {len(rows)} windows", flush=True)
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def evaluate_windows(bars, funding, targets, window_days):
    rows = []
    start = max(datetime.fromtimestamp(bars[0].start_ms / 1000, UTC), EVALUATION_START)
    last_end = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC)
    while start + timedelta(days=window_days) <= last_end:
        end = start + timedelta(days=window_days) - timedelta(milliseconds=1)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        base = replay_dynamic_incremental(
            bars,
            targets,
            funding,
            start_ms,
            end_ms,
            funding_on_excess_only=True,
        )
        stress = replay_dynamic_incremental(
            bars,
            targets,
            funding,
            start_ms,
            end_ms,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            funding_on_excess_only=True,
        )
        baseline = benchmark(bars, start_ms, end_ms)
        rows.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "strategy_return": base.net_return,
                "stress_return": stress.net_return,
                "benchmark_return": baseline["net_return"],
                "base_excess": base.net_return - baseline["net_return"],
                "stress_excess": stress.net_return - baseline["net_return"],
                "strategy_drawdown": base.max_drawdown,
                "stress_drawdown": stress.max_drawdown,
                "benchmark_drawdown": baseline["max_drawdown"],
                "base_beats_return_and_drawdown": (
                    base.net_return > baseline["net_return"]
                    and base.max_drawdown >= baseline["max_drawdown"]
                ),
                "stress_beats_return_and_drawdown": (
                    stress.net_return > baseline["net_return"]
                    and stress.max_drawdown >= baseline["max_drawdown"]
                ),
            }
        )
        start += timedelta(days=STEP_DAYS)
    return rows


def summarize(rows):
    base_excess = [row["base_excess"] for row in rows]
    stress_excess = [row["stress_excess"] for row in rows]
    return {
        "total_windows": len(rows),
        "base_return_win_rate": ratio(row["base_excess"] > 0 for row in rows),
        "stress_return_win_rate": ratio(row["stress_excess"] > 0 for row in rows),
        "base_return_and_drawdown_win_rate": ratio(
            row["base_beats_return_and_drawdown"] for row in rows
        ),
        "stress_return_and_drawdown_win_rate": ratio(
            row["stress_beats_return_and_drawdown"] for row in rows
        ),
        "median_base_excess": median(base_excess),
        "median_stress_excess": median(stress_excess),
        "worst_base_excess": min(base_excess),
        "worst_stress_excess": min(stress_excess),
        "best_base_excess": max(base_excess),
        "best_stress_excess": max(stress_excess),
    }


def ratio(values):
    items = list(values)
    return sum(items) / len(items)


def markdown(payload):
    lines = [
        "# BTC 冻结策略滚动窗口审计",
        "",
        "固定使用冻结参数，每 30 天移动起点，测试所有完整 1 年、2 年和 3 年窗口。",
        "",
        "| 窗口 | 数量 | 基准胜率 | 压力胜率 | 收益+DD胜率 | 压力收益+DD | 中位超额 | 最差超额 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, result in payload["windows"].items():
        value = result["summary"]
        lines.append(
            f"| {label} | {value['total_windows']} | "
            f"{pct(value['base_return_win_rate'])} | "
            f"{pct(value['stress_return_win_rate'])} | "
            f"{pct(value['base_return_and_drawdown_win_rate'])} | "
            f"{pct(value['stress_return_and_drawdown_win_rate'])} | "
            f"{pct(value['median_base_excess'])} | {pct(value['worst_base_excess'])} |"
        )
    lines += [
        "",
        "胜率表示策略在该滚动窗口内超过同期 BTC B&H；收益+DD要求收益更高且最大回撤不更差。",
        "",
    ]
    return "\n".join(lines)


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
