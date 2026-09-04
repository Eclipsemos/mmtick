#!/usr/bin/env python3
"""Audit the selected daily SMA slope-gate candidate without re-selecting it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import replay_segregated
from research_btc_daily_sma_slope_gate_3x import (
    FAST,
    FEE_BPS,
    HOLDOUT_END,
    HOLDOUT_START,
    MAINTENANCE,
    MAX_LEVERAGE,
    SLIPPAGE_BPS,
    SLOW,
    SPOT_CAP,
    build_targets,
    pct,
)
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_slope_gate_audit/2026-09-02")
LOOKBACK = 5
ROLLING_START = datetime(2020, 1, 1, tzinfo=UTC)
ROLLING_WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1_095))
STEP_DAYS = 30
BOOTSTRAP_BLOCKS = (7, 30, 90)
BOOTSTRAP_SAMPLES = 10_000


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    fast = simple_moving_average(daily, FAST)
    slow = simple_moving_average(daily, SLOW)
    targets = build_targets(daily, ends, len(bars), fast, slow, LOOKBACK)
    full_start = max(ROLLING_START, datetime.fromtimestamp(bars[0].start_ms / 1000, UTC))
    full_end = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC)
    rolling = {
        label: rolling_summary(bars, targets, funding, days, full_start, full_end)
        for label, days in ROLLING_WINDOWS
    }
    full = replay(bars, targets, funding, int(full_start.timestamp() * 1000), bars[-1].end_ms, True)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=int(full_start.timestamp() * 1000)
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=BOOTSTRAP_SAMPLES,
            seed=20260902 + block,
        )
        for block in BOOTSTRAP_BLOCKS
    }
    holdout = replay(bars, targets, funding, HOLDOUT_START, HOLDOUT_END, False)
    holdout_bh = benchmark(bars, HOLDOUT_START, HOLDOUT_END)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": f"daily-sma{FAST}-{SLOW}-sma{SLOW}-slope-{LOOKBACK}d",
        "protocol": {
            "selection": "candidate was fixed before this audit; no OOS or holdout selection",
            "rolling_step_days": STEP_DAYS,
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "hard_leverage": "maximum observed open and intrabar-low futures leverage <=3x",
        },
        "rolling": rolling,
        "bootstrap": bootstrap,
        "holdout_2019": {
            "strategy": as_public(holdout),
            "benchmark": holdout_bh,
            "excess": holdout.net_return - holdout_bh["net_return"],
        },
        "full": as_public(full),
        "conclusion": conclusion(rolling, bootstrap, holdout.net_return - holdout_bh["net_return"]),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def replay(bars, targets, funding, start, end, record_equity):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=SPOT_CAP,
        maintenance_rate=MAINTENANCE,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=MAX_LEVERAGE,
    )


def rolling_summary(bars, targets, funding, window_days, first, last):
    rows = []
    start = first
    while start + timedelta(days=window_days) <= last:
        end = start + timedelta(days=window_days) - timedelta(milliseconds=1)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        result = replay(bars, targets, funding, start_ms, end_ms, False)
        base = benchmark(bars, start_ms, end_ms)
        rows.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "strategy_return": result.net_return,
                "benchmark_return": base["net_return"],
                "excess": result.net_return - base["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": base["max_drawdown"],
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                "liquidated": result.liquidated,
            }
        )
        start += timedelta(days=STEP_DAYS)
    excess = [row["excess"] for row in rows]
    return {
        "summary": {
            "windows": len(rows),
            "return_win_rate": ratio(row["excess"] > 0 for row in rows),
            "return_and_drawdown_win_rate": ratio(
                row["excess"] > 0 and row["strategy_drawdown"] >= row["benchmark_drawdown"]
                for row in rows
            ),
            "median_excess": sorted(excess)[len(excess) // 2] if excess else 0,
            "worst_excess": min(excess) if excess else 0,
            "maximum_intrabar_leverage": max(
                (row["maximum_intrabar_leverage"] for row in rows), default=0
            ),
            "liquidations": sum(row["liquidated"] for row in rows),
        },
        "rows": rows,
    }


def ratio(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def as_public(result):
    value = {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }
    if result.equity_curve:
        value["equity_curve_points"] = len(result.equity_curve)
    return value


def conclusion(rolling, bootstrap, holdout_excess):
    return {
        "all_rolling_return_majorities": all(
            item["summary"]["return_win_rate"] >= 0.5 for item in rolling.values()
        ),
        "bootstrap_95pct_excess_positive": all(
            item["annualized_excess_vs_bh"]["p05"] > 0 for item in bootstrap.values()
        ),
        "positive_after_2019_holdout": holdout_excess > 0,
        "status": "RESEARCH_ONLY",
    }


def render(payload):
    lines = [
        "# BTC Daily SMA40 Slope Gate Audit (Hard 3X)",
        "",
        f"固定候选：`{payload['candidate']}`。本审计不重新选择参数，成本与 Funding 已计入。",
        "",
        "## 滚动窗口",
        "",
        "| 窗口 | 数量 | 超过B&H | 收益+DD胜出 | 中位超额 | 最差超额 | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, item in payload["rolling"].items():
        s = item["summary"]
        lines.append(
            f"| {label} | {s['windows']} | {pct(s['return_win_rate'])} | "
            f"{pct(s['return_and_drawdown_win_rate'])} | {pct(s['median_excess'])} | "
            f"{pct(s['worst_excess'])} | {s['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "## Bootstrap 与留出",
        "",
    ]
    for block, item in payload["bootstrap"].items():
        lines.append(
            f"- {block}：超过 B&H {pct(item['probability_beats_bh_return'])}；"
            f"年化超额 P05 {pct(item['annualized_excess_vs_bh']['p05'])}；"
            f"收益与 DD 同胜 {pct(item['probability_beats_return_and_drawdown'])}。"
        )
    holdout = payload["holdout_2019"]
    lines += [
        f"- 2019 独立留出超额：{pct(holdout['excess'])}。",
        "",
        "结论：Bootstrap 正超额下界和独立留出都必须通过，才会升级；当前状态："
        "**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
