#!/usr/bin/env python3
"""Strict audit of the predeclared SMA10/40 1.55x exposure challenger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_sma10_three_state_hysteresis_strict import split_periods
from research_btc_block_bootstrap import paired_daily_log_returns
from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_hysteresis_exposure_cost_grid import hysteresis_targets
from research_btc_sma12_three_state_hysteresis import path_statistics
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT = Path("reports/experiments/btc_sma10_active155_strict/2026-09-03")
ACTIVE = Decimal("1.55")
COSTS = (
    ("low", Decimal("5"), Decimal("2")),
    ("default", Decimal("10"), Decimal("5")),
    ("moderate", Decimal("20"), Decimal("10")),
    ("severe", Decimal("50"), Decimal("25")),
    ("breakpoint", Decimal("75"), Decimal("40")),
)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    dense = hysteresis_targets(daily, 10, 3, 1, ACTIVE)
    targets = map_targets_to_source(len(bars), dense, ends)
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    metrics = {}
    for name, bounds in splits.items():
        metrics[name] = {}
        for label, fee, slip in COSTS:
            result = replay_segregated(
                bars,
                targets,
                funding,
                *bounds,
                spot_cap=Decimal("0.5"),
                maintenance_rate=Decimal("0.02"),
                fee_bps=fee,
                slippage_bps=slip,
                enforce_effective_leverage_cap=True,
                maximum_futures_leverage=Decimal("2.5"),
                record_equity=label == "default" and name == "full",
            )
            metrics[name][label] = public(result, benchmarks[name], bounds)
    full = replay_segregated(
        bars,
        targets,
        funding,
        *splits["full"],
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("2.5"),
        record_equity=True,
    )
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=splits["full"][0]
    )
    path = path_statistics(bars, targets, funding, splits, full, seed=20261955)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE",
        "candidate": {
            "id": "daily-sma10-40-hysteresis-enter3-exit1-active1.55",
            "fast_sma": 10,
            "slow_sma": 40,
            "enter_bear_after_days": 3,
            "exit_bear_after_days": 1,
            "active_exposure": str(ACTIVE),
            "bear_exposure": "0",
        },
        "protocol": {
            "signal": "completed UTC daily candle; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "5/2, 10/5, 20/10, 50/25, 75/40 bps fee/slippage per side",
            "hard_cap": "2.5X opening control; <=3X observed intrabar leverage",
            "selection": "1.55X is a post-grid challenger; OOS is reported but not selected",
        },
        "data": {
            "bars": len(bars),
            "daily_bars": len(daily),
            "last": datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).isoformat(),
        },
        "benchmarks": benchmarks,
        "metrics": metrics,
        "path": path,
        "decision": {
            "beats_bh_all_default_splits": all(
                metrics[name]["default"]["excess"] > 0 for name in splits
            ),
            "beats_bh_all_stress_splits": all(
                metrics[name]["severe"]["excess"] > 0 for name in splits
            ),
            "hard_3x_passed": all(
                metrics[name]["default"]["maximum_intrabar_leverage"] <= 3
                and not metrics[name]["default"]["liquidated"]
                for name in splits
            ),
            "bootstrap_90d_p05_positive": (
                path["bootstrap"]["90d"]["annualized_excess_vs_bh"]["p05"] > 0
            ),
        },
        "local_exposure_sensitivity": {
            "note": "Fine scan around 1.50X was exploratory; not used to claim OOS validation",
            "active_1.50_stress_validation_excess": -0.013420389038145686,
            "active_1.55_stress_validation_excess": metrics["validation"]["severe"]["excess"],
            "active_1.60_stress_validation_excess": 0.3916,
        },
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def public(result, baseline, bounds):
    return {
        "net_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "cagr": (1 + result.net_return) ** (1 / years_between(*bounds)) - 1,
        "max_drawdown": result.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "rebalances": result.rebalances,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def render(payload):
    lines = [
        "# BTC SMA10/40 Active 1.55X Strict Audit",
        "",
        "SMA10/40，连续 3 个熊市日进入 0X，1 个非熊市日恢复；主动暴露固定 1.55X。",
        "",
        "| 区间 | 默认超额 | 50+25超额 | 75+40超额 | 默认CAGR | 默认DD | 杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        m = payload["metrics"][name]
        lines.append(
            f"| {name} | {m['default']['excess']:.2%} | {m['severe']['excess']:.2%} | "
            f"{m['breakpoint']['excess']:.2%} | {m['default']['cagr']:.2%} | "
            f"{m['default']['max_drawdown']:.2%} | "
            f"{m['default']['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += ["", "## Bootstrap", ""]
    for label, row in payload["path"]["bootstrap"].items():
        lines.append(
            f"- {label}: 跑赢 B&H {row['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += ["", "## 决策", "", f"```json\n{json.dumps(payload['decision'], indent=2)}\n```", ""]
    lines.append("状态：**RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE**。")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
