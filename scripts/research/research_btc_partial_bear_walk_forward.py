#!/usr/bin/env python3
"""Anchored walk-forward for partial-bear BTC exposure challengers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import benchmark, replay_dynamic_incremental
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_partial_bear_exposure import (
    BEAR_EXPOSURES,
    BULL_EXPOSURES,
    FUNDING_THRESHOLD,
    PERIODS,
)
from research_btc_sma_trend import load_funding, load_market
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source


def main() -> None:
    output_dir = Path("reports/experiments/btc_partial_bear_walk_forward/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    candidates = build_candidates(bars, funding, aggregate, ends)
    last_year = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).year
    base_equity = Decimal("1")
    stress_equity = Decimal("1")
    benchmark_equity = Decimal("1")
    rows = []
    for year in range(2022, last_year + 1):
        train_start = utc_ms(2020, 1, 1)
        train_end = utc_ms(year, 1, 1) - 1
        test_start = utc_ms(year, 1, 1)
        test_end = min(utc_ms(year + 1, 1, 1) - 1, bars[-1].end_ms)
        selected, training = select_candidate(bars, funding, candidates, train_start, train_end)
        base = replay_dynamic_incremental(
            bars,
            selected["targets"],
            funding,
            test_start,
            test_end,
            funding_on_excess_only=True,
        )
        stress = replay_dynamic_incremental(
            bars,
            selected["targets"],
            funding,
            test_start,
            test_end,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            funding_on_excess_only=True,
        )
        baseline = benchmark(bars, test_start, test_end)
        base_equity *= Decimal(str(1 + base.net_return))
        stress_equity *= Decimal(str(1 + stress.net_return))
        benchmark_equity *= Decimal(str(1 + baseline["net_return"]))
        rows.append(
            {
                "test_year": year,
                "selected_id": selected["id"],
                "training": training,
                "test_return": base.net_return,
                "stress_return": stress.net_return,
                "benchmark_return": baseline["net_return"],
                "excess_return": base.net_return - baseline["net_return"],
                "max_drawdown": base.max_drawdown,
            }
        )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "training": "anchored from 2020 through prior year",
            "selection": (
                "highest training stress return among candidates whose stress return "
                "beats B&H and base drawdown is no worse than B&H"
            ),
            "test": "next calendar year without parameter changes",
        },
        "years": rows,
        "compound": {
            "strategy_return": float(base_equity - Decimal("1")),
            "stress_return": float(stress_equity - Decimal("1")),
            "benchmark_return": float(benchmark_equity - Decimal("1")),
        },
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def build_candidates(bars, funding, aggregate, ends):
    result = []
    for periods in PERIODS:
        for bear_exposure in BEAR_EXPOSURES:
            for bull_exposure in BULL_EXPOSURES:
                regime = map_targets_to_source(
                    len(bars),
                    three_state_targets(aggregate, periods, bear_exposure, bull_exposure),
                    ends,
                )
                result.append(
                    {
                        "id": (
                            f"4h-{'-'.join(map(str, periods))}-bear{bear_exposure}x-"
                            f"bull{bull_exposure}x"
                        ),
                        "targets": funding_aware_targets(
                            regime, funding, bull_exposure, FUNDING_THRESHOLD
                        ),
                    }
                )
    return result


def select_candidate(bars, funding, candidates, start, end):
    baseline = benchmark(bars, start, end)
    evaluated = []
    for candidate in candidates:
        base = replay_dynamic_incremental(
            bars,
            candidate["targets"],
            funding,
            start,
            end,
            funding_on_excess_only=True,
        )
        stress = replay_dynamic_incremental(
            bars,
            candidate["targets"],
            funding,
            start,
            end,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            funding_on_excess_only=True,
        )
        evaluated.append((candidate, base, stress))
    eligible = [
        item
        for item in evaluated
        if item[2].net_return > baseline["net_return"]
        and item[1].max_drawdown >= baseline["max_drawdown"]
        and not item[1].bankrupt
    ]
    pool = eligible or evaluated
    candidate, base, stress = max(pool, key=lambda item: item[2].net_return)
    return candidate, {
        "eligible_candidates": len(eligible),
        "base_return": base.net_return,
        "stress_return": stress.net_return,
        "benchmark_return": baseline["net_return"],
        "max_drawdown": base.max_drawdown,
    }


def markdown(payload):
    lines = [
        "# BTC 熊市部分底仓年度 Walk-Forward",
        "",
        "| 年份 | 冻结配置 | 策略 | 压力 | B&H | 超额 | DD |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["years"]:
        lines.append(
            f"| {row['test_year']} | `{row['selected_id']}` | "
            f"{pct(row['test_return'])} | {pct(row['stress_return'])} | "
            f"{pct(row['benchmark_return'])} | {pct(row['excess_return'])} | "
            f"{pct(row['max_drawdown'])} |"
        )
    compound = payload["compound"]
    lines += [
        "",
        f"复合策略：{pct(compound['strategy_return'])}；"
        f"压力：{pct(compound['stress_return'])}；"
        f"B&H：{pct(compound['benchmark_return'])}。",
        "",
    ]
    return "\n".join(lines)


def utc_ms(year, month, day):
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
