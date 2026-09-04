#!/usr/bin/env python3
"""Research BTC daily SMA hysteresis exposure under strict 3x and cost stress."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_hysteresis_exposure_cost_grid/2026-09-03")
FAST_PERIODS = (10, 12)
SLOW_PERIOD = 40
ENTER_COUNTS = (1, 2, 3)
EXIT_COUNTS = (1, 2)
ACTIVE_EXPOSURES = tuple(Decimal(value) for value in ("0.75", "1", "1.25", "1.5"))
COSTS = (
    ("default", Decimal("10"), Decimal("5")),
    ("moderate", Decimal("20"), Decimal("10")),
    ("stress", Decimal("50"), Decimal("25")),
)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    rows = []
    for fast in FAST_PERIODS:
        for enter in ENTER_COUNTS:
            for exit in EXIT_COUNTS:
                for active in ACTIVE_EXPOSURES:
                    sparse = hysteresis_targets(daily, fast, enter, exit, active)
                    targets = map_targets_to_source(len(bars), sparse, ends)
                    metrics = {}
                    for name, bounds in splits.items():
                        metrics[name] = {}
                        for label, fee, slippage in COSTS:
                            result = replay_segregated(
                                bars,
                                targets,
                                funding,
                                *bounds,
                                spot_cap=Decimal("0.5"),
                                maintenance_rate=Decimal("0.02"),
                                fee_bps=fee,
                                slippage_bps=slippage,
                                enforce_effective_leverage_cap=True,
                                maximum_futures_leverage=Decimal("2.5"),
                            )
                            metrics[name][label] = public(result, benchmarks[name], bounds)
                    development = [
                        metrics[name][cost]["excess"]
                        for name in ("research", "validation")
                        for cost in ("default", "moderate", "stress")
                    ]
                    rows.append(
                        {
                            "id": f"sma{fast}/40-enter{enter}-exit{exit}-active{active}x",
                            "fast": fast,
                            "slow": SLOW_PERIOD,
                            "enter": enter,
                            "exit": exit,
                            "active": str(active),
                            "metrics": metrics,
                            "development_worst_excess": min(development),
                            "development_median_excess": sorted(development)[len(development) // 2],
                        }
                    )
    rows.sort(
        key=lambda row: (
            row["development_worst_excess"],
            row["development_median_excess"],
            row["metrics"]["validation"]["default"]["excess"],
        ),
        reverse=True,
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "signal": (
                "completed UTC daily SMA fast/40; enter bear after N bearish days, "
                "recover after M non-bearish days"
            ),
            "execution": "next 15m open",
            "wallets": "50% spot and 50% isolated USD-M collateral",
            "costs": "default 10+5 bps, moderate 20+10 bps, stress 50+25 bps per side",
            "hard_cap": "2.5X opening control and <=3X observed effective leverage",
            "selection": "worst Research/Validation excess across all costs; OOS excluded",
        },
        "data": {
            "bars": len(bars),
            "daily_bars": len(daily),
            "last": datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).isoformat(),
        },
        "benchmarks": benchmarks,
        "results": rows,
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def hysteresis_targets(daily, fast_period: int, enter: int, exit: int, active: Decimal):
    fast = simple_moving_average(daily, fast_period)
    slow = simple_moving_average(daily, SLOW_PERIOD)
    state = None
    bear_count = 0
    recovery_count = 0
    output = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            output.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= enter:
            state = "bear"
        elif state == "bear" and recovery_count >= exit:
            state = "active"
        output.append(Decimal("0") if state == "bear" else active)
    return tuple(output)


def public(result, baseline, bounds):
    return {
        "net_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "cagr": (1 + result.net_return) ** (1 / years_between(*bounds)) - 1,
        "max_drawdown": result.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "rebalances": result.rebalances,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def render(payload):
    lines = [
        "# BTC SMA Hysteresis Exposure and Cost Grid (Strict 3X)",
        "",
        "按 Research/Validation 三档成本的最差超额排序；OOS 不参与选择。",
        "",
        (
            "| 配置 | 开发最差 | Research压力 | Validation压力 | OOS默认 | Full默认 | "
            "Full CAGR | DD | 杠杆 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"][:40]:
        m = row["metrics"]
        full = m["full"]["default"]
        lines.append(
            f"| `{row['id']}` | {row['development_worst_excess']:.2%} | "
            f"{m['research']['stress']['excess']:.2%} | "
            f"{m['validation']['stress']['excess']:.2%} | "
            f"{m['oos']['default']['excess']:.2%} | {full['excess']:.2%} | "
            f"{full['cagr']:.2%} | {full['max_drawdown']:.2%} | "
            f"{full['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += ["", "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
