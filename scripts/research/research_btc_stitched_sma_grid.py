#!/usr/bin/env python3
"""Search a bounded daily SMA bear-flat grid on stitched BTC history."""

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

OUTPUT_DIR = Path("reports/experiments/btc_stitched_sma_grid/2026-09-02")
START_MS = int(datetime(2017, 10, 1, tzinfo=UTC).timestamp() * 1000)
FUTURES_START_MS = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
MAINTENANCE = Decimal("0.02")
SPOT_CAP = Decimal("0.5")
LEVERAGE_CAP = Decimal("2")
FAST_PERIODS = (5, 6, 7, 8, 9, 10, 12, 15, 20)
SLOW_PERIODS = (30, 35, 40, 45, 50, 60, 70, 80, 100)


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
    periods = build_periods(bars, spot, futures)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in periods.items()}
    rows = []
    for fast_period in FAST_PERIODS:
        fast = simple_moving_average(bars, fast_period)
        for slow_period in SLOW_PERIODS:
            if fast_period >= slow_period:
                continue
            slow = simple_moving_average(bars, slow_period)
            targets = build_targets(bars, fast, slow)
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
                    "fees": result.total_fees,
                    "funding": result.total_funding,
                }
            rows.append(
                {
                    "id": f"daily-sma{fast_period}-{slow_period}-bear-flat",
                    "fast": fast_period,
                    "slow": slow_period,
                    "metrics": metrics,
                }
            )
    for row in rows:
        row["development_score"] = min(
            row["metrics"][name]["excess"] for name in ("spot_pre2020", "2020_2022", "2023_2024")
        )
    rows.sort(key=lambda row: row["development_score"], reverse=True)
    selected = rows[0]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "protocol": {
            "strategy": "long-only daily SMA ordering with bear-flat exposure",
            "rule": "1.5x when close >= slow SMA or fast >= slow; 0x only when both are below",
            "selection": (
                "Research=2017-10..2019, 2020-2022; Validation=2023-2024; OOS=2025..latest"
            ),
            "costs": "10 bps fee + 5 bps slippage",
            "funding": "historical USD-M funding from 2020 onward; none on spot segment",
            "leverage": "2x order cap with observed effective leverage audited below 3x",
            "execution": "completed daily close; next daily open",
        },
        "data": {
            "spot_bars": len(spot),
            "futures_daily_bars": len(futures),
            "combined_bars": len(bars),
            "funding_events": len(events),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
            "continuity": "no duplicate or missing daily timestamps",
        },
        "periods": {name: [iso(start), iso(end)] for name, (start, end) in periods.items()},
        "benchmarks": benchmarks,
        "candidate_count": len(rows),
        "selected": selected,
        "top_candidates": rows[:25],
        "all_candidates": rows,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def build_periods(bars, spot, futures):
    end_2022 = int(datetime(2022, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000)
    start_2023 = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000)
    end_2024 = int(datetime(2024, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000)
    start_2025 = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000)
    return {
        "stitched_full": (START_MS, bars[-1].end_ms),
        "spot_pre2020": (START_MS, spot[-1].end_ms),
        "2020_2022": (futures[0].start_ms, end_2022),
        "2023_2024": (start_2023, end_2024),
        "2025_latest": (start_2025, bars[-1].end_ms),
    }


def build_targets(bars, fast, slow):
    return tuple(
        None
        if fast[index] is None or slow[index] is None
        else Decimal("0")
        if bar.close < slow[index] and fast[index] < slow[index]
        else Decimal("1.5")
        for index, bar in enumerate(bars)
    )


def replay(bars, targets, funding, start, end):
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
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=LEVERAGE_CAP,
    )


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC Stitched Daily SMA Grid (Hard 3X)",
        "",
        "固定 bear-flat 机制，扫描预先限定的日线 SMA 快慢周期。选择只使用 2017–2024，"
        "2025–最新作为只读 OOS。",
        "",
        "| 配置 | 开发期最差超额 | 2025 OOS超额 | Full超额 | Full DD | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["top_candidates"]:
        full = row["metrics"]["stitched_full"]
        lines.append(
            f"| `{row['id']}` | {pct(row['development_score'])} | "
            f"{pct(row['metrics']['2025_latest']['excess'])} | {pct(full['excess'])} | "
            f"{pct(full['strategy_drawdown'])} | {full['maximum_intrabar_leverage']:.3f}X |"
        )
    selected = payload["selected"]
    lines += [
        "",
        f"选择结果：`{selected['id']}`。候选数 {payload['candidate_count']}，"
        "所有回放均无强平，盘中有效杠杆使用 2X 下单缓冲。",
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。OOS、滚动窗口和 Bootstrap "
        "不能反向修改参数。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
