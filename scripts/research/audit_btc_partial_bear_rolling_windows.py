#!/usr/bin/env python3
"""Compare partial-bear challengers across rolling windows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_frozen_rolling_windows import WINDOWS, evaluate_windows, summarize
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

CANDIDATES = (
    ((25, 50, 100, 200), Decimal("0.5"), Decimal("1.75")),
    ((26, 52, 104, 208), Decimal("0.5"), Decimal("1.75")),
)
FUNDING_THRESHOLD = Decimal("0.0001")


def main() -> None:
    output_dir = Path("reports/experiments/btc_partial_bear_rolling/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    results = {}
    for periods, bear_exposure, bull_exposure in CANDIDATES:
        regime = map_targets_to_source(
            len(bars),
            three_state_targets(aggregate, periods, bear_exposure, bull_exposure),
            ends,
        )
        targets = funding_aware_targets(regime, funding, bull_exposure, FUNDING_THRESHOLD)
        candidate_id = f"4h-{'-'.join(map(str, periods))}-bear{bear_exposure}x-bull{bull_exposure}x"
        results[candidate_id] = {}
        for label, days in WINDOWS:
            rows = evaluate_windows(bars, funding, targets, days)
            results[candidate_id][label] = {
                "summary": summarize(rows),
                "rows": rows,
            }
            print(f"{candidate_id} {label}: {len(rows)}", flush=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "selection": "two predeclared neighboring challengers",
            "window_step_days": 30,
            "costs": "base and stress costs with funding on exposure above 1x",
        },
        "candidates": results,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def markdown(payload):
    lines = [
        "# BTC 熊市部分底仓滚动窗口审计",
        "",
        "| 候选 | 窗口 | 基准胜率 | 压力胜率 | 收益+DD | 压力收益+DD | "
        "压力中位超额 | 压力最差超额 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for candidate_id, windows in payload["candidates"].items():
        for label, result in windows.items():
            value = result["summary"]
            lines.append(
                f"| `{candidate_id}` | {label} | "
                f"{pct(value['base_return_win_rate'])} | "
                f"{pct(value['stress_return_win_rate'])} | "
                f"{pct(value['base_return_and_drawdown_win_rate'])} | "
                f"{pct(value['stress_return_and_drawdown_win_rate'])} | "
                f"{pct(value['median_stress_excess'])} | "
                f"{pct(value['worst_stress_excess'])} |"
            )
    return "\n".join(lines) + "\n"


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
