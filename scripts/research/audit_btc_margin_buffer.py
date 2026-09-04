#!/usr/bin/env python3
"""Audit approximate cross-margin liquidation buffers for frozen BTC candidates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import replay_dynamic_incremental
from research_btc_frozen_ensemble import build_targets, combine_sparse_targets
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar

OUTPUT_DIR = Path("reports/experiments/btc_margin_buffer/2026-09-02")
MAINTENANCE_RATES = (Decimal("0.004"), Decimal("0.005"), Decimal("0.01"), Decimal("0.02"))
INITIAL_EQUITY = Decimal("100000")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    targets = build_targets(bars, funding)
    targets["equal_weight_ensemble"] = combine_sparse_targets(
        targets["primary"], targets["partial_bear_challenger"]
    )
    full_start, full_end = split_periods(bars)["full"]
    results = {}
    for candidate_id, candidate_targets in targets.items():
        result = replay_dynamic_incremental(
            bars,
            candidate_targets,
            funding,
            full_start,
            full_end,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            funding_on_excess_only=True,
            record_risk=True,
        )
        results[candidate_id] = audit_candidate(bars, result, MAINTENANCE_RATES)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "margin_model": (
                "approximate cross-margin check: account equity must exceed maintenance "
                "rate times linear-futures notional"
            ),
            "price_path": "intrabar low for long positions",
            "rates": [str(rate) for rate in MAINTENANCE_RATES],
            "costs": "10 bps fee + 5 bps slippage; historical funding above 1x",
            "limitation": (
                "not an exchange liquidation simulator; Binance tiered maintenance, mark "
                "price, liquidation fee, ADL and isolated/cross settings require live account "
                "specification"
            ),
        },
        "data": {
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
            "bars": len(bars),
        },
        "results": results,
        "decision": {
            "historical_bankruptcy_seen": any(
                item["historical_bankruptcy"] for item in results.values()
            ),
            "requires_exchange_tier_confirmation": True,
            "live_approval": False,
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def audit_candidate(bars, result, rates):
    if not result.risk_curve:
        raise ValueError("risk curve is required")
    rows = [
        (
            timestamp,
            Decimal(str(intrabar_equity)),
            Decimal(str(futures_notional)),
            Decimal(str(total_notional)),
            Decimal(str(exposure)),
        )
        for timestamp, intrabar_equity, futures_notional, total_notional, exposure in (
            result.risk_curve
        )
    ]
    leveraged_rows = [row for row in rows if row[2] > 0]
    if not leveraged_rows:
        raise ValueError("candidate has no leveraged observations")
    summary = {
        "historical_bankruptcy": result.bankrupt,
        "maximum_exposure": str(max(row[4] for row in rows)),
        "minimum_equity_ratio": float(min(row[1] / INITIAL_EQUITY for row in rows)),
        "rates": {},
    }
    for rate in rates:
        buffers = [
            equity - rate * abs(futures_notional)
            for _, equity, futures_notional, _, _ in leveraged_rows
        ]
        minimum = min(buffers)
        minimum_row = leveraged_rows[buffers.index(minimum)]
        near_count = sum(buffer <= Decimal("0") for buffer in buffers)
        maintenance_utilizations = [
            rate * abs(futures_notional) / equity
            for _, equity, futures_notional, _, _ in leveraged_rows
        ]
        headrooms = [
            liquidation_decline_headroom(equity, futures_notional, total_notional, rate)
            for _, equity, futures_notional, total_notional, _ in leveraged_rows
        ]
        minimum_headroom = min(headrooms)
        headroom_row = leveraged_rows[headrooms.index(minimum_headroom)]
        summary["rates"][str(rate)] = {
            "minimum_buffer": float(minimum),
            "minimum_buffer_pct_initial_equity": float(minimum / INITIAL_EQUITY),
            "at_or_below_liquidation_buffer_bars": near_count,
            "minimum_at": iso(minimum_row[0]),
            "minimum_exposure": str(minimum_row[4]),
            "maximum_maintenance_utilization": float(max(maintenance_utilizations)),
            "minimum_additional_price_decline_to_maintenance": float(minimum_headroom),
            "headroom_minimum_at": iso(headroom_row[0]),
            "headroom_exposure": str(headroom_row[4]),
        }
    return summary


def liquidation_decline_headroom(equity, futures_notional, total_notional, rate):
    denominator = total_notional - rate * futures_notional
    if denominator <= 0:
        raise ValueError("invalid liquidation headroom denominator")
    return (equity - rate * futures_notional) / denominator


def markdown(payload):
    lines = [
        "# BTC 杠杆维持保证金缓冲审计",
        "",
        "按近似交叉保证金模型检查账户权益是否高于维持保证金。这是风险筛查，不是交易所清算价保证。",
        "",
        "| 候选 | 最大敞口 | 费率 | 最大保证金占用 | 最小继续下跌空间 | 缓冲≤0K线 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for candidate_id, result in payload["results"].items():
        for rate, item in result["rates"].items():
            lines.append(
                f"| `{candidate_id}` | {result['maximum_exposure']}X | {rate} | "
                f"{item['maximum_maintenance_utilization']:.2%} | "
                f"{item['minimum_additional_price_decline_to_maintenance']:.2%} | "
                f"{item['at_or_below_liquidation_buffer_bars']} |"
            )
    lines += [
        "",
        "近似公式：futures maintenance = maintenance_rate × (exposure−1) × equity；"
        "实际交易所还会使用标记价格、分层维持保证金、清算费和ADL。",
        "任何真实部署前必须按账户实际保证金模式和名义金额重新计算。",
        "",
    ]
    return "\n".join(lines)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
