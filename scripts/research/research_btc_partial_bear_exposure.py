#!/usr/bin/env python3
"""Research partial BTC exposure during identified bear regimes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import benchmark, replay_dynamic_incremental
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

PERIODS = ((24, 48, 96, 192), (25, 50, 100, 200), (26, 52, 104, 208))
BEAR_EXPOSURES = tuple(Decimal(value) for value in ("0", "0.25", "0.5"))
BULL_EXPOSURES = tuple(Decimal(value) for value in ("1.25", "1.5", "1.75"))
FUNDING_THRESHOLD = Decimal("0.0001")


def main() -> None:
    output_dir = Path("reports/experiments/btc_partial_bear_exposure/2026-09-02")
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
                regime = map_targets_to_source(
                    len(bars),
                    three_state_targets(aggregate, periods, bear_exposure, bull_exposure),
                    ends,
                )
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
                            f"4h-{'-'.join(map(str, periods))}-bear{bear_exposure}x-"
                            f"bull{bull_exposure}x"
                        ),
                        "periods": periods,
                        "bear_exposure": str(bear_exposure),
                        "bull_exposure": str(bull_exposure),
                        "metrics": metrics,
                    }
                )
    rows.sort(key=score, reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "bear": "0x, 0.25x, or 0.5x",
            "neutral": "1x",
            "bull": "1.25x, 1.5x, or 1.75x",
            "funding": "last known rate <=0.01%; charged only above 1x",
            "ranking": "research and validation stress excess only; OOS excluded",
        },
        "benchmark": benchmarks,
        "results": rows,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def score(row):
    metrics = row["metrics"]
    return metrics["research"]["stress_excess"] + metrics["validation"]["stress_excess"]


def markdown(payload):
    lines = [
        "# BTC 熊市部分暴露 Challenger",
        "",
        "冻结策略保持不变；本报告测试熊市保留少量 BTC 底仓。",
        "",
        "| 配置 | Research压力超额 | Validation压力超额 | OOS压力超额 | 全样本 | 压力全样本 | DD |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
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
