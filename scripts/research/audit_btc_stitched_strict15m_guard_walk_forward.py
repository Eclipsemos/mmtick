#!/usr/bin/env python3
"""Yearly walk-forward validation for the causal BTC SMA11 drawdown guard family."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from audit_btc_stitched_strict15m_walk_forward import annual_returns, combine_yearly_targets
from research_btc_stitched_strict15m_drawdown_guard import guarded_targets

OUTPUT = Path("reports/experiments/btc_stitched_strict15m_guard_walk_forward/2026-09-03")
LOOKBACKS = (60, 90, 180)
TRIGGERS = (Decimal("0.15"), Decimal("0.20"))
GUARDS = (Decimal("0.5"), Decimal("0.75"), Decimal("1"), Decimal("1.25"))
COSTS = (
    (Decimal("10"), Decimal("5")),
    (Decimal("20"), Decimal("10")),
)


def main() -> None:
    spot, futures, daily, target_indices, funding = base.load_hybrid_inputs()
    bars = spot + futures
    start = int(base.FUTURES_START.timestamp() * 1000)
    end = bars[-1].end_ms
    baseline = base.build_targets(daily, fast_period=11, enter_bear_days=2, active=Decimal("1.5"))
    candidates = targets_by_candidate(bars, daily, target_indices, baseline)
    selections = select_each_year(bars, candidates, funding, end)
    combined = combine_yearly_targets(bars, candidates, selections)
    result = base.replay(bars, combined, funding, start, end, record_equity=True)
    benchmark = base.benchmark(bars, start, end)
    yearly = annual_returns(bars, result.equity_curve, start)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "base": "daily SMA11/40 enter2 bear-flat, active 1.5X",
            "guard": "completed-daily trailing-high drawdown reduces active exposure",
            "selection": (
                "before each year, maximize worst cumulative excess under 10+5 and 20+10 bps"
            ),
            "execution": "spot daily pre-2020; perpetual next 15m open thereafter",
            "oos": "each annual return is generated after its parameter selection",
        },
        "data": {
            "first": base.iso(bars[0].start_ms),
            "last": base.iso(end),
            "candidate_count": len(candidates),
        },
        "selections": selections,
        "yearly": yearly,
        "full": {
            "strategy_return": result.net_return,
            "benchmark_return": benchmark["net_return"],
            "excess": result.net_return - benchmark["net_return"],
            "max_drawdown": result.max_drawdown,
            "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
            "liquidated": result.liquidated,
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def targets_by_candidate(bars, daily, target_indices, baseline):
    output = {}
    for lookback in LOOKBACKS:
        for trigger in TRIGGERS:
            for guard in GUARDS:
                candidate_id = f"look{lookback}-dd{trigger}-guard{guard}x"
                sparse = guarded_targets(baseline, daily, lookback, trigger, guard)
                output[candidate_id] = base.map_targets(len(bars), target_indices, sparse)
    return output


def select_each_year(bars, targets_by_id, funding, end):
    start = int(base.EVALUATION_START.timestamp() * 1000)
    final_year = datetime.fromtimestamp(end / 1000, UTC).year
    output = {}
    for year in range(2020, final_year + 1):
        train_end = base.utc_ms(year) - 1
        benchmark = base.benchmark(bars, start, train_end)
        scores = []
        for candidate_id, targets in targets_by_id.items():
            excesses = []
            for fee_bps, slippage_bps in COSTS:
                result = base.replay(
                    bars,
                    targets,
                    funding,
                    start,
                    train_end,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                )
                excesses.append(result.net_return - benchmark["net_return"])
            scores.append((min(excesses), candidate_id))
        score, candidate_id = max(scores)
        output[str(year)] = {"candidate": candidate_id, "training_worst_excess": score}
    return output


def render(payload):
    lines = [
        "# BTC Strict-15m Drawdown-Guard Annual Walk-Forward",
        "",
        "每年仅用此前数据选择保护层参数，并将各年的目标连续执行。",
        "",
        "| 年份 | 选择 | 训练最差超额 | 策略 | B&H | 超额 |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for year, selected in payload["selections"].items():
        row = payload["yearly"].get(year)
        if row is None:
            continue
        lines.append(
            f"| {year} | `{selected['candidate']}` | {selected['training_worst_excess']:.2%} | "
            f"{row['strategy_return']:.2%} | {row['benchmark_return']:.2%} | {row['excess']:.2%} |"
        )
    full = payload["full"]
    lines += [
        "",
        f"Full 策略 `{full['strategy_return']:.2%}`，B&H `{full['benchmark_return']:.2%}`，"
        f"超额 `{full['excess']:.2%}`，DD `{full['max_drawdown']:.2%}`，"
        f"峰值杠杆 `{full['maximum_intrabar_leverage']:.3f}X`。",
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
