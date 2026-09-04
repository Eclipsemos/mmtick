#!/usr/bin/env python3
"""Research bullish, neutral, and bearish BTC exposure states."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import as_dict, benchmark, replay_dynamic
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

PERIODS = (
    (16, 32, 64, 128),
    (20, 40, 80, 160),
    (22, 44, 88, 176),
    (24, 48, 96, 192),
    (25, 50, 100, 200),
    (26, 52, 104, 208),
    (28, 56, 112, 224),
)
BEAR_EXPOSURES = tuple(Decimal(value) for value in ("0", "0.25", "0.5", "0.75"))
BULL_EXPOSURES = tuple(Decimal(value) for value in ("1.25", "1.5", "1.75", "2", "2.25", "2.5"))


def main() -> None:
    output_dir = Path("reports/experiments/btc_three_state_exposure/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {split: benchmark(bars, start, end) for split, (start, end) in splits.items()}
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    rows = []
    for periods in PERIODS:
        for bear_exposure in BEAR_EXPOSURES:
            for bull_exposure in BULL_EXPOSURES:
                period_targets = three_state_targets(
                    aggregate, periods, bear_exposure, bull_exposure
                )
                targets = map_targets_to_source(len(bars), period_targets, ends)
                metrics = {}
                for split, (start, end) in splits.items():
                    base = replay_dynamic(bars, targets, None, start, end)
                    stress = replay_dynamic(
                        bars,
                        targets,
                        None,
                        start,
                        end,
                        fee_bps=Decimal("10"),
                        slippage_bps=Decimal("5"),
                    )
                    hybrid = replay_dynamic(
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
                        "hybrid_funding": as_dict(hybrid),
                        "excess_return": base.net_return - benchmarks[split]["net_return"],
                    }
                rows.append(
                    {
                        "id": (
                            f"4h-{'-'.join(map(str, periods))}-bear{bear_exposure}x-"
                            f"neutral1x-bull{bull_exposure}x"
                        ),
                        "periods": periods,
                        "bear_exposure": str(bear_exposure),
                        "bull_exposure": str(bull_exposure),
                        "metrics": metrics,
                    }
                )
    rows.sort(key=score, reverse=True)
    qualifying = [row for row in rows if qualifies(row, benchmarks)]
    qualifying.sort(key=lambda row: row["metrics"]["full"]["base"]["max_drawdown"], reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "bull": "strict fast-to-slow SMA ordering",
            "bear": "close below slowest SMA and fastest SMA below slowest SMA",
            "neutral": "all other states, fixed at 1x",
            "maximum_exposure": "2x in this stage, below the 3x cap",
            "execution": "completed 4h candle, next 15m open",
            "costs": "base 5+2 bps; stress 10+5 bps per fill",
        },
        "benchmark": benchmarks,
        "qualifying": qualifying,
        "results": rows,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def three_state_targets(bars, periods, bear_exposure, bull_exposure):
    series = tuple(simple_moving_average(bars, period) for period in periods)
    targets = []
    for index, bar in enumerate(bars):
        values = tuple(stream[index] for stream in series)
        if any(value is None for value in values):
            targets.append(None)
            continue
        bullish = all(left > right for left, right in zip(values, values[1:], strict=False))
        bearish = bar.close < values[-1] and values[0] < values[-1]
        targets.append(bull_exposure if bullish else bear_exposure if bearish else Decimal("1"))
    return tuple(targets)


def qualifies(row, benchmarks):
    full = row["metrics"]["full"]["base"]
    oos = row["metrics"]["oos"]["base"]
    return (
        not full["bankrupt"]
        and full["net_return"] > benchmarks["full"]["net_return"]
        and oos["net_return"] >= benchmarks["oos"]["net_return"]
        and full["max_drawdown"] >= benchmarks["full"]["max_drawdown"]
    )


def score(row):
    metrics = row["metrics"]
    return (
        metrics["research"]["excess_return"]
        + metrics["validation"]["excess_return"]
        + metrics["oos"]["excess_return"]
    )


def markdown(payload):
    lines = [
        "# BTC 三档动态暴露研究",
        "",
        "多头严格排列时加仓；明确熊市状态时降仓；其他中性状态保持 1X。",
        "",
        "## 通过收益、OOS 与回撤门槛的配置",
        "",
        "| 配置 | 全样本 | 超额 | DD | Validation超额 | OOS | OOS超额 | 压力全样本 | 混合Funding |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["qualifying"]:
        full = row["metrics"]["full"]
        validation = row["metrics"]["validation"]
        oos = row["metrics"]["oos"]
        lines.append(
            f"| `{row['id']}` | {pct(full['base']['net_return'])} | "
            f"{pct(full['excess_return'])} | {pct(full['base']['max_drawdown'])} | "
            f"{pct(validation['excess_return'])} | {pct(oos['base']['net_return'])} | "
            f"{pct(oos['excess_return'])} | {pct(full['stress']['net_return'])} | "
            f"{pct(full['hybrid_funding']['net_return'])} |"
        )
    if not payload["qualifying"]:
        lines.append("| 无 | - | - | - | - | - | - | - | - |")
    return "\n".join(lines) + "\n"


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
