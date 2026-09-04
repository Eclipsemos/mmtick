#!/usr/bin/env python3
"""Audit the pre-2025-selected BTC SMA10/40 hysteresis candidate at 1.25X."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_sma12_40 as base
from research_btc_collateral_architecture import replay_segregated

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_hysteresis_1p25_strict3x/2026-09-02")
START_MS = base.START_MS
FUTURES_START_MS = base.FUTURES_START_MS
FAST = 10
SLOW = 40
ENTER_BEAR_DAYS = 2
EXIT_BEAR_DAYS = 1
ACTIVE = Decimal("1.25")
SPOT_CAP = Decimal("0.5")
MAX_FUTURES_LEVERAGE = Decimal("3")
MAINTENANCE = Decimal("0.02")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1095))
STEP_DAYS = 30


def main() -> None:
    global FEE_BPS, SLIPPAGE_BPS
    parser = argparse.ArgumentParser()
    parser.add_argument("--fee-bps", type=Decimal, default=FEE_BPS)
    parser.add_argument("--slippage-bps", type=Decimal, default=SLIPPAGE_BPS)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    FEE_BPS = args.fee_bps
    SLIPPAGE_BPS = args.slippage_bps
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    spot = [bar for bar in base.load_spot_bars() if bar.end_ms < FUTURES_START_MS]
    base.validate_daily_continuity(spot)
    futures_15m = base.load_market("BTCUSDT")
    futures, _ = aggregate_complete_periods(futures_15m, "1d")
    futures = [bar for bar in futures if bar.start_ms >= FUTURES_START_MS]
    bars = spot + futures
    base.validate_daily_continuity(bars)
    funding = [[] for _ in bars]
    funding[len(spot) :] = funding_by_bar(futures, base.load_funding("BTCUSDT", futures_15m))
    targets = build_targets(bars)
    periods = period_bounds(bars[-1].end_ms, spot[-1].end_ms)
    full = replay(bars, targets, funding, *periods["stitched_full"], record_equity=True)
    strategy_logs, benchmark_logs = base.paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=periods["stitched_full"][0]
    )
    bootstrap = {
        f"{block}d": base.run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20262500 + block,
        )
        for block in (7, 30, 90)
    }
    segments = {}
    for name, bounds in periods.items():
        result = replay(bars, targets, funding, *bounds)
        benchmark = base.benchmark(bars, *bounds)
        segments[name] = public(result, benchmark)
    rolling = rolling_windows(bars, targets, funding, periods["stitched_full"])
    yearly = yearly_results(bars, targets, funding, periods["stitched_full"])
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": {
            "id": "daily-sma10-40-enter2-exit1-active1.25",
            "fast_sma": FAST,
            "slow_sma": SLOW,
            "enter_bear_after_days": ENTER_BEAR_DAYS,
            "exit_bear_after_days": EXIT_BEAR_DAYS,
            "active_exposure": str(ACTIVE),
            "inactive_exposure": "0",
            "selection": (
                "signal and exposure fixed using only pre-2025 development; among candidates "
                "passing the strict 3X effective-leverage audit, 1.25X had the strongest "
                "worst development-segment excess"
            ),
        },
        "protocol": {
            "data": "Binance spot 2017-2019 stitched to USD-M 2020-latest",
            "signal": "completed daily candle; next bar execution",
            "costs": (
                f"{FEE_BPS:g} bps fee + {SLIPPAGE_BPS:g} bps slippage; "
                "historical Funding on futures segment"
            ),
            "spot_cap": str(SPOT_CAP),
            "maximum_futures_leverage": str(MAX_FUTURES_LEVERAGE),
            "hard_effective_leverage_cap": "3X",
            "maintenance_rate": str(MAINTENANCE),
            "oos": "2025-latest excluded from selection",
        },
        "data": {
            "spot_bars": len(spot),
            "futures_daily_bars": len(futures),
            "combined_bars": len(bars),
            "funding_events": sum(len(items) for items in funding),
            "first": base.iso(bars[0].start_ms),
            "evaluation_start": base.iso(START_MS),
            "last": base.iso(bars[-1].end_ms),
        },
        "periods": {
            name: [base.iso(left), base.iso(right)] for name, (left, right) in periods.items()
        },
        "segments": segments,
        "full": public(full, base.benchmark(bars, *periods["stitched_full"])),
        "rolling": rolling,
        "yearly": yearly,
        "bootstrap": bootstrap,
        "hard_cap_passed": (
            all(item["maximum_intrabar_leverage"] <= 3 for item in segments.values())
            and all(item["summary"]["maximum_intrabar_leverage"] <= 3 for item in rolling.values())
            and not any(item["liquidated"] for item in segments.values())
        ),
        "cagr": {
            "strategy": annualized(full.net_return, periods["stitched_full"]),
            "benchmark": annualized(
                base.benchmark(bars, *periods["stitched_full"])["net_return"],
                periods["stitched_full"],
            ),
        },
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(render(payload), encoding="utf-8")
    print(output_dir / "README.md")


def build_targets(bars):
    fast = simple_moving_average(bars, FAST)
    slow = simple_moving_average(bars, SLOW)
    state = None
    bear_count = 0
    recovery_count = 0
    output = []
    for index, bar in enumerate(bars):
        if fast[index] is None or slow[index] is None:
            output.append(None)
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
        output.append(Decimal("0") if state == "bear" else ACTIVE)
    return tuple(output)


def replay(bars, targets, funding, start_ms, end_ms, record_equity=False):
    return replay_segregated(
        bars,
        targets,
        funding,
        start_ms,
        end_ms,
        spot_cap=SPOT_CAP,
        maintenance_rate=MAINTENANCE,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=MAX_FUTURES_LEVERAGE,
    )


def public(result, benchmark):
    return {
        "strategy_return": result.net_return,
        "benchmark_return": benchmark["net_return"],
        "excess": result.net_return - benchmark["net_return"],
        "strategy_drawdown": result.max_drawdown,
        "benchmark_drawdown": benchmark["max_drawdown"],
        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def rolling_windows(bars, targets, funding, bounds):
    first = datetime.fromtimestamp(bounds[0] / 1000, UTC)
    last = datetime.fromtimestamp(bounds[1] / 1000, UTC)
    output = {}
    for label, days in WINDOWS:
        rows = []
        start = first
        while start + timedelta(days=days) <= last:
            end = start + timedelta(days=days) - timedelta(milliseconds=1)
            left, right = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
            result = replay(bars, targets, funding, left, right)
            benchmark = base.benchmark(bars, left, right)
            rows.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "excess": result.net_return - benchmark["net_return"],
                    "strategy_return": result.net_return,
                    "benchmark_return": benchmark["net_return"],
                    "strategy_drawdown": result.max_drawdown,
                    "benchmark_drawdown": benchmark["max_drawdown"],
                    "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                    "liquidated": result.liquidated,
                }
            )
            start += timedelta(days=STEP_DAYS)
        excess = [row["excess"] for row in rows]
        output[label] = {
            "summary": {
                "windows": len(rows),
                "return_win_rate": ratio(value > 0 for value in excess),
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
    return output


def ratio(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def yearly_results(bars, targets, funding, bounds):
    first_year = datetime.fromtimestamp(bounds[0] / 1000, UTC).year
    last_year = datetime.fromtimestamp(bounds[1] / 1000, UTC).year
    rows = []
    for year in range(first_year, last_year + 1):
        left = max(bounds[0], int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000))
        right = min(bounds[1], int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1)
        result = replay(bars, targets, funding, left, right)
        benchmark = base.benchmark(bars, left, right)
        rows.append(
            {
                "year": year,
                "strategy_return": result.net_return,
                "benchmark_return": benchmark["net_return"],
                "excess": result.net_return - benchmark["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": benchmark["max_drawdown"],
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                "liquidated": result.liquidated,
            }
        )
    return rows


def period_bounds(last_end, spot_end):
    return {
        "spot_pre2020": (START_MS, spot_end),
        "2020_2022": (
            FUTURES_START_MS,
            int(datetime(2022, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
        ),
        "2023_2024": (
            int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2024, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
        ),
        "2025_latest": (int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000), last_end),
        "stitched_full": (START_MS, last_end),
    }


def annualized(net_return, bounds):
    years = (bounds[1] - bounds[0]) / (365.2425 * 86_400_000)
    return (1 + net_return) ** (1 / years) - 1


def pct(value):
    return f"{value:.2%}"


def render(payload):
    full = payload["segments"]["stitched_full"]
    lines = [
        "# BTC SMA10/40 Hysteresis 1.25X Audit (Strict 3X)",
        "",
        (
            "参数和暴露在 2025 年之前固定；2025–最新只读验证。"
            "连续 2 根 bearish 日线降为 0X，连续 1 根恢复至 1.25X。"
        ),
        "",
        "| 区间 | 策略 | B&H | 超额 | 策略DD | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["segments"].items():
        lines.append(
            f"| {name} | {pct(row['strategy_return'])} | {pct(row['benchmark_return'])} | "
            f"{pct(row['excess'])} | {pct(row['strategy_drawdown'])} | "
            f"{row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        (
            f"Full CAGR：{pct(payload['cagr']['strategy'])}；"
            f"B&H CAGR：{pct(payload['cagr']['benchmark'])}。"
        ),
        (
            f"Full 最大回撤：{pct(full['strategy_drawdown'])}；"
            f"硬杠杆约束：{'通过' if payload['hard_cap_passed'] else '失败'}。"
        ),
        "",
        "## Rolling Windows",
        "",
        "| 窗口 | 数量 | 超过 B&H | 收益+DD 同胜 | 中位超额 | 最差超额 | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, item in payload["rolling"].items():
        summary = item["summary"]
        lines.append(
            f"| {label} | {summary['windows']} | {pct(summary['return_win_rate'])} | "
            f"{pct(summary['return_and_drawdown_win_rate'])} | {pct(summary['median_excess'])} | "
            f"{pct(summary['worst_excess'])} | {summary['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "## Yearly",
        "",
        "| 年份 | 策略 | B&H | 超额 | 策略DD |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in payload["yearly"]:
        lines.append(
            f"| {row['year']} | {pct(row['strategy_return'])} | "
            f"{pct(row['benchmark_return'])} | {pct(row['excess'])} | "
            f"{pct(row['strategy_drawdown'])} |"
        )
    lines += ["", "## Bootstrap", ""]
    for label, item in payload["bootstrap"].items():
        lines.append(
            f"- {label}: 超过 B&H {pct(item['probability_beats_bh_return'])}；"
            f"年化超额 P05 {pct(item['annualized_excess_vs_bh']['p05'])}；"
            f"收益+DD 同胜 {pct(item['probability_beats_return_and_drawdown'])}。"
        )
    lines += [
        "",
        (
            "结论：该配置在严格 3X 有效杠杆下具备历史超额和较低回撤，"
            "但仍需更长的独立前向样本；不能据此批准实盘。"
        ),
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
