#!/usr/bin/env python3
"""Replay daily BTC candidates across spot 2017-2019 and futures 2020-latest."""

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

OUTPUT_DIR = Path("reports/experiments/btc_stitched_3x/2026-09-02")
START_MS = int(datetime(2017, 10, 1, tzinfo=UTC).timestamp() * 1000)
FUTURES_START_MS = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
MAINTENANCE = Decimal("0.02")
SPOT_CAP = Decimal("0.5")
LEVERAGE_CAP = Decimal("2")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    spot = [bar for bar in load_spot_bars() if bar.end_ms < FUTURES_START_MS]
    validate_daily_continuity(spot)
    futures_15m = load_market("BTCUSDT")
    futures_daily, _ = aggregate_complete_periods(futures_15m, "1d")
    futures = [bar for bar in futures_daily if bar.start_ms >= FUTURES_START_MS]
    if not futures:
        raise ValueError("no futures daily bars after stitch date")
    combined = spot + futures
    validate_daily_continuity(combined)
    futures_events = load_funding("BTCUSDT", futures_15m)
    combined_funding = [[] for _ in combined]
    aligned_funding = funding_by_bar(futures, futures_events)
    combined_funding[len(spot) :] = aligned_funding
    fast = simple_moving_average(combined, 8)
    slow = simple_moving_average(combined, 40)
    variants = {
        "sma8-40-bear-flat": build_targets(combined, fast, slow, mode="flat"),
        "sma8-40-slope5-bear-flat": build_targets(combined, fast, slow, slope=5, mode="flat"),
        "three-state-bull1.5-neutral1-bear0": build_targets(
            combined, fast, slow, mode="three_state"
        ),
    }
    periods = {
        "stitched_full": (START_MS, combined[-1].end_ms),
        "spot_pre2020": (START_MS, spot[-1].end_ms),
        "futures_2020_latest": (futures[0].start_ms, futures[-1].end_ms),
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
            combined[-1].end_ms,
        ),
    }
    results = {}
    for name, targets in variants.items():
        results[name] = {}
        for label, (start, end) in periods.items():
            segment_funding = [[] for _ in combined] if end < FUTURES_START_MS else combined_funding
            result = replay_segregated(
                combined,
                targets,
                segment_funding,
                start,
                end,
                spot_cap=SPOT_CAP,
                maintenance_rate=MAINTENANCE,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                enforce_effective_leverage_cap=True,
                maximum_futures_leverage=LEVERAGE_CAP,
            )
            bh = benchmark(combined, start, end)
            results[name][label] = {
                "strategy_return": result.net_return,
                "benchmark_return": bh["net_return"],
                "excess": result.net_return - bh["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": bh["max_drawdown"],
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                "liquidated": result.liquidated,
                "fees": result.total_fees,
                "funding": result.total_funding,
            }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "data": {
            "spot_bars": len(spot),
            "futures_15m_bars": len(futures_15m),
            "futures_daily_bars": len(futures),
            "combined_daily_bars": len(combined),
            "first": iso(combined[0].start_ms),
            "last": iso(combined[-1].end_ms),
            "futures_funding_events": len(futures_events),
            "continuity": "combined daily sequence has no missing or duplicate dates",
        },
        "protocol": {
            "signal": "completed daily SMA; next daily open execution",
            "costs": "10 bps fee + 5 bps slippage",
            "funding": "historical futures funding only from 2020-01-01 onward",
            "capital": "50% spot and 50% futures collateral",
            "leverage": "2x order cap as buffer; observed effective leverage must stay below 3x",
            "variants": (
                "bear-flat avoids pre-perpetual synthetic short funding; "
                "three-state adds neutral 1x"
            ),
        },
        "periods": {label: [iso(start), iso(end)] for label, (start, end) in periods.items()},
        "results": results,
        "conclusion": {
            "status": "RESEARCH_ONLY",
            "reason": (
                "Stitching is a cross-provider path check, not a substitute for a single "
                "venue's full-history execution data."
            ),
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def build_targets(bars, fast, slow, *, slope=0, mode):
    output = []
    for i, bar in enumerate(bars):
        if fast[i] is None or slow[i] is None or (slope and (i < slope or slow[i - slope] is None)):
            output.append(None)
            continue
        bullish = bar.close > slow[i] and fast[i] > slow[i]
        bearish = bar.close < slow[i] and fast[i] < slow[i]
        if slope:
            bearish = bearish and slow[i] < slow[i - slope]
        if mode == "three_state":
            output.append(Decimal("1.5") if bullish else Decimal("0") if bearish else Decimal("1"))
        else:
            output.append(Decimal("0") if bearish else Decimal("1.5"))
    return tuple(output)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC Stitched Spot + Futures 3X Audit",
        "",
        "使用 Binance 现货 2017-08 至 2019-12 与 USD-M 2020-01 至最新的连续日线；"
        "永续 Funding 仅从 2020 年开始计入。",
        "",
        (
            f"数据：现货 {payload['data']['spot_bars']} 根、"
            f"永续 {payload['data']['futures_daily_bars']} 根日线，"
            f"合计 {payload['data']['combined_daily_bars']} 根；无日期缺口。"
        ),
        "",
        "| 版本 | 区间 | 策略收益 | B&H | 超额 | 策略DD | B&H DD | 盘中杠杆 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, periods in payload["results"].items():
        for period, row in periods.items():
            lines.append(
                f"| `{variant}` | {period} | {pct(row['strategy_return'])} | "
                f"{pct(row['benchmark_return'])} | {pct(row['excess'])} | "
                f"{pct(row['strategy_drawdown'])} | {pct(row['benchmark_drawdown'])} | "
                f"{row['maximum_intrabar_leverage']:.3f}X |"
            )
    lines += [
        "",
        (
            "结论：这是跨数据源的独立路径验证。Funding 口径和现货/永续微小价格差异使其"
            "不能替代单一账户回放，当前仍为 **RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。"
        ),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
