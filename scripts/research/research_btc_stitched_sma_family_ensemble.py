#!/usr/bin/env python3
"""Evaluate a pre-declared equal-weight ensemble of the BTC SMA neighborhood."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap

OUTPUT = Path("reports/experiments/btc_stitched_sma_family_ensemble/2026-09-03")
FAST_PERIODS = (8, 9, 10, 11, 12)
ENTER_DAYS = (1, 2, 3)
ACTIVE_EXPOSURES = (Decimal("1.25"), Decimal("1.5"))


def main() -> None:
    spot, futures, daily, target_indices, funding = base.load_hybrid_inputs()
    bars = spot + futures
    bounds = base.periods(bars[-1].end_ms, spot[-1].end_ms)
    member_targets = build_member_targets(daily, len(bars), target_indices)
    ensemble = average_targets(member_targets)
    benchmarks = {name: base.benchmark(bars, *period) for name, period in bounds.items()}
    results = {}
    full_result = None
    for name, period in bounds.items():
        result = base.replay(
            bars,
            ensemble,
            funding,
            *period,
            record_equity=name == "full",
        )
        results[name] = base.public(result, benchmarks[name], *period)
        if name == "full":
            full_result = result
    if full_result is None:
        raise RuntimeError("full replay was not produced")
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full_result.equity_curve, 100_000.0, start_ms=bounds["full"][0]
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20260903 + block,
        )
        for block in (7, 30, 90, 180, 365)
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "family": (
                "all 30 pre-declared SMA fast 8-12 / slow 40 / enter 1-3 / active 1.25 or 1.5"
            ),
            "weights": "fixed equal 1/30; no member or weight selection",
            "signal": "completed UTC daily SMA; bear-flat; no future data",
            "execution": "spot daily pre-2020; perpetual next 15m open",
            "costs": "10 bps fee + 5 bps slippage per side; historical Funding",
            "wallets": "50% spot and 50% isolated USD-M collateral",
            "hard_cap": "2X futures opening control and <=3X observed effective leverage",
            "oos_policy": "all members and weights are fixed before reading OOS results",
        },
        "data": {
            "spot_daily_bars": len(spot),
            "perpetual_15m_bars": len(futures),
            "last": base.iso(bars[-1].end_ms),
            "member_count": len(member_targets),
        },
        "results": results,
        "bootstrap": bootstrap,
        "decision": decision(results, bootstrap),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def build_member_targets(daily, source_count, target_indices):
    members = []
    for fast in FAST_PERIODS:
        for enter in ENTER_DAYS:
            for active in ACTIVE_EXPOSURES:
                sparse = base.build_targets(
                    daily,
                    fast_period=fast,
                    enter_bear_days=enter,
                    active=active,
                )
                members.append(base.map_targets(source_count, target_indices, sparse))
    return tuple(members)


def average_targets(members):
    if not members or len({len(member) for member in members}) != 1:
        raise ValueError("member target streams must be non-empty and equally sized")
    output = []
    previous = None
    for values in zip(*members, strict=True):
        known = [Decimal(value) for value in values if value is not None]
        if not known:
            output.append(None)
            continue
        target = sum(known, Decimal("0")) / Decimal(len(known))
        if target != previous:
            output.append(target)
            previous = target
        else:
            output.append(None)
    return tuple(output)


def decision(results, bootstrap):
    return {
        "beats_bh_all_splits": all(row["excess"] > 0 for row in results.values()),
        "hard_3x_passed": all(
            row["maximum_intrabar_leverage"] <= 3 and not row["liquidated"]
            for row in results.values()
        ),
        "bootstrap_90d_p05_positive": bootstrap["90d"]["annualized_excess_vs_bh"]["p05"] > 0,
        "bootstrap_365d_p05_positive": bootstrap["365d"]["annualized_excess_vs_bh"]["p05"] > 0,
    }


def render(payload):
    lines = [
        "# BTC Stitched SMA Family Ensemble",
        "",
        "固定等权组合全部 30 个预先定义的 SMA 邻域成员；不按 OOS 或历史收益选成员。",
        "",
        "| 区间 | 策略 | B&H | 超额 | CAGR | DD | 最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["results"].items():
        lines.append(
            f"| {name} | {row['strategy_return']:.2%} | {row['benchmark_return']:.2%} | "
            f"{row['excess']:.2%} | {row['strategy_cagr']:.2%} | "
            f"{row['strategy_drawdown']:.2%} | {row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += ["", "## Bootstrap", ""]
    for label, row in payload["bootstrap"].items():
        lines.append(
            f"- {label}: 跑赢 B&H {row['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}；"
            f"收益与 DD 同胜 {row['probability_beats_return_and_drawdown']:.2%}。"
        )
    decision = payload["decision"]
    lines += [
        "",
        f"固定家族在全部分段超过 B&H：{'是' if decision['beats_bh_all_splits'] else '否'}。",
        f"3X 硬约束通过：{'是' if decision['hard_3x_passed'] else '否'}。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
