#!/usr/bin/env python3
"""Audit the unlevered BTC SMA12/40 bear-defense attribution variant."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from audit_btc_sma12_three_state_attribution import classify_states, targets_for_states
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import annualized_return, replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT = Path("reports/experiments/btc_sma12_bear_defense/2026-09-03")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    targets = map_targets_to_source(
        len(bars),
        targets_for_states(classify_states(daily), {"bear": "0", "neutral": "1", "bull": "1"}),
        ends,
    )
    splits = split_periods(bars)
    full = replay(bars, targets, funding, *splits["full"], record_equity=True)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=splits["full"][0]
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20261103 + block,
        )
        for block in (7, 30, 90)
    }
    periods = {
        name: summarize(replay(bars, targets, funding, *bounds), benchmark(bars, *bounds), bounds)
        for name, bounds in splits.items()
    }
    rolling = {
        label: rolling_summary(bars, targets, funding, splits["full"], days)
        for label, days in (("1y", 365), ("2y", 730), ("3y", 1_095))
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / LOW_RISK_CHALLENGER",
        "protocol": {
            "strategy": "daily SMA12/40 bear-flat; otherwise 1X",
            "selection": "fixed attribution variant; no parameter search",
            "execution": "completed UTC daily signal; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "leverage": "2.5X opening cap and <=3X intrabar effective leverage",
        },
        "data": {"bars": len(bars), "last": iso(bars[-1].end_ms)},
        "periods": periods,
        "bootstrap": bootstrap,
        "rolling": rolling,
        "decision": (
            "retain only as a lower-drawdown comparison; one-year win rate and "
            "bootstrap lower bounds do not support replacing the three-state candidate"
        ),
    }
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
        maximum_futures_leverage=Decimal("2.5"),
    )


def summarize(result, baseline, bounds):
    return {
        "net_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "cagr": annualized_return(result.net_return, years_between(*bounds)),
        "max_drawdown": result.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
    }


def rolling_summary(bars, targets, funding, bounds, days):
    cursor = datetime.fromtimestamp(bounds[0] / 1000, UTC)
    last = datetime.fromtimestamp(bounds[1] / 1000, UTC)
    rows = []
    while cursor + timedelta(days=days) <= last:
        stop = cursor + timedelta(days=days) - timedelta(milliseconds=1)
        start_ms, end_ms = int(cursor.timestamp() * 1000), int(stop.timestamp() * 1000)
        result = replay(bars, targets, funding, start_ms, end_ms)
        baseline = benchmark(bars, start_ms, end_ms)
        excess = result.net_return - baseline["net_return"]
        rows.append(
            {
                "excess": excess,
                "beats_return": excess > 0,
                "beats_return_and_drawdown": (
                    excess > 0 and result.max_drawdown >= baseline["max_drawdown"]
                ),
            }
        )
        cursor += timedelta(days=30)
    excess_values = sorted(row["excess"] for row in rows)
    return {
        "windows": len(rows),
        "return_win_rate": ratio(row["beats_return"] for row in rows),
        "joint_win_rate": ratio(row["beats_return_and_drawdown"] for row in rows),
        "median_excess": excess_values[len(excess_values) // 2],
        "worst_excess": min(excess_values),
    }


def ratio(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def render(payload):
    full = payload["periods"]["full"]
    lines = [
        "# BTC SMA12/40 Bear Defense Audit",
        "",
        "熊市空仓，其他状态保持 1X；不使用额外总暴露。",
        "",
        f"全样本收益 {full['net_return']:.2%}，CAGR {full['cagr']:.2%}，"
        f"B&H {full['benchmark_return']:.2%}，最大回撤 {full['max_drawdown']:.2%}。",
        "",
        "## Bootstrap",
        "",
    ]
    for label, row in payload["bootstrap"].items():
        lines.append(
            f"- {label}: 超过 B&H {row['probability_beats_bh_return']:.2%}；"
            f"收益与 DD 同胜 {row['probability_beats_return_and_drawdown']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += [
        "",
        "## Rolling Windows",
        "",
        "| 窗口 | 数量 | 超过B&H | 收益与DD同胜 | 中位超额 | 最差超额 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, row in payload["rolling"].items():
        lines.append(
            f"| {label} | {row['windows']} | {row['return_win_rate']:.2%} | "
            f"{row['joint_win_rate']:.2%} | {row['median_excess']:.2%} | "
            f"{row['worst_excess']:.2%} |"
        )
    lines += [
        "",
        "结论：仅保留为低回撤对照；短窗口稳定性不足，不能替换三状态候选。",
        "状态：**RESEARCH_ONLY / LOW_RISK_CHALLENGER**。",
        "",
    ]
    return "\n".join(lines)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
