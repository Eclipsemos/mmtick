#!/usr/bin/env python3
"""Research faster bear-exit hysteresis for BTC dynamic exposure."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import benchmark, replay_dynamic_incremental
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

PERIODS = ((24, 48, 96, 192), (25, 50, 100, 200), (26, 52, 104, 208))
BULL_EXPOSURES = tuple(Decimal(value) for value in ("1.25", "1.5", "1.75"))
FUNDING_THRESHOLD = Decimal("0.0001")


def main() -> None:
    output_dir = Path("reports/experiments/btc_bear_hysteresis/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {split: benchmark(bars, start, end) for split, (start, end) in splits.items()}
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    rows = []
    for periods in PERIODS:
        for exit_index in range(4):
            for bull_exposure in BULL_EXPOSURES:
                period_targets = hysteresis_targets(aggregate, periods, exit_index, bull_exposure)
                regime = map_targets_to_source(len(bars), period_targets, ends)
                targets = funding_aware_targets(regime, funding, bull_exposure, FUNDING_THRESHOLD)
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
                    metrics[split] = {
                        "base_return": base.net_return,
                        "stress_return": stress.net_return,
                        "max_drawdown": base.max_drawdown,
                        "excess_return": base.net_return - benchmarks[split]["net_return"],
                        "stress_excess": stress.net_return - benchmarks[split]["net_return"],
                    }
                rows.append(
                    {
                        "id": (
                            f"4h-{'-'.join(map(str, periods))}-bear-exit-sma"
                            f"{periods[exit_index]}-bull{bull_exposure}x"
                        ),
                        "periods": periods,
                        "bear_exit_period": periods[exit_index],
                        "bull_exposure": str(bull_exposure),
                        "metrics": metrics,
                    }
                )
    rows.sort(key=score, reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "bear_entry": "close below slow SMA and fast SMA below slow SMA",
            "bear_exit": "stateful exit when close exceeds selected faster SMA",
            "neutral": "1x",
            "bull": "strict four-SMA ordering with configured exposure",
            "funding": "last known rate <=0.01%; charged only above 1x",
            "ranking": "research and validation stress excess only; OOS excluded",
        },
        "benchmark": benchmarks,
        "results": rows,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def hysteresis_targets(bars, periods, exit_index, bull_exposure):
    series = tuple(simple_moving_average(bars, period) for period in periods)
    in_bear = False
    targets = []
    for index, bar in enumerate(bars):
        values = tuple(stream[index] for stream in series)
        if any(value is None for value in values):
            targets.append(None)
            continue
        bullish = all(left > right for left, right in zip(values, values[1:], strict=False))
        if bullish:
            in_bear = False
        elif in_bear and bar.close > values[exit_index]:
            in_bear = False
        elif not in_bear and bar.close < values[-1] and values[0] < values[-1]:
            in_bear = True
        targets.append(bull_exposure if bullish else Decimal("0") if in_bear else Decimal("1"))
    return tuple(targets)


def score(row):
    metrics = row["metrics"]
    return metrics["research"]["stress_excess"] + metrics["validation"]["stress_excess"]


def markdown(payload):
    lines = [
        "# BTC 熊市快速退出 Challenger",
        "",
        "冻结策略保持不变；本报告探索熊市状态用更快 SMA 恢复到 1X。",
        "",
        "| 配置 | Research压力超额 | Validation压力超额 | OOS压力超额 | 全样本 | 压力全样本 | DD |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"][:20]:
        metrics = row["metrics"]
        lines.append(
            f"| `{row['id']}` | {pct(metrics['research']['stress_excess'])} | "
            f"{pct(metrics['validation']['stress_excess'])} | "
            f"{pct(metrics['oos']['stress_excess'])} | "
            f"{pct(metrics['full']['base_return'])} | "
            f"{pct(metrics['full']['stress_return'])} | "
            f"{pct(metrics['full']['max_drawdown'])} |"
        )
    return "\n".join(lines) + "\n"


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
