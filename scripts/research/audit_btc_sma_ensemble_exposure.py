#!/usr/bin/env python3
"""Audit active-exposure sensitivity for the fixed BTC daily-SMA ensemble."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_daily_sma_ensemble_strict import (
    COMPONENTS,
    SPOT_CAP,
    equal_weight_targets,
    iso,
)
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_sma_ensemble_exposure/2026-09-03")
EXPOSURES = (Decimal("1.5"), Decimal("1.6"), Decimal("1.7"), Decimal("1.75"), Decimal("1.8"))
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
MAX_LEVERAGE = Decimal("3")


def dense_targets(daily, active: Decimal):
    streams = tuple(
        build_dense_targets_with_active(daily, item["fast"], item["slow"], item["bear"], active)
        for item in COMPONENTS
    )
    return equal_weight_targets(streams)


def build_dense_targets_with_active(daily, fast_period, slow_period, bear_exposure, active):
    fast = simple_moving_average(daily, fast_period)
    slow = simple_moving_average(daily, slow_period)
    output = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            output.append(None)
        elif bar.close < slow[index] and fast[index] < slow[index]:
            output.append(bear_exposure)
        else:
            output.append(active)
    return tuple(output)


def replay(bars, targets, funding, start, end, *, record_equity=False):
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
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=MAX_LEVERAGE,
    )


def summary(result, baseline):
    return {
        "net_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "max_drawdown": result.max_drawdown,
        "benchmark_drawdown": baseline["max_drawdown"],
        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "rebalances": result.rebalances,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def main() -> None:
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *period) for name, period in splits.items()}
    daily, ends = aggregate_complete_periods(bars, "1d")
    rows = []
    for active in EXPOSURES:
        dense = dense_targets(daily, active)
        targets = map_targets_to_source(len(bars), dense, ends)
        metrics = {}
        full_result = None
        for name, period in splits.items():
            result = replay(bars, targets, funding, *period, record_equity=name == "full")
            metrics[name] = summary(result, benchmarks[name])
            if name == "full":
                full_result = result
        assert full_result is not None
        strategy_logs, benchmark_logs = paired_daily_log_returns(
            bars, full_result.equity_curve, 100_000.0, start_ms=splits["full"][0]
        )
        rows.append(
            {
                "active_exposure": str(active),
                "metrics": metrics,
                "bootstrap_90d": run_bootstrap(
                    strategy_logs, benchmark_logs, block_days=90, samples=10_000, seed=20260990
                ),
            }
        )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / EXPOSURE_SENSITIVITY",
        "protocol": {
            "components": [{**item, "bear": str(item["bear"])} for item in COMPONENTS],
            "construction": "fixed equal weights; no exposure selection from OOS",
            "exposures": [str(value) for value in EXPOSURES],
            "signal": "completed UTC daily bars; next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical Funding",
            "hard_cap": "3X maximum futures leverage and observed effective leverage",
        },
        "data": {
            "bars": len(bars),
            "daily_bars": len(daily),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
            "full_years": years_between(*splits["full"]),
        },
        "results": rows,
        "all_exposures_beat_bh_all_splits": all(
            row["metrics"][name]["excess"] > 0 for row in rows for name in splits
        ),
        "all_exposures_within_hard_cap": all(
            row["metrics"][name]["maximum_intrabar_leverage"] <= float(MAX_LEVERAGE)
            and not row["metrics"][name]["liquidated"]
            for row in rows
            for name in splits
        ),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def render(payload):
    lines = [
        "# BTC Daily-SMA Ensemble Active-Exposure Audit",
        "",
        (
            "固定 SMA7/35、SMA8/40、SMA12/40 等权组合，只扫描预先声明的非熊市主动暴露；"
            "不根据 OOS 选值。"
        ),
        "",
        "| Active | Research超额 | Validation超额 | OOS超额 | Full CAGR | Full DD | "
        "杠杆 | 90d P05 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        metrics = row["metrics"]
        full = metrics["full"]
        full_cagr = (1 + full["net_return"]) ** (1 / payload["data"]["full_years"]) - 1
        lines.append(
            f"| {row['active_exposure']}X | {metrics['research']['excess']:.2%} | "
            f"{metrics['validation']['excess']:.2%} | {metrics['oos']['excess']:.2%} | "
            f"{full_cagr:.2%} | {full['max_drawdown']:.2%} | "
            f"{full['maximum_intrabar_leverage']:.3f}X | "
            f"{row['bootstrap_90d']['annualized_excess_vs_bh']['p05']:.2%} |"
        )
    lines += [
        "",
        f"跨所有区间超过 B&H：{'是' if payload['all_exposures_beat_bh_all_splits'] else '否'}。",
        f"所有配置满足严格 3X：{'是' if payload['all_exposures_within_hard_cap'] else '否'}。",
        "主动暴露越高只代表杠杆变化；若要冻结，必须预先指定参数并独立前瞻观察。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
