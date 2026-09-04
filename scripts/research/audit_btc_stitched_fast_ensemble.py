#!/usr/bin/env python3
"""Audit the faster SMA ensemble on stitched BTC spot and perpetual history."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_sma12_40 as base

from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_stitched_fast_ensemble/2026-09-02")
COMPONENTS = ((8, 40), (12, 40), (15, 40))
BULL = Decimal("1.5")
BEAR = Decimal("-0.1")
FUNDING_THRESHOLD = Decimal("0.0001")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-funding-gate", action="store_true")
    parser.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    parser.add_argument("--slippage-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    # The replay implementation reads these module-level protocol constants.
    # Override them only for this process so sensitivity runs remain isolated.
    base.FEE_BPS = args.fee_bps
    base.SLIPPAGE_BPS = args.slippage_bps
    args.output_dir.mkdir(parents=True, exist_ok=True)
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
    raw_targets = build_targets(bars)
    targets = raw_targets if args.no_funding_gate else apply_funding_gate(raw_targets, funding)
    end_ms = bars[-1].end_ms
    periods = {
        "spot_pre2020": (base.START_MS, spot[-1].end_ms),
        "2020_2022": (
            base.FUTURES_START_MS,
            int(datetime(2022, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
        ),
        "2023_2024": (
            int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2024, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
        ),
        "2025_latest": (int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000), end_ms),
        "stitched_full": (base.START_MS, end_ms),
    }
    segments = {}
    for name, (start, end) in periods.items():
        result = base.replay(bars, targets, funding, start, end, False)
        bh = base.benchmark(bars, start, end)
        segments[name] = public(result, bh)
    full = base.replay(bars, targets, funding, *periods["stitched_full"], True)
    logs, bh_logs = base.paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=periods["stitched_full"][0]
    )
    bootstrap = {
        f"{block}d": base.run_bootstrap(
            logs, bh_logs, block_days=block, samples=10_000, seed=20261300 + block
        )
        for block in (7, 30, 90)
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": {
            "components": COMPONENTS,
            "bull_exposure": str(BULL),
            "bear_exposure": str(BEAR),
            "funding_threshold": None if args.no_funding_gate else str(FUNDING_THRESHOLD),
            "funding_gate_enabled": not args.no_funding_gate,
            "selection_note": "challenger discovered during exploration; not clean OOS",
        },
        "protocol": {
            "data": "Binance spot 2017-2019 stitched to USD-M 2020-latest",
            "signal": "completed daily candle; next bar execution",
            "costs": (
                f"{args.fee_bps:g} bps fee + {args.slippage_bps:g} bps slippage; "
                "funding on perpetual segment"
            ),
            "leverage": "2x futures order cap; effective leverage audited below 3x",
        },
        "data": {
            "spot_bars": len(spot),
            "futures_daily_bars": len(futures),
            "combined_bars": len(bars),
            "funding_events": len(events),
            "first": base.iso(bars[0].start_ms),
            "last": base.iso(end_ms),
        },
        "segments": segments,
        "bootstrap": bootstrap,
        "full_cagr": annualized(
            segments["stitched_full"]["strategy_return"], periods["stitched_full"]
        ),
        "benchmark_cagr": annualized(
            segments["stitched_full"]["benchmark_return"], periods["stitched_full"]
        ),
        "hard_cap_passed": all(
            row["maximum_intrabar_leverage"] <= 3.0 for row in segments.values()
        ),
    }
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(render(payload), encoding="utf-8")
    print(args.output_dir / "README.md")


def build_targets(bars):
    streams = []
    for fast, slow in COMPONENTS:
        f = simple_moving_average(bars, fast)
        s = simple_moving_average(bars, slow)
        streams.append(
            tuple(
                None
                if f[i] is None or s[i] is None
                else BEAR
                if bar.close < s[i] and f[i] < s[i]
                else BULL
                for i, bar in enumerate(bars)
            )
        )
    return tuple(
        None if any(value is None for value in values) else sum(values, Decimal("0")) / 3
        for values in zip(*streams, strict=True)
    )


def apply_funding_gate(targets, funding):
    state = Decimal("0")
    latest = Decimal("0")
    output = []
    for target, events in zip(targets, funding, strict=True):
        if target is not None:
            state = Decimal(target)
        for event in events:
            latest = event.rate
        output.append(Decimal("1") if state > 1 and latest > FUNDING_THRESHOLD else state)
    return tuple(output)


def public(result, baseline):
    return {
        "strategy_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "strategy_drawdown": result.max_drawdown,
        "benchmark_drawdown": baseline["max_drawdown"],
        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
    }


def annualized(net_return, bounds):
    years = (bounds[1] - bounds[0]) / (365.2425 * 86_400_000)
    return (1 + net_return) ** (1 / years) - 1


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC Stitched Faster SMA Ensemble Challenger",
        "",
        ("固定 SMA8/40、SMA12/40、SMA15/40 等权；熊市 -0.1X，牛市 1.5X。"),
        (
            "数据为 Binance 现货 2017–2019 与 USD-M 2020–最新拼接；"
            "候选曾在探索阶段暴露，不能视为干净 OOS。"
        ),
        "",
        "| 区间 | 策略 | B&H | 超额 | 策略DD | 盘中最高杠杆 |",
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
        f"Full CAGR：{pct(payload['full_cagr'])}；B&H CAGR：{pct(payload['benchmark_cagr'])}。",
        f"硬杠杆约束：{'通过' if payload['hard_cap_passed'] else '失败'}。",
        "",
        "## Bootstrap",
        "",
    ]
    for block, row in payload["bootstrap"].items():
        lines.append(
            f"- {block}: 超过 B&H {row['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += [
        "",
        (
            "Funding 过滤：已启用。"
            if payload["candidate"]["funding_gate_enabled"]
            else "Funding 过滤：未启用。"
        ),
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
