#!/usr/bin/env python3
"""Evaluate the frozen funding-aware BTC candidate on forward-only data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import (
    as_dict,
    benchmark,
    replay_dynamic_incremental,
)
from research_btc_frozen_ensemble import combine_sparse_targets
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

FORWARD_START_MS = int(datetime(2026, 9, 3, tzinfo=UTC).timestamp() * 1000)
FUNDING_THRESHOLD = Decimal("0.0001")
CANDIDATES = (
    {
        "id": "primary",
        "status": "FORWARD_TESTING",
        "periods": (26, 52, 104, 208),
        "bear_exposure": Decimal("0"),
        "bull_exposure": Decimal("1.5"),
    },
    {
        "id": "partial_bear_challenger",
        "status": "FORWARD_OBSERVATION_CONTROL",
        "periods": (25, 50, 100, 200),
        "bear_exposure": Decimal("0.5"),
        "bull_exposure": Decimal("1.75"),
    },
)


def main() -> None:
    output_dir = Path("reports/experiments/btc_funding_aware_forward/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    payload = base_payload(bars)
    if bars[-1].end_ms < FORWARD_START_MS:
        payload["status"] = "AWAITING_FORWARD_DATA"
        write(output_dir, payload)
        return
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    end_ms = bars[-1].end_ms
    baseline = benchmark(bars, FORWARD_START_MS, end_ms)
    target_sets = build_target_sets(bars, funding, aggregate, ends)
    results = {}
    for candidate_id, targets in target_sets.items():
        base = replay_dynamic_incremental(
            bars,
            targets,
            funding,
            FORWARD_START_MS,
            end_ms,
            funding_on_excess_only=True,
        )
        stress = replay_dynamic_incremental(
            bars,
            targets,
            funding,
            FORWARD_START_MS,
            end_ms,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            funding_on_excess_only=True,
        )
        results[candidate_id] = {
            "strategy": as_dict(base),
            "stress": as_dict(stress),
            "excess_return": base.net_return - baseline["net_return"],
        }
    payload.update(
        {
            "status": "FORWARD_OBSERVATION_ACTIVE",
            "forward_end": iso(end_ms),
            "source_bars": sum(bar.start_ms >= FORWARD_START_MS for bar in bars),
            "buy_and_hold": baseline,
            "results": results,
        }
    )
    write(output_dir, payload)


def build_target_sets(bars, funding, aggregate, ends):
    target_sets = {}
    for candidate in CANDIDATES:
        regime = map_targets_to_source(
            len(bars),
            three_state_targets(
                aggregate,
                candidate["periods"],
                candidate["bear_exposure"],
                candidate["bull_exposure"],
            ),
            ends,
        )
        targets = funding_aware_targets(
            regime, funding, candidate["bull_exposure"], FUNDING_THRESHOLD
        )
        target_sets[candidate["id"]] = targets
    target_sets["equal_weight_ensemble"] = combine_sparse_targets(
        target_sets["primary"], target_sets["partial_bear_challenger"]
    )
    return target_sets


def base_payload(bars):
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": None,
        "frozen_candidates": [
            {
                "id": candidate["id"],
                "timeframe": "4h",
                "sma_periods": candidate["periods"],
                "bear_exposure": str(candidate["bear_exposure"]),
                "neutral_exposure": "1",
                "bull_exposure": str(candidate["bull_exposure"]),
                "funding_threshold": str(FUNDING_THRESHOLD),
                "status": candidate["status"],
                "position_model": "fixed quantities between sparse target changes",
            }
            for candidate in CANDIDATES
        ]
        + [
            {
                "id": "equal_weight_ensemble",
                "status": "FORWARD_TESTING_SECONDARY",
                "construction": "50% primary + 50% partial_bear_challenger",
                "weights": {"primary": "0.5", "partial_bear_challenger": "0.5"},
                "maximum_exposure": "1.625",
                "position_model": "fixed quantities between sparse target changes",
            }
        ],
        "forward_start": iso(FORWARD_START_MS),
        "latest_available_bar": iso(bars[-1].end_ms),
        "parameter_changes_allowed": False,
    }


def write(output_dir, payload):
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    status = payload["status"]
    lines = [
        "# BTC Funding-aware 冻结策略前向观察",
        "",
        f"状态：**{status}**。",
        "",
        f"前向起点：{payload['forward_start']}；最新可用数据：{payload['latest_available_bar']}。",
        "",
    ]
    if status == "FORWARD_OBSERVATION_ACTIVE":
        lines += [
            "| 候选 | 收益 | 压力收益 | 最大回撤 | 超额 |",
            "|---|---:|---:|---:|---:|",
        ]
        for candidate_id, result in payload["results"].items():
            lines.append(
                f"| `{candidate_id}` | {pct(result['strategy']['net_return'])} | "
                f"{pct(result['stress']['net_return'])} | "
                f"{pct(result['strategy']['max_drawdown'])} | "
                f"{pct(result['excess_return'])} |"
            )
        lines += [
            "",
            f"BTC B&H：{pct(payload['buy_and_hold']['net_return'])}；"
            f"回撤：{pct(payload['buy_and_hold']['max_drawdown'])}。",
            "",
        ]
    else:
        lines += ["尚无 2026-09-03 之后的完整数据。参数已冻结，不得提前调整。", ""]
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(output_dir / "README.md")


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
