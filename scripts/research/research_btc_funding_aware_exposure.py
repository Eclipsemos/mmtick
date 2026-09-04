#!/usr/bin/env python3
"""Test causal funding-aware leverage on the BTC three-state strategy."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import (
    as_dict,
    benchmark,
    replay_dynamic,
    replay_dynamic_incremental,
)
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

PERIODS = ((24, 48, 96, 192), (25, 50, 100, 200), (26, 52, 104, 208))
BULL_EXPOSURES = tuple(Decimal(value) for value in ("1.5", "1.75", "2", "2.25"))
FUNDING_THRESHOLDS = (
    Decimal("-0.00005"),
    Decimal("0"),
    Decimal("0.000025"),
    Decimal("0.00005"),
    Decimal("0.0001"),
)


def main() -> None:
    output_dir = Path("reports/experiments/btc_funding_aware_exposure/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    events = load_funding("BTCUSDT", bars)
    funding = funding_by_bar(bars, events)
    splits = split_periods(bars)
    benchmarks = {split: benchmark(bars, start, end) for split, (start, end) in splits.items()}
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    rows = []
    for periods in PERIODS:
        for bull_exposure in BULL_EXPOSURES:
            period_targets = three_state_targets(aggregate, periods, Decimal("0"), bull_exposure)
            regime_targets = map_targets_to_source(len(bars), period_targets, ends)
            for threshold in FUNDING_THRESHOLDS:
                targets = funding_aware_targets(regime_targets, funding, bull_exposure, threshold)
                metrics = {}
                for split, (start, end) in splits.items():
                    base = replay_dynamic_incremental(
                        bars,
                        targets,
                        funding,
                        start,
                        end,
                        funding_on_excess_only=True,
                    )
                    stress = replay_dynamic_incremental(
                        bars,
                        targets,
                        funding,
                        start,
                        end,
                        fee_bps=Decimal("10"),
                        slippage_bps=Decimal("5"),
                        funding_on_excess_only=True,
                    )
                    conservative = replay_dynamic(
                        bars,
                        targets,
                        funding,
                        start,
                        end,
                        funding_on_excess_only=True,
                    )
                    metrics[split] = {
                        "base": as_dict(base),
                        "stress": as_dict(stress),
                        "conservative_full_reopen": as_dict(conservative),
                        "excess_return": base.net_return - benchmarks[split]["net_return"],
                    }
                rows.append(
                    {
                        "id": (
                            f"4h-{'-'.join(map(str, periods))}-bull{bull_exposure}x-"
                            f"funding-le-{threshold}"
                        ),
                        "periods": periods,
                        "bull_exposure": str(bull_exposure),
                        "funding_threshold": str(threshold),
                        "metrics": metrics,
                    }
                )
    rows.sort(key=score, reverse=True)
    qualifying = [row for row in rows if qualifies(row, benchmarks)]
    qualifying.sort(key=lambda row: row["metrics"]["full"]["base"]["max_drawdown"], reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "strategy": (
                "bear 0x, neutral 1x, bull leverage only when last known funding is below threshold"
            ),
            "causality": (
                "funding event changes the next-bar target; no future funding rate is used"
            ),
            "funding": "charged only on exposure above 1x",
            "maximum_exposure": "2.25x in this grid",
            "costs": "base 5+2 bps; stress 10+5 bps per fill",
            "rebalancing": (
                "primary replay trades only exposure delta; conservative replay fully reopens"
            ),
            "ranking": "research and validation only; OOS is excluded from score",
        },
        "benchmark": benchmarks,
        "qualifying": qualifying,
        "results": rows,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def funding_aware_targets(regime_targets, funding, bull_exposure, threshold):
    regime = Decimal("1")
    latest_rate = Decimal("0")
    targets = []
    previous_target = None
    for index, regime_target in enumerate(regime_targets):
        if regime_target is not None:
            regime = Decimal(regime_target)
        for event in funding[index]:
            latest_rate = event.rate
        target = regime
        if regime == bull_exposure and latest_rate > threshold:
            target = Decimal("1")
        targets.append(target if target != previous_target else None)
        previous_target = target
    return tuple(targets)


def qualifies(row, benchmarks):
    full = row["metrics"]["full"]["base"]
    stress = row["metrics"]["full"]["stress"]
    oos = row["metrics"]["oos"]["base"]
    return (
        not full["bankrupt"]
        and full["net_return"] > benchmarks["full"]["net_return"]
        and stress["net_return"] > benchmarks["full"]["net_return"]
        and oos["net_return"] >= benchmarks["oos"]["net_return"]
        and full["max_drawdown"] >= benchmarks["full"]["max_drawdown"]
    )


def score(row):
    metrics = row["metrics"]
    return metrics["research"]["excess_return"] + metrics["validation"]["excess_return"]


def markdown(payload):
    lines = [
        "# BTC Funding-aware 动态暴露研究",
        "",
        "只使用已经公布的最近一期 Funding；费率高于阈值时把多头额外杠杆降回 1X。",
        "",
        "## 同时通过基准、压力成本、OOS 和回撤门槛",
        "",
        "| 配置 | 全样本 | DD | 压力全样本 | 全量重开 | OOS | OOS超额 | Funding成本 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["qualifying"]:
        full = row["metrics"]["full"]
        oos = row["metrics"]["oos"]
        lines.append(
            f"| `{row['id']}` | {pct(full['base']['net_return'])} | "
            f"{pct(full['base']['max_drawdown'])} | {pct(full['stress']['net_return'])} | "
            f"{pct(full['conservative_full_reopen']['net_return'])} | "
            f"{pct(oos['base']['net_return'])} | {pct(oos['excess_return'])} | "
            f"{full['base']['total_funding']:.2f} |"
        )
    if not payload["qualifying"]:
        lines.append("| 无 | - | - | - | - | - | - | - |")
    return "\n".join(lines) + "\n"


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
