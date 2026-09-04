#!/usr/bin/env python3
"""Anchored yearly walk-forward for the equal-weight BTC family ensemble."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import benchmark, replay_dynamic_incremental
from research_btc_frozen_ensemble import combine_sparse_targets
from research_btc_funding_aware_walk_forward import (
    build_candidates as build_primary_candidates,
)
from research_btc_funding_aware_walk_forward import select_candidate as select_primary
from research_btc_funding_aware_walk_forward import utc_ms
from research_btc_partial_bear_walk_forward import (
    build_candidates as build_partial_candidates,
)
from research_btc_partial_bear_walk_forward import select_candidate as select_partial
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods

OUTPUT_DIR = Path("reports/experiments/btc_ensemble_walk_forward/2026-09-02")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    primary_candidates = build_primary_candidates(bars, funding, aggregate, ends)
    partial_candidates = build_partial_candidates(bars, funding, aggregate, ends)
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
        primary, primary_training = select_primary(
            bars, funding, primary_candidates, train_start, train_end
        )
        partial, partial_training = select_partial(
            bars, funding, partial_candidates, train_start, train_end
        )
        targets = combine_sparse_targets(primary["targets"], partial["targets"])
        base = replay_dynamic_incremental(
            bars,
            targets,
            funding,
            test_start,
            test_end,
            funding_on_excess_only=True,
        )
        stress = replay_dynamic_incremental(
            bars,
            targets,
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
                "primary_id": primary["id"],
                "partial_id": partial["id"],
                "primary_training": primary_training,
                "partial_training": partial_training,
                "test_return": base.net_return,
                "stress_return": stress.net_return,
                "benchmark_return": baseline["net_return"],
                "excess_return": base.net_return - baseline["net_return"],
                "max_drawdown": base.max_drawdown,
            }
        )
    compound = {
        "strategy_return": float(base_equity - Decimal("1")),
        "stress_return": float(stress_equity - Decimal("1")),
        "benchmark_return": float(benchmark_equity - Decimal("1")),
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "training": "anchored from 2020 through prior calendar year",
            "selection": "each component family independently selects using training only",
            "ensemble": "50% selected primary + 50% selected partial-bear target",
            "test": "next calendar year without parameter changes",
            "engine": "fixed quantities between sparse target changes",
        },
        "years": rows,
        "compound": compound,
        "status": (
            "WALK_FORWARD_PASS"
            if compound["stress_return"] > compound["benchmark_return"]
            else "WALK_FORWARD_FAIL"
        ),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def markdown(payload):
    lines = [
        "# BTC 等权家族年度 Walk-Forward",
        "",
        "主策略家族与熊市部分底仓家族每年分别只用此前数据选参，再等权组合到下一年。",
        "",
        "| 年份 | 主策略 | 熊市底仓 | 组合 | 压力 | B&H | 超额 | DD |",
        "|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["years"]:
        lines.append(
            f"| {row['test_year']} | `{row['primary_id']}` | `{row['partial_id']}` | "
            f"{pct(row['test_return'])} | {pct(row['stress_return'])} | "
            f"{pct(row['benchmark_return'])} | {pct(row['excess_return'])} | "
            f"{pct(row['max_drawdown'])} |"
        )
    compound = payload["compound"]
    lines += [
        "",
        f"复合组合：{pct(compound['strategy_return'])}；"
        f"压力：{pct(compound['stress_return'])}；"
        f"B&H：{pct(compound['benchmark_return'])}。",
        "",
        f"状态：**{payload['status']}**。",
        "",
    ]
    return "\n".join(lines)


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
