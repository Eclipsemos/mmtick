#!/usr/bin/env python3
"""Compare the invalid constant-exposure replay with fixed-quantity replay."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import (
    as_dict,
    replay_dynamic_constant_exposure_legacy,
    replay_dynamic_incremental,
)
from research_btc_frozen_ensemble import build_targets, combine_sparse_targets
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar

OUTPUT_DIR = Path("reports/experiments/btc_rebalance_model_audit/2026-09-02")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    targets = build_targets(bars, funding)
    targets["equal_weight_ensemble"] = combine_sparse_targets(
        targets["primary"], targets["partial_bear_challenger"]
    )
    splits = split_periods(bars)
    results = {}
    for candidate_id, candidate_targets in targets.items():
        results[candidate_id] = {}
        for split in ("full", "oos"):
            start, end = splits[split]
            results[candidate_id][split] = compare(bars, candidate_targets, funding, start, end)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "finding": (
            "legacy replay implicitly restored target leverage every 15m bar without "
            "charging the required rebalance; fixed replay holds quantities until a sparse "
            "target change"
        ),
        "deterministic_example": {
            "path": "inherited 2x exposure; price 100 -> 110 -> 121",
            "legacy_return": 0.44,
            "fixed_quantity_return": 0.42,
        },
        "results": results,
        "decision": {
            "legacy_candidate_approval_allowed": False,
            "authoritative_engine": "fixed_quantity",
            "reports_recomputed": True,
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def compare(bars, targets, funding, start, end):
    output = {}
    for label, fee, slippage in (
        ("base", Decimal("5"), Decimal("2")),
        ("stress", Decimal("10"), Decimal("5")),
    ):
        fixed = replay_dynamic_incremental(
            bars,
            targets,
            funding,
            start,
            end,
            fee_bps=fee,
            slippage_bps=slippage,
            funding_on_excess_only=True,
        )
        legacy = replay_dynamic_constant_exposure_legacy(
            bars,
            targets,
            funding,
            start,
            end,
            fee_bps=fee,
            slippage_bps=slippage,
            funding_on_excess_only=True,
        )
        output[label] = {
            "fixed_quantity": as_dict(fixed),
            "legacy_constant_exposure": as_dict(legacy),
            "legacy_minus_fixed_return": legacy.net_return - fixed.net_return,
        }
    return output


def markdown(payload):
    lines = [
        "# BTC 免费再平衡模型审计",
        "",
        "旧增量引擎每根15m K线按目标杠杆复合收益，但只在信号变化时收费，"
        "等价于免费连续再平衡。新权威模型只在稀疏目标变化时交易，期间持仓数量固定。",
        "",
        "确定性复现：继承2X仓位、价格 `100 -> 110 -> 121`，旧模型+44%，固定数量应为+42%。",
        "",
        "| 候选 | 分段 | 成本 | 固定数量 | 旧模型 | 旧减新 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for candidate_id, splits in payload["results"].items():
        for split, costs in splits.items():
            for cost_id, result in costs.items():
                lines.append(
                    f"| `{candidate_id}` | {split} | {cost_id} | "
                    f"{pct(result['fixed_quantity']['net_return'])} | "
                    f"{pct(result['legacy_constant_exposure']['net_return'])} | "
                    f"{pct(result['legacy_minus_fixed_return'])} |"
                )
    lines += [
        "",
        "旧模型不再允许用于候选批准。所有冻结指标、Walk-Forward、滚动窗口、"
        "Bootstrap和归因报告均以固定数量模型为准。",
        "",
    ]
    return "\n".join(lines)


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
