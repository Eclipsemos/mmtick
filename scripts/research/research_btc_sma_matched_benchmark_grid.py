#!/usr/bin/env python3
"""Screen a pre-declared SMA family against a matched continuous-1.5x benchmark."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from audit_btc_sma11_levered_benchmark import constant_targets

OUTPUT = Path("reports/experiments/btc_sma_matched_benchmark_grid/2026-09-03")
FAST_PERIODS = (8, 9, 10, 11, 12)
ENTER_DAYS = (1, 2, 3)
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
    for fast in FAST_PERIODS:
        for enter in ENTER_DAYS:
            targets = base.map_targets(
                len(bars),
                target_indices,
                base.build_targets(daily, fast_period=fast, enter_bear_days=enter, active=ACTIVE),
            )
            metrics = {}
            for name in ("research", "validation"):
                result = base.replay(bars, targets, funding, *bounds[name])
                metrics[name] = public(result, matched[name])
            row = {
                "id": f"sma{fast}/40-enter{enter}-exit1-active1.5x",
                "fast": fast,
                "enter": enter,
                "metrics": metrics,
                "development_min_matched_excess": min(
                    metrics[name]["matched_excess"] for name in metrics
                ),
            }
            rows.append(row)
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
        targets = base.map_targets(
            len(bars),
            target_indices,
            base.build_targets(
                daily,
                fast_period=row["fast"],
                enter_bear_days=row["enter"],
                active=ACTIVE,
            ),
        )
        for name in ("oos", "full"):
            row["metrics"][name] = public(
                base.replay(bars, targets, funding, *bounds[name]), matched[name]
            )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / NOT_PROMOTED",
        "protocol": {
            "family": ("SMA fast 8-12 / slow 40 / enter 1-3 / exit 1 / active 1.5X / bear 0X"),
            "benchmark": (
                "continuous 1.5X BTC with identical 50/50 wallets, costs, Funding, and controls"
            ),
            "development": (
                "Research and Validation only; OOS remains unread unless development passes"
            ),
            "execution": "completed daily signal; next 15m open",
            "hard_cap": "2X futures opening control and <=3X observed effective leverage",
        },
        "data": {"last": base.iso(bars[-1].end_ms), "candidate_count": len(rows)},
        "matched_benchmark": {name: public(result, result) for name, result in matched.items()},
        "results": rows,
        "development_qualifying_count": len(qualifying),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


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
        "# BTC SMA Family vs Matched 1.5X Benchmark",
        "",
        "仅用 Research 与 Validation 对预先定义的 SMA 家族筛选，"
        "并直接比较同一回放模型下的持续 1.5X BTC。",
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
        "OOS 只对开发期合格成员计算；没有合格成员时不读取 OOS，以避免逆向选择。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
