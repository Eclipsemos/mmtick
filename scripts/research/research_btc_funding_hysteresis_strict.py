#!/usr/bin/env python3
"""Test low-turnover Funding confirmation on the strict BTC SMA10/40 strategy."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_sma10_three_state_hysteresis_strict import (
    hysteresis_targets,
    split_periods,
)
from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT = Path("reports/experiments/btc_funding_hysteresis_strict/2026-09-03")
THRESHOLD = Decimal("0.0001")
ACTIVE = Decimal("1.5")
REDUCED = Decimal("1")
CONFIRMATIONS = (1, 2, 3, 4)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    base = map_targets_to_source(len(bars), hysteresis_targets(daily), ends)
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    rows = []
    for enter in CONFIRMATIONS:
        for exit in CONFIRMATIONS:
            targets = funding_gate_hysteresis(base, funding, enter, exit)
            metrics = {}
            for name, bounds in splits.items():
                costs = {}
                for label, fee, slip in (
                    ("default", Decimal("10"), Decimal("5")),
                    ("moderate", Decimal("20"), Decimal("10")),
                    ("stress", Decimal("50"), Decimal("25")),
                ):
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
                    costs[label] = public(result, benchmarks[name], bounds)
                metrics[name] = costs
            default = metrics["full"]["default"]
            dev_values = [
                metrics[split][cost]["excess"]
                for split in ("research", "validation")
                for cost in ("default", "moderate", "stress")
            ]
            rows.append(
                {
                    "id": f"funding-enter{enter}-exit{exit}",
                    "enter_confirmations": enter,
                    "exit_confirmations": exit,
                    "metrics": metrics,
                    "development_worst_excess": min(dev_values),
                    "full_rebalances": default["rebalances"],
                }
            )
    rows.sort(
        key=lambda row: (
            row["development_worst_excess"],
            row["metrics"]["validation"]["default"]["excess"],
        ),
        reverse=True,
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "base": "daily SMA10/40; bear after 2 days, restore after 1 day; active 1.5X, bear 0X",
            "funding_gate": "latest known funding > 0.01% reduces active 1.5X to 1X",
            "confirmation": (
                "enter/exit counts are consecutive Funding events (normally 8h), not 15m bars"
            ),
            "execution": "completed UTC daily signal; next 15m open",
            "costs": "default 10+5 bps, moderate 20+10 bps, stress 50+25 bps per side",
            "hard_cap": "2.5X opening control and <=3X observed effective leverage",
            "selection": "worst Research/Validation excess across all three costs; OOS excluded",
        },
        "data": {
            "bars": len(bars),
            "daily_bars": len(daily),
            "funding_events": sum(len(events) for events in funding),
            "last": datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).isoformat(),
        },
        "benchmarks": benchmarks,
        "results": rows,
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def funding_gate_hysteresis(base, funding, enter: int, exit: int):
    if len(base) != len(funding):
        raise ValueError("target and funding streams must have equal lengths")
    high_streak = 0
    low_streak = 0
    reduced = False
    output = []
    previous = None
    for index, events in enumerate(funding):
        for event in events:
            if event.rate > THRESHOLD:
                high_streak += 1
                low_streak = 0
                if high_streak >= enter:
                    reduced = True
            else:
                low_streak += 1
                high_streak = 0
                if low_streak >= exit:
                    reduced = False
        target = base[index]
        if target is None:
            target = previous
        if target is None:
            output.append(None)
            continue
        target = Decimal("0") if Decimal(target) == 0 else (REDUCED if reduced else ACTIVE)
        output.append(target if target != previous else None)
        previous = target
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
        "fees": result.total_fees,
        "funding": result.total_funding,
        "rebalances": result.rebalances,
    }


def render(payload):
    lines = [
        "# BTC Funding Confirmation Hysteresis (Strict 15m)",
        "",
        "Funding 阈值固定为 0.01%；只有连续 Funding 事件确认才降/升杠杆。",
        "",
        (
            "| 配置 | 开发最差超额 | Research默认 | Validation默认 | OOS默认 | "
            "Full默认 | Full DD | 调仓 | 杠杆 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        m = row["metrics"]
        full = m["full"]["default"]
        lines.append(
            f"| `{row['id']}` | {row['development_worst_excess']:.2%} | "
            f"{m['research']['default']['excess']:.2%} | "
            f"{m['validation']['default']['excess']:.2%} | "
            f"{m['oos']['default']['excess']:.2%} | {full['excess']:.2%} | "
            f"{full['max_drawdown']:.2%} | {row['full_rebalances']} | "
            f"{full['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "## 压力成本开发期",
        "",
        "| 配置 | Research 50+25 | Validation 50+25 |",
        "|---|---:|---:|",
    ]
    for row in payload["results"]:
        m = row["metrics"]
        lines.append(
            f"| `{row['id']}` | {m['research']['stress']['excess']:.2%} | "
            f"{m['validation']['stress']['excess']:.2%} |"
        )
    lines += ["", "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
