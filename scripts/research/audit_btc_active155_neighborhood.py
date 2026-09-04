#!/usr/bin/env python3
"""Audit a predeclared neighborhood around the BTC active-1.55x challenger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_sma10_three_state_hysteresis_strict import split_periods
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_active155_neighborhood/2026-09-03")
FAST_PERIODS = (9, 10, 11)
SLOW = 40
ENTER_DAYS = (2, 3, 4)
EXIT_DAYS = (1, 2)
ACTIVE = (Decimal("1.50"), Decimal("1.55"), Decimal("1.60"))
COSTS = (("default", Decimal("10"), Decimal("5")), ("stress", Decimal("50"), Decimal("25")))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    rows = []
    total = len(FAST_PERIODS) * len(ENTER_DAYS) * len(EXIT_DAYS) * len(ACTIVE)
    done = 0
    for fast in FAST_PERIODS:
        for enter in ENTER_DAYS:
            for exit_days in EXIT_DAYS:
                for active in ACTIVE:
                    dense = hysteresis_targets(daily, fast, enter, exit_days, active)
                    targets = map_targets_to_source(len(bars), dense, ends)
                    metrics = {}
                    for name, bounds in splits.items():
                        metrics[name] = {}
                        for label, fee, slip in COSTS:
                            result = replay_segregated(
                                bars,
                                targets,
                                funding,
                                *bounds,
                                spot_cap=Decimal("0.5"),
                                maintenance_rate=Decimal("0.02"),
                                fee_bps=fee,
                                slippage_bps=slip,
                                enforce_effective_leverage_cap=True,
                                maximum_futures_leverage=Decimal("2.5"),
                            )
                            metrics[name][label] = {
                                "net_return": result.net_return,
                                "benchmark_return": benchmarks[name]["net_return"],
                                "excess": result.net_return - benchmarks[name]["net_return"],
                                "max_drawdown": result.max_drawdown,
                                "maximum_intrabar_leverage": (
                                    result.maximum_observed_futures_leverage
                                ),
                                "liquidated": result.liquidated,
                                "rebalances": result.rebalances,
                            }
                    dev = [
                        metrics[name][cost]["excess"]
                        for name in ("research", "validation")
                        for cost in ("default", "stress")
                    ]
                    row = {
                        "id": f"sma{fast}/40-enter{enter}-exit{exit_days}-active{active}x",
                        "fast": fast,
                        "enter": enter,
                        "exit": exit_days,
                        "active": str(active),
                        "metrics": metrics,
                        "development_worst_excess": min(dev),
                        "development_all_positive": min(dev) > 0,
                    }
                    rows.append(row)
                    done += 1
                    if done % 3 == 0:
                        print(f"completed {done}/{total}", flush=True)
    rows.sort(
        key=lambda row: (
            row["development_all_positive"],
            row["development_worst_excess"],
        ),
        reverse=True,
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "neighborhood": (
                "SMA fast 9/10/11, slow 40; enter 2/3/4 days; exit 1/2 days; active 1.50/1.55/1.60X"
            ),
            "execution": "completed UTC daily signal; next 15m open",
            "wallets": "50% spot and 50% isolated USD-M collateral",
            "costs": "default 10+5 bps and stress 50+25 bps per side",
            "hard_cap": "2.5X opening control and <=3X observed effective leverage",
            "selection": "development-only; OOS is reported and never selected",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "benchmarks": benchmarks,
        "candidate_count": len(rows),
        "passing_development_count": sum(row["development_all_positive"] for row in rows),
        "results": rows,
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def hysteresis_targets(daily, fast_period: int, enter: int, exit_days: int, active: Decimal):
    fast = simple_moving_average(daily, fast_period)
    slow = simple_moving_average(daily, SLOW)
    state = None
    bear_count = recovery_count = 0
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
        elif state == "bear" and recovery_count >= exit_days:
            state = "active"
        output.append(Decimal("0") if state == "bear" else active)
    return tuple(output)


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def render(payload):
    lines = [
        "# BTC Active 1.55X Neighborhood Audit",
        "",
        "在 1.55X 候选附近测试快线、入熊确认和主动暴露；按开发期四项超额联合排序。",
        "",
        "| 配置 | 开发最差超额 | R默认 | V默认 | R压力 | V压力 | OOS默认 | Full默认 | DD | 杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        m = row["metrics"]
        full = m["full"]["default"]
        lines.append(
            f"| `{row['id']}` | {row['development_worst_excess']:.2%} | "
            f"{m['research']['default']['excess']:.2%} | "
            f"{m['validation']['default']['excess']:.2%} | "
            f"{m['research']['stress']['excess']:.2%} | "
            f"{m['validation']['stress']['excess']:.2%} | "
            f"{m['oos']['default']['excess']:.2%} | {full['excess']:.2%} | "
            f"{full['max_drawdown']:.2%} | {full['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        (
            f"开发期全部超额为正：{payload['passing_development_count']} / "
            f"{payload['candidate_count']}。"
        ),
        "",
    ]
    lines.append("状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
