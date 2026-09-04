#!/usr/bin/env python3
"""Test causal higher-timeframe filters for the fixed BTC SMA10/40 hysteresis signal."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_sma12_40 as base
from research_btc_collateral_architecture import replay_segregated

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_hysteresis_bear_filter/2026-09-02")
START_MS = base.START_MS
FUTURES_START_MS = base.FUTURES_START_MS
FAST = 10
SLOW = 40
LONG_FILTER = 100
SLOPE_DAYS = 5
ENTER_BEAR_DAYS = 2
EXIT_BEAR_DAYS = 1
ACTIVE = Decimal("1.25")
SPOT_CAP = Decimal("0.5")
MAX_FUTURES_LEVERAGE = Decimal("3")
MAINTENANCE = Decimal("0.02")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
VARIANTS = ("none", "slope", "sma100", "both")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    spot = [bar for bar in base.load_spot_bars() if bar.end_ms < FUTURES_START_MS]
    base.validate_daily_continuity(spot)
    futures_15m = base.load_market("BTCUSDT")
    futures, _ = aggregate_complete_periods(futures_15m, "1d")
    futures = [bar for bar in futures if bar.start_ms >= FUTURES_START_MS]
    bars = spot + futures
    base.validate_daily_continuity(bars)
    funding = [[] for _ in bars]
    funding[len(spot) :] = funding_by_bar(futures, base.load_funding("BTCUSDT", futures_15m))
    periods = period_bounds(bars[-1].end_ms, spot[-1].end_ms)
    rows = []
    for variant in VARIANTS:
        targets = build_targets(bars, variant)
        metrics = {}
        for name, bounds in periods.items():
            result = replay(bars, targets, funding, *bounds)
            benchmark = base.benchmark(bars, *bounds)
            metrics[name] = {
                "strategy_return": result.net_return,
                "benchmark_return": benchmark["net_return"],
                "excess": result.net_return - benchmark["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": benchmark["max_drawdown"],
                "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                "liquidated": result.liquidated,
            }
        rows.append({"variant": variant, "metrics": metrics})
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": {
            "base_signal": "SMA10/40 hysteresis, enter after 2 bearish days, exit after 1",
            "active_exposure": str(ACTIVE),
            "filters": {
                "slope": f"SMA40[t] < SMA40[t-{SLOPE_DAYS}]",
                "sma100": "close < SMA100",
                "both": "slope and SMA100 condition",
            },
            "selection": "predefined causal variants; no OOS selection",
        },
        "protocol": {
            "data": "Binance spot 2017-2019 stitched to USD-M 2020-latest",
            "signal": "completed daily candle; next bar execution",
            "costs": "10 bps fee + 5 bps slippage; historical Funding on futures segment",
            "hard_effective_leverage_cap": "3X",
        },
        "data": {"combined_bars": len(bars), "last": base.iso(bars[-1].end_ms)},
        "periods": {
            name: [base.iso(left), base.iso(right)] for name, (left, right) in periods.items()
        },
        "rows": rows,
        "hard_cap_passed": all(
            item["metrics"]["stitched_full"]["maximum_intrabar_leverage"] <= 3
            and not item["metrics"]["stitched_full"]["liquidated"]
            for item in rows
        ),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_targets(bars, variant):
    fast = simple_moving_average(bars, FAST)
    slow = simple_moving_average(bars, SLOW)
    long_sma = simple_moving_average(bars, LONG_FILTER)
    state = None
    bear_count = 0
    recovery_count = 0
    output = []
    for index, bar in enumerate(bars):
        if fast[index] is None or slow[index] is None:
            output.append(None)
            continue
        basic = bar.close < slow[index] and fast[index] < slow[index]
        slope = (
            index >= SLOPE_DAYS
            and slow[index - SLOPE_DAYS] is not None
            and slow[index] < slow[index - SLOPE_DAYS]
        )
        sma100 = long_sma[index] is not None and bar.close < long_sma[index]
        filter_ok = {
            "none": True,
            "slope": slope,
            "sma100": sma100,
            "both": slope and sma100,
        }[variant]
        bearish = basic and filter_ok
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


def replay(bars, targets, funding, start_ms, end_ms):
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
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=MAX_FUTURES_LEVERAGE,
    )


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


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC SMA10/40 Hysteresis Bear Filter (Hard 3X)",
        "",
        "比较基础熊市条件、SMA40 斜率过滤、SMA100 价格过滤及二者组合。",
        "",
        "| 变体 | Full收益 | Full超额 | Full DD | 2023-24超额 | 2025+超额 | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        full = row["metrics"]["stitched_full"]
        v = row["metrics"]["2023_2024"]
        oos = row["metrics"]["2025_latest"]
        lines.append(
            f"| {row['variant']} | {pct(full['strategy_return'])} | {pct(full['excess'])} | "
            f"{pct(full['strategy_drawdown'])} | {pct(v['excess'])} | {pct(oos['excess'])} | "
            f"{full['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        f"硬杠杆约束：{'通过' if payload['hard_cap_passed'] else '失败'}。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
