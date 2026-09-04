#!/usr/bin/env python3
"""Select a daily SMA hysteresis candidate using only pre-2025 stitched data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_spot_pre2020 import load_spot_bars, validate_daily_continuity
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_stitched_hysteresis_grid/2026-09-02")
START_MS = int(datetime(2017, 10, 1, tzinfo=UTC).timestamp() * 1000)
FUTURES_START_MS = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
SPOT_CAP = Decimal("0.5")
MAX_FUTURES_LEVERAGE = Decimal("2")
FAST_PERIODS = (6, 8, 10, 12, 15, 20)
SLOW = 40
ENTER_DAYS = (1, 2, 3, 5)
EXIT_DAYS = (1, 2, 3)
ACTIVE = Decimal("1.5")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    spot = [bar for bar in load_spot_bars() if bar.end_ms < FUTURES_START_MS]
    validate_daily_continuity(spot)
    futures_15m = load_market("BTCUSDT")
    futures, _ = aggregate_complete_periods(futures_15m, "1d")
    futures = [bar for bar in futures if bar.start_ms >= FUTURES_START_MS]
    bars = spot + futures
    validate_daily_continuity(bars)
    funding = [[] for _ in bars]
    funding[len(spot) :] = funding_by_bar(futures, load_funding("BTCUSDT", futures_15m))
    periods = period_bounds(spot, bars[-1].end_ms)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in periods.items()}
    rows = []
    for fast in FAST_PERIODS:
        for enter in ENTER_DAYS:
            for exit_days in EXIT_DAYS:
                targets = build_targets(bars, fast, enter, exit_days)
                metrics = {}
                for name, (start, end) in periods.items():
                    result = replay(bars, targets, funding, start, end)
                    metrics[name] = {
                        "strategy_return": result.net_return,
                        "benchmark_return": benchmarks[name]["net_return"],
                        "excess": result.net_return - benchmarks[name]["net_return"],
                        "strategy_drawdown": result.max_drawdown,
                        "benchmark_drawdown": benchmarks[name]["max_drawdown"],
                        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                        "liquidated": result.liquidated,
                    }
                development = min(
                    metrics[name]["excess"] for name in ("spot_pre2020", "2020_2022", "2023_2024")
                )
                rows.append(
                    {
                        "id": f"daily-sma{fast}-40-enter{enter}-exit{exit_days}",
                        "fast": fast,
                        "slow": SLOW,
                        "enter_bear_days": enter,
                        "exit_bear_days": exit_days,
                        "development_score": development,
                        "metrics": metrics,
                    }
                )
    rows.sort(key=lambda row: row["development_score"], reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "selection": (
                "ranked only by the minimum excess across 2017-2019, 2020-2022, and 2023-2024"
            ),
            "oos": "2025-latest is read-only and excluded from selection",
            "signal": "completed daily candle; next bar execution",
            "costs": "10 bps fee + 5 bps slippage; historical Funding on futures segment",
            "leverage": "2x futures order cap; effective leverage audited below 3x",
        },
        "data": {
            "spot_bars": len(spot),
            "futures_daily_bars": len(futures),
            "combined_bars": len(bars),
            "funding_events": sum(len(items) for items in funding),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
        },
        "periods": {name: [iso(start), iso(end)] for name, (start, end) in periods.items()},
        "benchmarks": benchmarks,
        "candidate_count": len(rows),
        "selected": rows[0],
        "top_candidates": rows[:30],
        "all_candidates": rows,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def build_targets(bars, fast_period, enter_days, exit_days):
    fast = simple_moving_average(bars, fast_period)
    slow = simple_moving_average(bars, SLOW)
    state = None
    bear_count = 0
    recovery_count = 0
    targets = []
    for index, bar in enumerate(bars):
        if fast[index] is None or slow[index] is None:
            targets.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= enter_days:
            state = "bear"
        elif state == "bear" and recovery_count >= exit_days:
            state = "active"
        targets.append(Decimal("0") if state == "bear" else ACTIVE)
    return tuple(targets)


def replay(bars, targets, funding, start, end):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=SPOT_CAP,
        maintenance_rate=Decimal("0.02"),
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=MAX_FUTURES_LEVERAGE,
    )


def period_bounds(spot, last_end):
    return {
        "spot_pre2020": (START_MS, spot[-1].end_ms),
        "2020_2022": (
            FUTURES_START_MS,
            int(datetime(2022, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
        ),
        "2023_2024": (
            int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2024, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
        ),
        "2025_latest": (
            int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000),
            last_end,
        ),
        "stitched_full": (START_MS, last_end),
    }


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def render(payload):
    selected = payload["selected"]
    lines = [
        "# BTC Stitched Daily SMA Hysteresis Grid",
        "",
        "参数只按 2017–2024 的最差分段超额选择；2025–最新严格只读。",
        "",
        "| 配置 | 开发最差超额 | 2025 OOS超额 | Full超额 | Full DD |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in payload["top_candidates"]:
        full = row["metrics"]["stitched_full"]
        oos = row["metrics"]["2025_latest"]
        lines.append(
            f"| `{row['id']}` | {pct(row['development_score'])} | {pct(oos['excess'])} | "
            f"{pct(full['excess'])} | {pct(full['strategy_drawdown'])} |"
        )
    lines += [
        "",
        f"选择结果：`{selected['id']}`；候选数 {payload['candidate_count']}。",
        "OOS 没有参与选择；所有回放使用 2X 下单缓冲和历史 Funding。",
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
