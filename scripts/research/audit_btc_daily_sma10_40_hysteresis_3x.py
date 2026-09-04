#!/usr/bin/env python3
"""Audit fixed daily SMA10/40 bear-flat hysteresis BTC exposure."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_dynamic_exposure import benchmark, replay_dynamic_incremental
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma10_40_hysteresis_3x/2026-09-02")
START = datetime(2020, 1, 1, tzinfo=UTC)
FAST = 10
SLOW = 40
ENTER_BEAR_DAYS = 2
EXIT_BEAR_DAYS = 1
ACTIVE = Decimal("1.5")
INACTIVE = Decimal("0")
WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1_095))
STEP_DAYS = 30
BLOCKS = (7, 30, 90)
SAMPLES = 10_000
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    targets = build_targets(bars)
    start_ms = int(START.timestamp() * 1000)
    end_ms = bars[-1].end_ms
    full = replay(bars, targets, funding, start_ms, end_ms, record=True)
    bh = benchmark(bars, start_ms, end_ms)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=start_ms
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=SAMPLES,
            seed=20261700 + block,
        )
        for block in BLOCKS
    }
    rolling = {
        label: rolling_summary(bars, targets, funding, days, end_ms) for label, days in WINDOWS
    }
    splits = split_summary(bars, targets, funding, start_ms, end_ms)
    years = (end_ms - start_ms) / (365.2425 * 86_400_000)
    full_metrics = public(full, years)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "timeframe": "1d",
            "fast_sma": FAST,
            "slow_sma": SLOW,
            "enter_bear_after_days": ENTER_BEAR_DAYS,
            "exit_bear_after_days": EXIT_BEAR_DAYS,
            "active_exposure": str(ACTIVE),
            "inactive_exposure": str(INACTIVE),
            "rule": (
                "bear condition is close < SMA40 and SMA10 < SMA40; "
                "state changes only after confirmation days"
            ),
        },
        "protocol": {
            "selection": "fixed before this audit; no OOS or rolling-window selection",
            "signal": "completed daily candle; next 15m open target change",
            "costs": "10 bps fee + 5 bps slippage; historical funding above 1x",
            "leverage": "1x spot cap and 0.5x futures overlay; effective leverage audited",
            "future_data": "no future bars or funding events used",
        },
        "data": {
            "bars": len(bars),
            "funding_events": len(funding),
            "evaluation_start": START.isoformat(),
            "last": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        },
        "full": full_metrics,
        "benchmark": bh,
        "splits": splits,
        "rolling": rolling,
        "bootstrap": bootstrap,
        "conclusion": {
            "beats_bh_full": full.net_return > bh["net_return"],
            "beats_bh_all_aggregate_splits": all(row["excess"] > 0 for row in splits.values()),
            "effective_leverage_below_3x": (
                full_metrics["maximum_effective_futures_leverage"] <= 3
            ),
            "bootstrap_p05_positive": all(
                item["annualized_excess_vs_bh"]["p05"] > 0 for item in bootstrap.values()
            ),
            "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def build_targets(bars):
    aggregate, ends = aggregate_complete_periods(bars, "1d")
    fast = simple_moving_average(aggregate, FAST)
    slow = simple_moving_average(aggregate, SLOW)
    state = None
    bear_count = 0
    good_count = 0
    sparse = []
    for index, bar in enumerate(aggregate):
        if fast[index] is None or slow[index] is None:
            sparse.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        good_count = good_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= ENTER_BEAR_DAYS:
            state = "bear"
        elif state == "bear" and good_count >= EXIT_BEAR_DAYS:
            state = "active"
        sparse.append(INACTIVE if state == "bear" else ACTIVE)
    return map_targets_to_source(len(bars), tuple(sparse), ends)


def replay(bars, targets, funding, start_ms, end_ms, *, record=False):
    return replay_dynamic_incremental(
        bars,
        targets,
        funding,
        start_ms,
        end_ms,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        funding_on_excess_only=True,
        spot_exposure_cap=Decimal("1"),
        record_equity=record,
        record_risk=record,
        record_exposure=record,
    )


def effective_leverage(result):
    return max(
        (
            futures_value / low_equity
            for _timestamp, low_equity, futures_value, _total, _exposure in result.risk_curve
            if low_equity > 0
        ),
        default=0.0,
    )


def public(result, years):
    return {
        "net_return": result.net_return,
        "cagr": (1 + result.net_return) ** (1 / years) - 1,
        "max_drawdown": result.max_drawdown,
        "maximum_target_exposure": max(
            (float(item[1]) for item in result.exposure_curve),
            default=0.0,
        ),
        "maximum_effective_futures_leverage": effective_leverage(result),
        "rebalances": result.completed_trades,
        "fees": result.total_fees,
        "funding": result.total_funding,
        "liquidated": result.bankrupt,
    }


def rolling_summary(bars, targets, funding, days, end_ms):
    rows = []
    start = START
    last = datetime.fromtimestamp(end_ms / 1000, UTC)
    while start + timedelta(days=days) <= last:
        stop = start + timedelta(days=days) - timedelta(milliseconds=1)
        left, right = int(start.timestamp() * 1000), int(stop.timestamp() * 1000)
        result = replay(bars, targets, funding, left, right)
        bh = benchmark(bars, left, right)
        rows.append(
            {
                "start": start.isoformat(),
                "end": stop.isoformat(),
                "strategy_return": result.net_return,
                "benchmark_return": bh["net_return"],
                "excess": result.net_return - bh["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": bh["max_drawdown"],
                "beats_return": result.net_return > bh["net_return"],
                "beats_return_and_drawdown": (
                    result.net_return > bh["net_return"]
                    and result.max_drawdown >= bh["max_drawdown"]
                ),
            }
        )
        start += timedelta(days=STEP_DAYS)
    excess = [row["excess"] for row in rows]
    return {
        "summary": {
            "windows": len(rows),
            "return_win_rate": ratio(row["beats_return"] for row in rows),
            "return_and_drawdown_win_rate": ratio(row["beats_return_and_drawdown"] for row in rows),
            "median_excess": sorted(excess)[len(excess) // 2] if excess else 0.0,
            "worst_excess": min(excess) if excess else 0.0,
        },
        "rows": rows,
    }


def split_summary(bars, targets, funding, start_ms, end_ms):
    boundaries = {
        "research": (
            start_ms,
            min(
                end_ms,
                int(datetime(2022, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
            ),
        ),
        "validation": (
            int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000),
            min(
                end_ms,
                int(datetime(2024, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
            ),
        ),
        "oos": (int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000), end_ms),
        "full": (start_ms, end_ms),
    }
    output = {}
    for label, (left, right) in boundaries.items():
        result = replay(bars, targets, funding, left, right)
        bh = benchmark(bars, left, right)
        output[label] = {
            "strategy_return": result.net_return,
            "benchmark_return": bh["net_return"],
            "excess": result.net_return - bh["net_return"],
            "strategy_drawdown": result.max_drawdown,
            "benchmark_drawdown": bh["max_drawdown"],
        }
    return output


def ratio(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def pct(value):
    return f"{value:.2%}"


def cagr_bh(payload):
    last = datetime.fromisoformat(payload["data"]["last"])
    years = (last - START).total_seconds() / (365.2425 * 86_400)
    return (1 + payload["benchmark"]["net_return"]) ** (1 / years) - 1


def render(payload):
    full = payload["full"]
    bh = payload["benchmark"]
    lines = [
        "# BTC Daily SMA10/40 Hysteresis Audit (Hard 3X)",
        "",
        "固定日线 SMA10/40；进入熊市需连续 2 根 bearish 日线，恢复需 1 根 non-bearish 日线。",
        "压力成本为 10 bps 手续费、5 bps 滑点及历史 Funding。",
        "",
        "## Full 2020–最新",
        "",
        "| 指标 | 策略 | B&H |",
        "|---|---:|---:|",
        f"| 收益 | {pct(full['net_return'])} | {pct(bh['net_return'])} |",
        f"| CAGR | {pct(full['cagr'])} | {pct(cagr_bh(payload))} |",
        f"| 最大回撤 | {pct(full['max_drawdown'])} | {pct(bh['max_drawdown'])} |",
        f"| 最高有效 Futures 杠杆 | {full['maximum_effective_futures_leverage']:.3f}X | - |",
        "",
        "## Aggregate Splits",
        "",
        "| 区间 | 策略 | B&H | 超额 |",
        "|---|---:|---:|---:|",
    ]
    for label in ("research", "validation", "oos", "full"):
        row = payload["splits"][label]
        lines.append(
            f"| {label} | {pct(row['strategy_return'])} | {pct(row['benchmark_return'])} | "
            f"{pct(row['excess'])} |"
        )
    lines += [
        "",
        "## Rolling Windows",
        "",
        "| 窗口 | 数量 | 超过 B&H | 收益+DD 同胜 | 中位超额 | 最差超额 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, item in payload["rolling"].items():
        summary = item["summary"]
        lines.append(
            f"| {label} | {summary['windows']} | {pct(summary['return_win_rate'])} | "
            f"{pct(summary['return_and_drawdown_win_rate'])} | {pct(summary['median_excess'])} | "
            f"{pct(summary['worst_excess'])} |"
        )
    lines += ["", "## Bootstrap", ""]
    for block, item in payload["bootstrap"].items():
        lines.append(
            f"- {block}: beat B&H {pct(item['probability_beats_bh_return'])}; "
            f"joint return+DD {pct(item['probability_beats_return_and_drawdown'])}; "
            f"annualized excess P05 {pct(item['annualized_excess_vs_bh']['p05'])}."
        )
    lines += [
        "",
        (
            "结论：历史收益与回撤表现较强，但 Bootstrap 及未见前向数据仍是必要验证；"
            "状态为 **RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。"
        ),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
