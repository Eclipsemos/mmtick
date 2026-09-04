#!/usr/bin/env python3
"""Statistically audit the fixed BTC bear-flat candidate on stitched daily data."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from audit_btc_spot_pre2020 import load_spot_bars, validate_daily_continuity
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market
from research_btc_stitched_3x import (
    FEE_BPS,
    FUTURES_START_MS,
    LEVERAGE_CAP,
    MAINTENANCE,
    SLIPPAGE_BPS,
    SPOT_CAP,
    build_targets,
)

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_stitched_3x_audit/2026-09-02")
START_MS = int(datetime(2017, 10, 1, tzinfo=UTC).timestamp() * 1000)
STEP_DAYS = 30
WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1_095))
BLOCKS = (7, 30, 90)
SAMPLES = 10_000


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    spot = [bar for bar in load_spot_bars() if bar.end_ms < FUTURES_START_MS]
    validate_daily_continuity(spot)
    futures_15m = load_market("BTCUSDT")
    futures, _ = aggregate_complete_periods(futures_15m, "1d")
    futures = [bar for bar in futures if bar.start_ms >= FUTURES_START_MS]
    combined = spot + futures
    validate_daily_continuity(combined)
    funding_events = load_funding("BTCUSDT", futures_15m)
    funding = [[] for _ in combined]
    funding[len(spot) :] = funding_by_bar(futures, funding_events)
    fast = simple_moving_average(combined, 8)
    slow = simple_moving_average(combined, 40)
    targets = build_targets(combined, fast, slow, mode="flat")
    full = replay(combined, targets, funding, START_MS, combined[-1].end_ms, True)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        combined, full.equity_curve, 100_000.0, start_ms=START_MS
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=SAMPLES,
            seed=20260950 + block,
        )
        for block in BLOCKS
    }
    first = datetime.fromtimestamp(START_MS / 1000, UTC)
    last = datetime.fromtimestamp(combined[-1].end_ms / 1000, UTC)
    rolling = {
        label: rolling_summary(combined, targets, funding, days, first, last)
        for label, days in WINDOWS
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": "daily-sma8-40-bear-flat",
        "protocol": {
            "selection": (
                "candidate fixed before stitched audit; no rolling or bootstrap result "
                "used for selection"
            ),
            "price_history": "Binance spot 2017-08 through 2019-12; Binance USD-M from 2020-01",
            "funding": "none on spot segment; historical USD-M funding from 2020 onward",
            "costs": "10 bps fee + 5 bps slippage",
            "leverage": (
                f"{LEVERAGE_CAP}x order cap with observed effective leverage audited below 3x"
            ),
        },
        "data": {
            "bars": len(combined),
            "spot_bars": len(spot),
            "futures_daily_bars": len(futures),
            "funding_events": len(funding_events),
            "first": iso(combined[0].start_ms),
            "evaluation_start": iso(START_MS),
            "last": iso(combined[-1].end_ms),
        },
        "full": public(full),
        "benchmark": benchmark(combined, START_MS, combined[-1].end_ms),
        "rolling": rolling,
        "bootstrap": bootstrap,
        "conclusion": conclusion(rolling, bootstrap),
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
        maximum_futures_leverage=LEVERAGE_CAP,
    )


def rolling_summary(bars, targets, funding, days, first, last):
    rows = []
    start = first
    while start + timedelta(days=days) <= last:
        end = start + timedelta(days=days) - timedelta(milliseconds=1)
        start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        result = replay(bars, targets, funding, start_ms, end_ms, False)
        bh = benchmark(bars, start_ms, end_ms)
        rows.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "strategy_return": result.net_return,
                "benchmark_return": bh["net_return"],
                "excess": result.net_return - bh["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": bh["max_drawdown"],
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


def public(result):
    return {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
        "equity_curve_points": len(result.equity_curve),
    }


def conclusion(rolling, bootstrap):
    return {
        "rolling_return_majority": all(
            item["summary"]["return_win_rate"] >= 0.5 for item in rolling.values()
        ),
        "bootstrap_p05_positive": all(
            item["annualized_excess_vs_bh"]["p05"] > 0 for item in bootstrap.values()
        ),
        "status": "RESEARCH_ONLY",
    }


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC Stitched Bear-Flat Statistical Audit",
        "",
        (
            f"固定候选：`{payload['candidate']}`。数据跨越 "
            f"{payload['data']['first']} 至 {payload['data']['last']}。"
        ),
        "",
        "## 滚动窗口",
        "",
        "| 窗口 | 数量 | 超过 B&H | 收益+DD胜出 | 中位超额 | 最差超额 | 盘中最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, item in payload["rolling"].items():
        s = item["summary"]
        lines.append(
            f"| {label} | {s['windows']} | {pct(s['return_win_rate'])} | "
            f"{pct(s['return_and_drawdown_win_rate'])} | {pct(s['median_excess'])} | "
            f"{pct(s['worst_excess'])} | {s['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += ["", "## 区块 Bootstrap", ""]
    for label, item in payload["bootstrap"].items():
        lines.append(
            f"- {label}：超过 B&H {pct(item['probability_beats_bh_return'])}；"
            f"收益与 DD 同胜 {pct(item['probability_beats_return_and_drawdown'])}；"
            f"年化超额 P05 {pct(item['annualized_excess_vs_bh']['p05'])}。"
        )
    lines += [
        "",
        "Bootstrap 的 P05 若为负，表示不能排除同样的历史区块顺序导致策略落后 B&H。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
