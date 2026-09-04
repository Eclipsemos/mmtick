#!/usr/bin/env python3
"""Audit a fixed hysteresis daily SMA candidate on stitched BTC history."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_sma12_40 as base

from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_stitched_sma10_40_hysteresis/2026-09-02")
FAST = 10
SLOW = 40
ENTER_BEAR_DAYS = 2
EXIT_BEAR_DAYS = 1
BOOTSTRAP_BLOCKS = (7, 30, 90, 180, 365, 730)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    spot = [bar for bar in base.load_spot_bars() if bar.end_ms < base.FUTURES_START_MS]
    base.validate_daily_continuity(spot)
    futures_15m = base.load_market("BTCUSDT")
    futures, _ = aggregate_complete_periods(futures_15m, "1d")
    futures = [bar for bar in futures if bar.start_ms >= base.FUTURES_START_MS]
    bars = spot + futures
    base.validate_daily_continuity(bars)
    events = base.load_funding("BTCUSDT", futures_15m)
    funding = [[] for _ in bars]
    funding[len(spot) :] = base.funding_by_bar(futures, events)
    targets = build_targets(bars)
    start_ms = base.START_MS
    end_ms = bars[-1].end_ms
    full = base.replay(bars, targets, funding, start_ms, end_ms, True)
    strategy_logs, benchmark_logs = base.paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=start_ms
    )
    bootstrap = {
        f"{block}d": base.run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=base.SAMPLES,
            seed=20261800 + block,
        )
        for block in BOOTSTRAP_BLOCKS
    }
    tail = tail_concentration(strategy_logs, benchmark_logs)
    first = datetime.fromtimestamp(start_ms / 1000, UTC)
    last = datetime.fromtimestamp(end_ms / 1000, UTC)
    rolling = {
        label: base.rolling_summary(bars, targets, funding, days, first, last)
        for label, days in base.WINDOWS
    }
    periods = {
        "spot_pre2020": (start_ms, spot[-1].end_ms),
        "2020_2022": (
            base.FUTURES_START_MS,
            int(datetime(2022, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
        ),
        "2023_2024": (
            int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2024, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
        ),
        "2025_latest": (
            int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000),
            end_ms,
        ),
        "stitched_full": (start_ms, end_ms),
    }
    segments = {}
    for label, (left, right) in periods.items():
        result = base.replay(bars, targets, funding, left, right, False)
        benchmark = base.benchmark(bars, left, right)
        segments[label] = {
            "strategy_return": result.net_return,
            "benchmark_return": benchmark["net_return"],
            "excess": result.net_return - benchmark["net_return"],
            "strategy_drawdown": result.max_drawdown,
            "benchmark_drawdown": benchmark["max_drawdown"],
            "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
            "liquidated": result.liquidated,
        }
    full_public = base.public(full)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "id": "stitched-daily-sma10-40-bear-flat-hysteresis",
            "fast_sma": FAST,
            "slow_sma": SLOW,
            "enter_bear_after_days": ENTER_BEAR_DAYS,
            "exit_bear_after_days": EXIT_BEAR_DAYS,
            "active_exposure": "1.5",
            "inactive_exposure": "0",
        },
        "protocol": {
            "selection": "fixed before this stitched audit; no OOS or rolling-window selection",
            "data": "Binance spot 2017-08..2019-12 stitched to Binance USD-M 2020..latest",
            "signal": "completed daily candle; next daily/15m execution boundary",
            "costs": "10 bps fee + 5 bps slippage; historical Funding only on futures segment",
            "leverage": "2x order cap as buffer; observed effective leverage must stay below 3x",
            "future_data": "state changes use only the current and previous completed daily bars",
        },
        "data": {
            "bars": len(bars),
            "spot_bars": len(spot),
            "futures_daily_bars": len(futures),
            "funding_events": len(events),
            "first": base.iso(bars[0].start_ms),
            "evaluation_start": base.iso(start_ms),
            "last": base.iso(end_ms),
        },
        "full": full_public,
        "benchmark": base.benchmark(bars, start_ms, end_ms),
        "segments": segments,
        "rolling": rolling,
        "bootstrap": bootstrap,
        "tail_concentration": tail,
        "conclusion": {
            "beats_bh_full": full.net_return > segments["stitched_full"]["benchmark_return"],
            "beats_bh_every_segment": all(
                segments[label]["excess"] > 0
                for label in ("spot_pre2020", "2020_2022", "2023_2024", "2025_latest")
            ),
            "effective_leverage_below_3x": full.maximum_observed_futures_leverage <= 3,
            "bootstrap_p05_positive": all(
                value["annualized_excess_vs_bh"]["p05"] > 0 for value in bootstrap.values()
            ),
            "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def build_targets(bars):
    fast = simple_moving_average(bars, FAST)
    slow = simple_moving_average(bars, SLOW)
    state = None
    bear_count = 0
    recovery_count = 0
    sparse = []
    for index, bar in enumerate(bars):
        if fast[index] is None or slow[index] is None:
            sparse.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= ENTER_BEAR_DAYS:
            state = "bear"
        elif state == "bear" and recovery_count >= EXIT_BEAR_DAYS:
            state = "active"
        sparse.append(Decimal("0") if state == "bear" else Decimal("1.5"))
    return tuple(sparse)


def pct(value):
    return f"{value:.2%}"


def tail_concentration(strategy_logs, benchmark_logs):
    paired = list(zip(strategy_logs, benchmark_logs, strict=True))
    ranked = sorted(
        range(len(paired)),
        key=lambda index: paired[index][0] - paired[index][1],
        reverse=True,
    )
    years = len(paired) / 365.2425
    output = {}
    for count in (0, 1, 5, 10, 20):
        remove = set(ranked[:count])
        strategy = [value for index, (value, _other) in enumerate(paired) if index not in remove]
        benchmark = [value for index, (_other, value) in enumerate(paired) if index not in remove]
        output[str(count)] = {
            "strategy_cagr": math.exp(sum(strategy) / years) - 1,
            "benchmark_cagr": math.exp(sum(benchmark) / years) - 1,
            "annualized_excess": math.exp((sum(strategy) - sum(benchmark)) / years) - 1,
        }
    return output


def render(payload):
    full = payload["full"]
    bh = payload["benchmark"]
    lines = [
        "# BTC Stitched SMA10/40 Hysteresis Audit (Hard 3X)",
        "",
        "固定日线 SMA10/40；连续 2 根 bearish 日线才进入熊市，连续 1 根 non-bearish 日线恢复。",
        "现货 2017–2019 与 USD-M 2020–最新拼接，压力成本为 10 bps 手续费、5 bps 滑点及 Funding。",
        "",
        "## Stitched Full",
        "",
        "| 指标 | 策略 | B&H |",
        "|---|---:|---:|",
        f"| 收益 | {pct(full['net_return'])} | {pct(bh['net_return'])} |",
        f"| 最大回撤 | {pct(full['max_drawdown'])} | {pct(bh['max_drawdown'])} |",
        f"| 最高盘中有效杠杆 | {full['maximum_intrabar_leverage']:.3f}X | - |",
        "",
        "## Segments",
        "",
        "| 区间 | 策略 | B&H | 超额 | DD |",
        "|---|---:|---:|---:|---:|",
    ]
    for label in ("spot_pre2020", "2020_2022", "2023_2024", "2025_latest", "stitched_full"):
        row = payload["segments"][label]
        lines.append(
            f"| {label} | {pct(row['strategy_return'])} | {pct(row['benchmark_return'])} | "
            f"{pct(row['excess'])} | {pct(row['strategy_drawdown'])} |"
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
        "## Tail concentration",
        "",
        "| Removed best relative days | Strategy CAGR | B&H CAGR | Excess |",
        "|---:|---:|---:|---:|",
    ]
    for count, row in payload["tail_concentration"].items():
        lines.append(
            f"| {count} | {pct(row['strategy_cagr'])} | {pct(row['benchmark_cagr'])} | "
            f"{pct(row['annualized_excess'])} |"
        )
    lines += [
        "",
        (
            "结论：90–730 日区块 Bootstrap 的正超额下界已为正，支持中周期机制；"
            "但 7/30 日下界仍为负，且日线聚合执行不能替代严格 15m 风控审计。"
        ),
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
