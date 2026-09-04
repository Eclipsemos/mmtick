#!/usr/bin/env python3
"""Research a fixed blend of two independently defined BTC trend sleeves."""

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

OUTPUT_DIR = Path("reports/experiments/btc_hysteresis_fast_blend/2026-09-02")
START_MS = base.START_MS
FUTURES_START_MS = base.FUTURES_START_MS
SLOW = 40
HYST_FAST = 10
HYST_ENTER = 2
HYST_EXIT = 1
HYST_ACTIVE = Decimal("1.25")
FAST_COMPONENTS = (8, 12, 15)
FAST_BULL = Decimal("1.5")
FAST_BEAR = Decimal("-0.1")
WEIGHTS = tuple(Decimal(value) for value in ("0", "0.25", "0.5", "0.75", "1"))
SPOT_CAP = Decimal("0.5")
MAX_FUTURES_LEVERAGE = Decimal("3")
MAINTENANCE = Decimal("0.02")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")


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
    hyst = hysteresis_targets(bars)
    fast = fast_targets(bars)
    rows = []
    for fast_weight in WEIGHTS:
        targets = tuple(
            None
            if left is None or right is None
            else (Decimal("1") - fast_weight) * left + fast_weight * right
            for left, right in zip(hyst, fast, strict=True)
        )
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
        development = min(
            metrics[name]["excess"] for name in ("spot_pre2020", "2020_2022", "2023_2024")
        )
        rows.append(
            {
                "fast_weight": str(fast_weight),
                "development_score": development,
                "metrics": metrics,
            }
        )
    rows.sort(key=lambda row: row["development_score"], reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": {
            "hysteresis_sleeve": "SMA10/40 enter bear after 2 days, exit after 1; active 1.25X",
            "fast_sleeve": "equal SMA8/40, SMA12/40, SMA15/40; bull 1.5X, bear -0.1X",
            "selection": (
                "fixed blend weights ranked by worst pre-2025 segment excess; OOS excluded"
            ),
        },
        "protocol": {
            "data": "Binance spot 2017-2019 stitched to USD-M 2020-latest",
            "signal": "completed daily candle; next bar execution",
            "costs": "10 bps fee + 5 bps slippage; historical Funding on futures segment",
            "hard_effective_leverage_cap": "3X",
        },
        "data": {
            "spot_bars": len(spot),
            "futures_daily_bars": len(futures),
            "combined_bars": len(bars),
            "last": base.iso(bars[-1].end_ms),
        },
        "periods": {
            name: [base.iso(left), base.iso(right)] for name, (left, right) in periods.items()
        },
        "candidate_count": len(rows),
        "selected": rows[0],
        "rows": rows,
        "hard_cap_passed": all(
            row["metrics"]["stitched_full"]["maximum_intrabar_leverage"] <= 3
            and not row["metrics"]["stitched_full"]["liquidated"]
            for row in rows
        ),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def hysteresis_targets(bars):
    fast = simple_moving_average(bars, HYST_FAST)
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
        elif state == "active" and bear_count >= HYST_ENTER:
            state = "bear"
        elif state == "bear" and recovery_count >= HYST_EXIT:
            state = "active"
        output.append(Decimal("0") if state == "bear" else HYST_ACTIVE)
    return tuple(output)


def fast_targets(bars):
    streams = []
    for fast in FAST_COMPONENTS:
        fast_sma = simple_moving_average(bars, fast)
        slow_sma = simple_moving_average(bars, SLOW)
        streams.append(
            tuple(
                None
                if fast_sma[index] is None or slow_sma[index] is None
                else FAST_BEAR
                if bar.close < slow_sma[index] and fast_sma[index] < slow_sma[index]
                else FAST_BULL
                for index, bar in enumerate(bars)
            )
        )
    return tuple(
        None if any(value is None for value in values) else sum(values, Decimal("0")) / 3
        for values in zip(*streams, strict=True)
    )


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
        "# BTC SMA Hysteresis + Fast Ensemble Blend (Hard 3X)",
        "",
        "固定混合两个独立策略 sleeve；权重仅按 2025 年前开发段的最差超额排序。",
        "",
        "| Fast sleeve权重 | 开发最差超额 | 2025+超额 | Full超额 | Full DD | 最高盘中杠杆 |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        full = row["metrics"]["stitched_full"]
        oos = row["metrics"]["2025_latest"]
        lines.append(
            f"| {row['fast_weight']} | {pct(row['development_score'])} | "
            f"{pct(oos['excess'])} | {pct(full['excess'])} | "
            f"{pct(full['strategy_drawdown'])} | {full['maximum_intrabar_leverage']:.3f}X |"
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
