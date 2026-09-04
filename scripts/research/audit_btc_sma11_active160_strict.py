#!/usr/bin/env python3
"""Strict audit of the development-selected BTC SMA11/40 1.60x candidate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_sma10_three_state_hysteresis_strict import split_periods
from research_btc_block_bootstrap import paired_daily_log_returns
from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma12_three_state_hysteresis import path_statistics
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_sma11_active160_strict/2026-09-03")
ACTIVE = Decimal("1.60")
FAST = 11
SLOW = 40
ENTER = 2
EXIT = 1
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
    dense = build_targets(daily)
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
    path = path_statistics(bars, targets, funding, splits, full, seed=20261960)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE",
        "candidate": {
            "id": "daily-sma11-40-hysteresis-enter2-exit1-active1.60",
            "fast_sma": FAST,
            "slow_sma": SLOW,
            "enter_bear_after_days": ENTER,
            "exit_bear_after_days": EXIT,
            "active_exposure": str(ACTIVE),
            "bear_exposure": "0",
        },
        "protocol": {
            "signal": "completed UTC daily candle; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "5/2, 10/5, 20/10, 50/25, 75/40 bps fee/slippage per side",
            "hard_cap": "2.5X opening control; <=3X observed intrabar leverage",
            "selection": (
                "selected from a predeclared 54-point development neighborhood; OOS excluded"
            ),
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
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
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def build_targets(daily):
    fast = simple_moving_average(daily, FAST)
    slow = simple_moving_average(daily, SLOW)
    state = None
    bear_count = recovery_count = 0
    output = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            output.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= ENTER:
            state = "bear"
        elif state == "bear" and recovery_count >= EXIT:
            state = "active"
        output.append(Decimal("0") if state == "bear" else ACTIVE)
    return tuple(output)


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
        "# BTC SMA11/40 Active 1.60X Strict Audit",
        "",
        "SMA11/40，连续 2 个熊市日进入 0X，1 个非熊市日恢复；主动暴露固定 1.60X。",
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


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
