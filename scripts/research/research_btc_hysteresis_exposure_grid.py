#!/usr/bin/env python3
"""Evaluate fixed SMA10/40 hysteresis at different exposures under a hard 3X cap."""

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

OUTPUT_DIR = Path("reports/experiments/btc_hysteresis_exposure_grid/2026-09-02")
START_MS = base.START_MS
FUTURES_START_MS = base.FUTURES_START_MS
FAST = 10
SLOW = 40
ENTER_BEAR_DAYS = 2
EXIT_BEAR_DAYS = 1
EXPOSURES = tuple(Decimal(value) for value in ("0.75", "1.0", "1.25", "1.5", "1.75", "2.0"))
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
    rows = []
    for exposure in EXPOSURES:
        targets = build_targets(bars, exposure)
        metrics = {}
        full_result = replay(bars, targets, funding, *periods["stitched_full"], record_equity=True)
        strategy_logs, benchmark_logs = base.paired_daily_log_returns(
            bars,
            full_result.equity_curve,
            100_000.0,
            start_ms=periods["stitched_full"][0],
        )
        bootstrap = {
            f"{block}d": base.run_bootstrap(
                strategy_logs,
                benchmark_logs,
                block_days=block,
                samples=10_000,
                seed=20262000 + int(exposure * 100) + block,
            )
            for block in (30, 90)
        }
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
                "fees": result.total_fees,
                "funding": result.total_funding,
            }
        rows.append({"active_exposure": str(exposure), "metrics": metrics, "bootstrap": bootstrap})
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": {
            "fast_sma": FAST,
            "slow_sma": SLOW,
            "enter_bear_after_days": ENTER_BEAR_DAYS,
            "exit_bear_after_days": EXIT_BEAR_DAYS,
            "inactive_exposure": "0",
            "selection": "signal fixed before this exposure-only audit; no OOS selection",
        },
        "protocol": {
            "data": "Binance spot 2017-2019 stitched to USD-M 2020-latest",
            "signal": "completed daily candle; next bar execution",
            "costs": "10 bps fee + 5 bps slippage; historical Funding on futures segment",
            "spot_cap": str(SPOT_CAP),
            "maximum_futures_leverage": str(MAX_FUTURES_LEVERAGE),
            "hard_effective_leverage_cap": "3X",
            "maintenance_rate": str(MAINTENANCE),
        },
        "data": {
            "spot_bars": len(spot),
            "futures_daily_bars": len(futures),
            "combined_bars": len(bars),
            "funding_events": sum(len(items) for items in funding),
            "first": base.iso(bars[0].start_ms),
            "last": base.iso(bars[-1].end_ms),
        },
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


def build_targets(bars, active: Decimal):
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
        output.append(Decimal("0") if state == "bear" else active)
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


def period_bounds(last_end: int, spot_end: int):
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
        "# BTC SMA10/40 Hysteresis Exposure Grid (Hard 3X)",
        "",
        "信号固定为 SMA10/40、连续 2 根 bearish 才降为 0X、连续 1 根恢复；仅扫描主动暴露。",
        "2025–最新为只读验证区间，不参与暴露选择。",
        "",
        "| 主动暴露 | Full收益 | B&H | Full超额 | Full DD | 2025+超额 | 最高盘中杠杆 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        full = row["metrics"]["stitched_full"]
        oos = row["metrics"]["2025_latest"]
        lines.append(
            f"| {row['active_exposure']}X | {pct(full['strategy_return'])} | "
            f"{pct(full['benchmark_return'])} | {pct(full['excess'])} | "
            f"{pct(full['strategy_drawdown'])} | {pct(oos['excess'])} | "
            f"{full['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "## Bootstrap",
        "",
        ("| 主动暴露 | 30日超过 B&H | 30日超额 P05 | 90日超过 B&H | 90日超额 P05 |"),
        "|---:|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        b30 = row["bootstrap"]["30d"]
        b90 = row["bootstrap"]["90d"]
        lines.append(
            f"| {row['active_exposure']}X | {pct(b30['probability_beats_bh_return'])} | "
            f"{pct(b30['annualized_excess_vs_bh']['p05'])} | "
            f"{pct(b90['probability_beats_bh_return'])} | "
            f"{pct(b90['annualized_excess_vs_bh']['p05'])} |"
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
