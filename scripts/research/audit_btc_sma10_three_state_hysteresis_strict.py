#!/usr/bin/env python3
"""Audit fixed BTC daily SMA10/40 2/1 hysteresis under strict 15m execution."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma12_three_state_hysteresis import path_statistics
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_sma10_hysteresis_strict/2026-09-03")
START_MS = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
ACTIVE = Decimal("1.5")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    daily_targets = hysteresis_targets(daily)
    targets = map_targets_to_source(len(bars), daily_targets, ends)
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
    path = path_statistics(bars, targets, funding, splits, full_result, seed=20261700)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE",
        "candidate": {
            "id": "daily-sma10-40-hysteresis-enter2-exit1-active1.5",
            "fast_sma": 10,
            "slow_sma": 40,
            "enter_bear_after_days": 2,
            "exit_bear_after_days": 1,
            "active_exposure": str(ACTIVE),
            "bear_exposure": "0",
        },
        "protocol": {
            "signal": "completed UTC daily candle; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "hard_cap": "2.5X opening control; <=3X observed intrabar leverage",
            "selection": "fixed neighbor from pre-existing strict 15m grid; no OOS retuning",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "metrics": metrics,
        "path": path,
        "decision": decision(metrics, path),
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def hysteresis_targets(daily):
    fast = simple_moving_average(daily, 10)
    slow = simple_moving_average(daily, 40)
    state = None
    bear_count = 0
    recovery_count = 0
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
        elif state == "active" and bear_count >= 2:
            state = "bear"
        elif state == "bear" and recovery_count >= 1:
            state = "active"
        output.append(Decimal("0") if state == "bear" else ACTIVE)
    return tuple(output)


def split_periods(bars):
    return {
        "research": (START_MS, utc_ms(2022, 12, 31, 23, 59, 59)),
        "validation": (utc_ms(2023), utc_ms(2024, 12, 31, 23, 59, 59)),
        "oos": (utc_ms(2025), bars[-1].end_ms),
        "full": (START_MS, bars[-1].end_ms),
    }


def replay(bars, targets, funding, start, end, *, record=False):
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
        record_equity=record,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("2.5"),
    )


def public(result, baseline, bounds):
    return {
        "net_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "cagr": (1 + result.net_return) ** (1 / years_between(*bounds)) - 1,
        "max_drawdown": result.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def decision(metrics, path):
    return {
        "beats_bh_all_splits": all(row["excess"] > 0 for row in metrics.values()),
        "hard_3x_passed": all(
            row["maximum_intrabar_leverage"] <= 3 and not row["liquidated"]
            for row in metrics.values()
        ),
        "bootstrap_90d_p05_positive": (
            path["bootstrap"]["90d"]["annualized_excess_vs_bh"]["p05"] > 0
        ),
        "tail_5d_excess_positive": next(
            row["annualized_excess"]
            for row in path["tail_concentration"]
            if row["removed_best_relative_days"] == 5
        )
        > 0,
        "status": "RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE",
    }


def utc_ms(year, month=1, day=1, hour=0, minute=0, second=0):
    return int(datetime(year, month, day, hour, minute, second, tzinfo=UTC).timestamp() * 1000)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def render(payload):
    lines = [
        "# BTC SMA10/40 Hysteresis Strict 15m Audit",
        "",
        "固定 SMA10/40，连续 2 个熊市日进入 0X，1 个非熊市日恢复 1.5X。",
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
    lines += ["", "## Tail", "", "| 移除最佳日 | 年化超额 |", "|---:|---:|"]
    for row in payload["path"]["tail_concentration"]:
        lines.append(f"| {row['removed_best_relative_days']} | {row['annualized_excess']:.2%} |")
    yearly = payload["path"]["yearly_summary"]
    lines += [
        "",
        f"逐年跑赢：{yearly['wins']}/{yearly['years']}；"
        f"单侧符号检验 p={yearly['one_sided_sign_pvalue']:.4f}。",
        "",
        "状态：**RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
