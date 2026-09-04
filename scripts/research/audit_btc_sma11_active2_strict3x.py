#!/usr/bin/env python3
"""Audit a fixed SMA11/40 2x total target with a strict 3x futures cap."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import replay_segregated

OUTPUT = Path("reports/experiments/btc_sma11_active2_strict3x/2026-09-03")
ACTIVE = Decimal("2")
FUTURES_CAP = Decimal("3")


def main() -> None:
    spot, futures, daily, target_indices, funding = base.load_hybrid_inputs()
    bars = spot + futures
    targets = base.map_targets(
        len(bars),
        target_indices,
        base.build_targets(daily, fast_period=11, enter_bear_days=2, active=ACTIVE),
    )
    bounds = base.periods(bars[-1].end_ms, spot[-1].end_ms)
    results = {}
    benchmarks = {name: base.benchmark(bars, *period) for name, period in bounds.items()}
    full = None
    for name, period in bounds.items():
        result = replay(bars, targets, funding, *period, record_equity=name == "full")
        results[name] = base.public(result, benchmarks[name], *period)
        if name == "full":
            full = result
    if full is None:
        raise RuntimeError("full replay was not produced")
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=bounds["full"][0]
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
            "candidate": "SMA11/40 enter2-exit1, bear 0X, active total target 2X",
            "execution": "completed UTC daily signal; next 15m open",
            "wallets": "50% spot and 50% isolated USD-M collateral",
            "opening_control": "3X futures-wallet leverage",
            "hard_cap": "<=3X observed futures leverage including intrabar low",
            "costs": "10 bps fee + 5 bps slippage per side; historical Funding",
        },
        "data": {"bars": len(bars), "last": base.iso(bars[-1].end_ms)},
        "results": results,
        "bootstrap": bootstrap,
        "decision": {
            "hard_cap_passed": all(
                row["maximum_intrabar_leverage"] <= 3 and not row["liquidated"]
                for row in results.values()
            ),
            "beats_bh_all_splits": all(row["excess"] > 0 for row in results.values()),
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def replay(bars, targets, funding, start, end, *, record_equity=False):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=FUTURES_CAP,
    )


def render(payload):
    lines = [
        "# BTC SMA11/40 Active 2X Strict 3X Audit",
        "",
        "固定 total target 2X；50% 现货 + 50% 隔离 USD-M 抵押，开仓和盘中有效杠杆均审计 3X。",
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
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += [
        "",
        f"全部分段超过 B&H：{'是' if payload['decision']['beats_bh_all_splits'] else '否'}；"
        f"3X 硬约束通过：{'是' if payload['decision']['hard_cap_passed'] else '否'}。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
