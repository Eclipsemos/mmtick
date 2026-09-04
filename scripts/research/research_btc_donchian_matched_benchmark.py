#!/usr/bin/env python3
"""Screen daily Donchian BTC breakouts against a matched continuous-1.5x benchmark."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from audit_btc_sma11_levered_benchmark import constant_targets

from mastermind_tick.bar_research import donchian_targets

OUTPUT = Path("reports/experiments/btc_donchian_matched_benchmark/2026-09-03")
WINDOWS = ((20, 10), (55, 20), (100, 50))
ACTIVE = Decimal("1.5")


def main() -> None:
    spot, futures, daily, target_indices, funding = base.load_hybrid_inputs()
    bars = spot + futures
    bounds = base.periods(bars[-1].end_ms, spot[-1].end_ms)
    matched_targets = constant_targets(len(bars), ACTIVE)
    matched = {
        name: base.replay(bars, matched_targets, funding, *period)
        for name, period in bounds.items()
    }
    rows = []
    for entry_window, exit_window in WINDOWS:
        targets = map_breakout_targets(daily, len(bars), target_indices, entry_window, exit_window)
        metrics = {}
        for name in ("research", "validation"):
            metrics[name] = public(
                base.replay(bars, targets, funding, *bounds[name]), matched[name]
            )
        rows.append(
            {
                "id": f"daily-donchian-{entry_window}-{exit_window}-long-only-active1.5x",
                "entry_window": entry_window,
                "exit_window": exit_window,
                "metrics": metrics,
                "development_min_matched_excess": min(
                    value["matched_excess"] for value in metrics.values()
                ),
            }
        )
    rows.sort(key=lambda row: row["development_min_matched_excess"], reverse=True)
    qualifying = [
        row
        for row in rows
        if row["development_min_matched_excess"] > 0
        and all(
            not row["metrics"][name]["liquidated"]
            and row["metrics"][name]["maximum_intrabar_leverage"] <= 3
            for name in ("research", "validation")
        )
    ]
    for row in qualifying:
        targets = map_breakout_targets(
            daily,
            len(bars),
            target_indices,
            row["entry_window"],
            row["exit_window"],
        )
        for name in ("oos", "full"):
            row["metrics"][name] = public(
                base.replay(bars, targets, funding, *bounds[name]), matched[name]
            )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / NOT_PROMOTED",
        "protocol": {
            "family": "daily long-only Donchian breakout with channel exit and 1.5X active target",
            "windows": WINDOWS,
            "benchmark": (
                "continuous 1.5X BTC using identical wallets, costs, Funding, and controls"
            ),
            "selection": "Research and Validation only; OOS unread unless development passes",
            "execution": "completed daily signal; next 15m open",
            "hard_cap": "2X futures opening control and <=3X observed effective leverage",
        },
        "data": {"last": base.iso(bars[-1].end_ms), "candidate_count": len(rows)},
        "results": rows,
        "development_qualifying_count": len(qualifying),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def map_breakout_targets(daily, source_count, target_indices, entry_window: int, exit_window: int):
    raw = donchian_targets(daily, entry_window, exit_window, "long_only")
    sparse = tuple(None if value is None else ACTIVE if value else Decimal("0") for value in raw)
    return base.map_targets(source_count, target_indices, sparse)


def public(result, matched) -> dict:
    return {
        "net_return": result.net_return,
        "matched_return": matched.net_return,
        "matched_excess": result.net_return - matched.net_return,
        "max_drawdown": result.max_drawdown,
        "matched_drawdown": matched.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
    }


def render(payload) -> str:
    lines = [
        "# BTC Daily Donchian Breakout vs Matched 1.5X Benchmark",
        "",
        "日线 Donchian 突破与持续 1.5X BTC 使用相同的钱包、成本、Funding 与盘中保护。",
        "",
        "| 配置 | R相对1.5X | V相对1.5X | 开发最差 | R DD | V DD |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        research = row["metrics"]["research"]
        validation = row["metrics"]["validation"]
        lines.append(
            f"| `{row['id']}` | {research['matched_excess']:.2%} | "
            f"{validation['matched_excess']:.2%} | "
            f"{row['development_min_matched_excess']:.2%} | "
            f"{research['max_drawdown']:.2%} | {validation['max_drawdown']:.2%} |"
        )
    lines += [
        "",
        "开发期合格成员："
        f"{payload['development_qualifying_count']} / {payload['data']['candidate_count']}。",
        "OOS 只对开发期合格成员计算。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
