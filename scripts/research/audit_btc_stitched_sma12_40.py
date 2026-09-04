#!/usr/bin/env python3
"""Audit the frozen daily SMA12/40 bear-flat candidate on stitched BTC data."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from audit_btc_spot_pre2020 import load_spot_bars, validate_daily_continuity
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_stitched_sma12_40_audit/2026-09-02")
START_MS = int(datetime(2017, 10, 1, tzinfo=UTC).timestamp() * 1000)
FUTURES_START_MS = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
FAST = 12
SLOW = 40
SPOT_CAP = Decimal("0.5")
LEVERAGE_CAP = Decimal("2")
MAINTENANCE = Decimal("0.02")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1_095))
STEP_DAYS = 30
BLOCKS = (7, 30, 90)
SAMPLES = 10_000


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    spot = [bar for bar in load_spot_bars() if bar.end_ms < FUTURES_START_MS]
    validate_daily_continuity(spot)
    futures_15m = load_market("BTCUSDT")
    futures, _ = aggregate_complete_periods(futures_15m, "1d")
    futures = [bar for bar in futures if bar.start_ms >= FUTURES_START_MS]
    bars = spot + futures
    validate_daily_continuity(bars)
    events = load_funding("BTCUSDT", futures_15m)
    funding = [[] for _ in bars]
    funding[len(spot) :] = funding_by_bar(futures, events)
    fast = simple_moving_average(bars, FAST)
    slow = simple_moving_average(bars, SLOW)
    targets = build_targets(bars, fast, slow)
    full = replay(bars, targets, funding, START_MS, bars[-1].end_ms, True)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=START_MS
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=SAMPLES,
            seed=20261200 + block,
        )
        for block in BLOCKS
    }
    first = datetime.fromtimestamp(START_MS / 1000, UTC)
    last = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC)
    rolling = {
        label: rolling_summary(bars, targets, funding, days, first, last) for label, days in WINDOWS
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": "daily-sma12-40-bear-flat",
        "protocol": {
            "selection": (
                "SMA12/40 was fixed from the bounded grid using only 2017-2024; 2025+ was read-only"
            ),
            "costs": "10 bps fee + 5 bps slippage",
            "funding": "USD-M funding from 2020 onward; none on spot segment",
            "leverage": "2x order buffer; observed effective leverage must remain <=3x",
        },
        "data": {
            "bars": len(bars),
            "spot_bars": len(spot),
            "futures_daily_bars": len(futures),
            "funding_events": len(events),
            "first": iso(bars[0].start_ms),
            "evaluation_start": iso(START_MS),
            "last": iso(bars[-1].end_ms),
        },
        "full": public(full),
        "benchmark": benchmark(bars, START_MS, bars[-1].end_ms),
        "rolling": rolling,
        "bootstrap": bootstrap,
        "conclusion": {
            "rolling_return_majority": all(
                item["summary"]["return_win_rate"] >= 0.5 for item in rolling.values()
            ),
            "bootstrap_p05_positive": all(
                item["annualized_excess_vs_bh"]["p05"] > 0 for item in bootstrap.values()
            ),
            "status": "RESEARCH_ONLY",
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def build_targets(bars, fast, slow):
    output = []
    for index, bar in enumerate(bars):
        if fast[index] is None or slow[index] is None:
            output.append(None)
        elif bar.close < slow[index] and fast[index] < slow[index]:
            output.append(Decimal("0"))
        else:
            output.append(Decimal("1.5"))
    return tuple(output)


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


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC SMA12/40 Bear-Flat Audit (Hard 3X)",
        "",
        "参数在 2017–2024 网格阶段固定；2025–最新只读。所有成本与 Funding 均按协议计入。",
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
    lines += ["", "## Bootstrap", ""]
    for label, item in payload["bootstrap"].items():
        lines.append(
            f"- {label}：超过 B&H {pct(item['probability_beats_bh_return'])}；"
            f"收益与 DD 同胜 {pct(item['probability_beats_return_and_drawdown'])}；"
            f"年化超额 P05 {pct(item['annualized_excess_vs_bh']['p05'])}。"
        )
    lines += [
        "",
        (
            f"Full 策略收益 {pct(payload['full']['net_return'])}，"
            f"B&H {pct(payload['benchmark']['net_return'])}，"
            f"最大回撤 {pct(payload['full']['max_drawdown'])}；"
            f"最高盘中杠杆 {payload['full']['maximum_intrabar_leverage']:.3f}X。"
        ),
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
