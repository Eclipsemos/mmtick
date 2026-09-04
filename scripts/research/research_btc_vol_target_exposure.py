#!/usr/bin/env python3
"""Research causal volatility-targeted BTC exposure capped at 3x."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from research_btc_dynamic_exposure import as_dict, benchmark, replay_dynamic
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

PERIODS = ((24, 48, 96, 192), (25, 50, 100, 200), (26, 52, 104, 208))
VOL_LOOKBACKS = (42, 126)
TARGET_VOLS = tuple(Decimal(value) for value in ("0.5", "0.75", "1", "1.25", "1.5"))
FUNDING_THRESHOLD = Decimal("0.0001")
EXPOSURE_STEP = Decimal("0.25")
MAX_EXPOSURE = Decimal("3")


def main() -> None:
    output_dir = Path("reports/experiments/btc_vol_target_exposure/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {split: benchmark(bars, start, end) for split, (start, end) in splits.items()}
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    rows = []
    for periods in PERIODS:
        regime = three_state_targets(aggregate, periods, Decimal("0"), Decimal("2"))
        for lookback in VOL_LOOKBACKS:
            realized = annualized_volatility(aggregate, lookback)
            for target_vol in TARGET_VOLS:
                period_targets = volatility_targets(regime, realized, target_vol)
                source_targets = map_targets_to_source(len(bars), period_targets, ends)
                targets = funding_filter(source_targets, funding, FUNDING_THRESHOLD)
                metrics = {}
                for split, (start, end) in splits.items():
                    base = replay_dynamic(
                        bars,
                        targets,
                        funding,
                        start,
                        end,
                        funding_on_excess_only=True,
                    )
                    stress = replay_dynamic(
                        bars,
                        targets,
                        funding,
                        start,
                        end,
                        fee_bps=Decimal("10"),
                        slippage_bps=Decimal("5"),
                        funding_on_excess_only=True,
                    )
                    metrics[split] = {
                        "base": as_dict(base),
                        "stress": as_dict(stress),
                        "excess_return": base.net_return - benchmarks[split]["net_return"],
                    }
                rows.append(
                    {
                        "id": (
                            f"4h-{'-'.join(map(str, periods))}-vol{lookback}-target{target_vol}"
                        ),
                        "periods": periods,
                        "vol_lookback": lookback,
                        "target_vol": str(target_vol),
                        "metrics": metrics,
                    }
                )
    rows.sort(key=score, reverse=True)
    qualifying = [row for row in rows if qualifies(row, benchmarks)]
    qualifying.sort(key=lambda row: row["metrics"]["full"]["base"]["max_drawdown"], reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "strategy": "bear 0x, neutral 1x, bullish exposure = target vol / realized vol",
            "realized_vol": "closed 4h log returns, annualized with sqrt(6*365)",
            "exposure_rounding": "down to 0.25x increments",
            "maximum_exposure": "3x",
            "funding": "last known rate filter; charged only above 1x",
            "ranking": "research and validation only; OOS excluded",
        },
        "benchmark": benchmarks,
        "qualifying": qualifying,
        "results": rows,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def annualized_volatility(bars, lookback):
    returns: list[Decimal | None] = [None]
    for previous, current in zip(bars, bars[1:], strict=False):
        returns.append((current.close / previous.close).ln())
    values: list[Decimal | None] = []
    annualizer = Decimal(6 * 365).sqrt()
    for index in range(len(bars)):
        window = [
            value
            for value in returns[max(0, index - lookback + 1) : index + 1]
            if value is not None
        ]
        if len(window) < lookback:
            values.append(None)
            continue
        mean = sum(window, Decimal("0")) / Decimal(len(window))
        variance = sum(((value - mean) ** 2 for value in window), Decimal("0")) / Decimal(
            len(window) - 1
        )
        values.append(variance.sqrt() * annualizer)
    return tuple(values)


def volatility_targets(regime, realized, target_vol):
    targets = []
    for state, volatility in zip(regime, realized, strict=True):
        if state is None or volatility is None:
            targets.append(None)
        elif state == 0:
            targets.append(Decimal("0"))
        elif state == 1:
            targets.append(Decimal("1"))
        else:
            raw = MAX_EXPOSURE if volatility == 0 else target_vol / volatility
            raw = min(MAX_EXPOSURE, max(Decimal("1"), raw))
            targets.append(
                (raw / EXPOSURE_STEP).to_integral_value(rounding=ROUND_FLOOR) * EXPOSURE_STEP
            )
    return tuple(targets)


def funding_filter(source_targets, funding, threshold):
    state = Decimal("1")
    latest_rate = Decimal("0")
    previous_target = None
    targets = []
    for index, source_target in enumerate(source_targets):
        if source_target is not None:
            state = Decimal(source_target)
        for event in funding[index]:
            latest_rate = event.rate
        target = Decimal("1") if state > 1 and latest_rate > threshold else state
        targets.append(target if target != previous_target else None)
        previous_target = target
    return tuple(targets)


def qualifies(row, benchmarks):
    full = row["metrics"]["full"]
    oos = row["metrics"]["oos"]
    return (
        not full["base"]["bankrupt"]
        and full["base"]["net_return"] > benchmarks["full"]["net_return"]
        and full["stress"]["net_return"] > benchmarks["full"]["net_return"]
        and oos["base"]["net_return"] >= benchmarks["oos"]["net_return"]
        and full["base"]["max_drawdown"] >= benchmarks["full"]["max_drawdown"]
    )


def score(row):
    metrics = row["metrics"]
    return metrics["research"]["excess_return"] + metrics["validation"]["excess_return"]


def markdown(payload):
    lines = [
        "# BTC 波动率目标动态暴露研究",
        "",
        "多头趋势中按已完成 4h 收益波动率调整暴露；波动越高，杠杆越低。",
        "",
        "## 严格门槛通过配置",
        "",
        "| 配置 | 全样本 | DD | 压力全样本 | OOS | OOS超额 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["qualifying"]:
        full = row["metrics"]["full"]
        oos = row["metrics"]["oos"]
        lines.append(
            f"| `{row['id']}` | {pct(full['base']['net_return'])} | "
            f"{pct(full['base']['max_drawdown'])} | {pct(full['stress']['net_return'])} | "
            f"{pct(oos['base']['net_return'])} | {pct(oos['excess_return'])} |"
        )
    if not payload["qualifying"]:
        lines.append("| 无 | - | - | - | - | - |")
    return "\n".join(lines) + "\n"


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
