#!/usr/bin/env python3
"""Screen pre-declared daily mean-reversion rules against continuous 1.5x BTC."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from audit_btc_sma11_levered_benchmark import constant_targets

from mastermind_tick.bar_research import bollinger_reversion_targets, rsi_reversion_targets

OUTPUT = Path("reports/experiments/btc_mean_reversion_matched_benchmark/2026-09-03")
BOLLINGER = ((20, 1.5), (20, 2.0), (50, 1.5), (50, 2.0))
RSI = ((14, 30), (14, 35), (21, 30), (21, 35))
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
    candidates = candidate_signals(daily)
    rows = []
    for identifier, family, raw_targets in candidates:
        targets = scale_and_map(raw_targets, len(bars), target_indices)
        metrics = {
            name: public(base.replay(bars, targets, funding, *bounds[name]), matched[name])
            for name in ("research", "validation")
        }
        rows.append(
            {
                "id": identifier,
                "family": family,
                "metrics": metrics,
                "development_min_matched_excess": min(
                    metric["matched_excess"] for metric in metrics.values()
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
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / NOT_PROMOTED",
        "protocol": {
            "families": "daily long-only Bollinger and RSI mean reversion; exit to cash at center",
            "benchmark": (
                "continuous 1.5X BTC with identical wallets, costs, Funding, and controls"
            ),
            "selection": (
                "Research and Validation only; OOS remains unread without a qualifying rule"
            ),
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


def candidate_signals(daily):
    output = []
    for period, deviation in BOLLINGER:
        output.append(
            (
                f"daily-bollinger-{period}-{deviation:g}-long-only-active1.5x",
                "bollinger",
                bollinger_reversion_targets(daily, period, deviation, "long_only"),
            )
        )
    for period, lower in RSI:
        output.append(
            (
                f"daily-rsi-{period}-{lower}-long-only-active1.5x",
                "rsi",
                rsi_reversion_targets(daily, period, lower, 100 - lower, "long_only"),
            )
        )
    return tuple(output)


def scale_and_map(raw_targets, source_count, target_indices):
    sparse = tuple(
        None if value is None else ACTIVE if value else Decimal("0") for value in raw_targets
    )
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
        "# BTC Daily Mean Reversion vs Matched 1.5X Benchmark",
        "",
        "预先定义的 Bollinger 和 RSI 日线回归规则均与持续 1.5X BTC 使用相同回放模型。",
        "",
        "| 配置 | 家族 | R相对1.5X | V相对1.5X | 开发最差 | R DD | V DD |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        research = row["metrics"]["research"]
        validation = row["metrics"]["validation"]
        lines.append(
            f"| `{row['id']}` | {row['family']} | {research['matched_excess']:.2%} | "
            f"{validation['matched_excess']:.2%} | {row['development_min_matched_excess']:.2%} | "
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
