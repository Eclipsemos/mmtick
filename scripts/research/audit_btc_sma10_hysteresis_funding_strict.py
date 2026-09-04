#!/usr/bin/env python3
"""Audit SMA10/40 hysteresis with a fixed positive-funding leverage gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_sma10_three_state_hysteresis_strict import (
    hysteresis_targets,
    public,
    replay,
    split_periods,
)
from research_btc_collateral_architecture import replay_segregated
from research_btc_sma12_three_state_hysteresis import path_statistics
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT = Path("reports/experiments/btc_sma10_hysteresis_funding_strict/2026-09-03")
FUNDING_THRESHOLD = Decimal("0.0001")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    raw = map_targets_to_source(len(bars), hysteresis_targets(daily), ends)
    targets = funding_gate(raw, funding, FUNDING_THRESHOLD)
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    metrics = {}
    full_result = None
    for name, bounds in splits.items():
        result = replay(bars, targets, funding, *bounds, record=name == "full")
        metrics[name] = public(result, benchmarks[name], bounds)
        if name == "full":
            full_result = result
    if full_result is None:
        raise RuntimeError("full replay missing")
    path = path_statistics(bars, targets, funding, splits, full_result, seed=20261800)
    stress = stress_sensitivity(bars, targets, funding, splits)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": {
            "id": "daily-sma10-40-hysteresis-enter2-exit1-active1.5-funding01",
            "base_signal": "SMA10/40; bear after 2 days, recover after 1 day",
            "funding_threshold": str(FUNDING_THRESHOLD),
            "funding_action": (
                "active target 1.5X becomes 1.0X when latest known funding exceeds threshold"
            ),
            "bear_target": "0X",
        },
        "protocol": {
            "signal": "completed UTC daily candle; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "hard_cap": "2.5X opening control; <=3X observed intrabar leverage",
            "causality": "funding gate uses only funding events known by each 15m bar",
            "selection": "fixed funding threshold from prior research; no OOS retuning",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "metrics": metrics,
        "path": path,
        "stress_sensitivity": stress,
        "decision": {
            "beats_bh_all_splits": all(row["excess"] > 0 for row in metrics.values()),
            "hard_3x_passed": all(
                row["maximum_intrabar_leverage"] <= 3 and not row["liquidated"]
                for row in metrics.values()
            ),
            "bootstrap_90d_p05_positive": (
                path["bootstrap"]["90d"]["annualized_excess_vs_bh"]["p05"] > 0
            ),
            "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        },
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def funding_gate(targets, funding, threshold):
    if len(targets) != len(funding):
        raise ValueError("target and funding streams must have equal lengths")
    active = Decimal("0")
    latest = Decimal("0")
    output = []
    for target, events in zip(targets, funding, strict=True):
        if target is not None:
            active = Decimal(target)
        for event in events:
            latest = event.rate
        output.append(Decimal("1") if active > 1 and latest > threshold else active)
    return tuple(output)


def benchmark(bars, start, end):
    from research_btc_dynamic_exposure import benchmark as base_benchmark

    return base_benchmark(bars, start, end)


def stress_sensitivity(bars, targets, funding, splits):
    output = {}
    cost_grid = (
        (Decimal("10"), Decimal("5")),
        (Decimal("20"), Decimal("10")),
        (Decimal("50"), Decimal("25")),
    )
    for fee, slippage in cost_grid:
        label = f"{fee}+{slippage}bps"
        output[label] = {}
        for name, bounds in splits.items():
            result = replay_segregated(
                bars,
                targets,
                funding,
                *bounds,
                spot_cap=Decimal("0.5"),
                maintenance_rate=Decimal("0.02"),
                fee_bps=fee,
                slippage_bps=slippage,
                enforce_effective_leverage_cap=True,
                maximum_futures_leverage=Decimal("2.5"),
            )
            baseline = benchmark(bars, *bounds)
            output[label][name] = {
                "net_return": result.net_return,
                "benchmark_return": baseline["net_return"],
                "excess": result.net_return - baseline["net_return"],
                "max_drawdown": result.max_drawdown,
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                "liquidated": result.liquidated,
            }
    return output


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def render(payload):
    lines = [
        "# BTC SMA10/40 Hysteresis + Funding Gate (Strict 15m)",
        "",
        "固定 0.01% Funding 过滤：高 Funding 时将 1.5X 主动暴露降至 1X，熊市仍为 0X。",
        "",
        "| 区间 | 策略 | B&H | 超额 | CAGR | DD | 最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = payload["metrics"][name]
        lines.append(
            f"| {name} | {row['net_return']:.2%} | {row['benchmark_return']:.2%} | "
            f"{row['excess']:.2%} | {row['cagr']:.2%} | {row['max_drawdown']:.2%} | "
            f"{row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += ["", "## Bootstrap", ""]
    for label, row in payload["path"]["bootstrap"].items():
        lines.append(
            f"- {label}: 超过 B&H {row['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += [
        "",
        "## Cost Sensitivity",
        "",
        "| 成本 | Validation超额 | Full超额 |",
        "|---|---:|---:|",
    ]
    for cost, rows in payload["stress_sensitivity"].items():
        lines.append(
            f"| {cost} | {rows['validation']['excess']:.2%} | {rows['full']['excess']:.2%} |"
        )
    lines += ["", "## Tail", "", "| 移除最佳日 | 年化超额 |", "|---:|---:|"]
    for row in payload["path"]["tail_concentration"]:
        lines.append(f"| {row['removed_best_relative_days']} | {row['annualized_excess']:.2%} |")
    yearly = payload["path"]["yearly_summary"]
    lines += [
        "",
        f"逐年跑赢：{yearly['wins']}/{yearly['years']}；"
        f"单侧符号检验 p={yearly['one_sided_sign_pvalue']:.4f}。",
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
