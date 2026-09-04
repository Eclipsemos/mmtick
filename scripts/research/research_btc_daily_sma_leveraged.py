#!/usr/bin/env python3
"""Evaluate the frozen daily-SMA leveraged BTC challenger.

The candidate is intentionally evaluated after the exploratory grid: this script is a
reproducible freeze/audit, not an OOS parameter selector.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_macro_gated_3x import run_bootstrap
from research_btc_block_bootstrap import paired_daily_log_returns
from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_leveraged/2026-09-02")
SPOT_CAP = Decimal("0.5")
BEAR_EXPOSURE = Decimal("0.2")
BULL_EXPOSURE = Decimal("1.5")
FROZEN_FAST = 8
FROZEN_SLOW = 40
NEIGHBORS = ((7, 35), (8, 40), (9, 45), (10, 50), (11, 55))


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    daily, ends = aggregate_complete_periods(bars, "1d")
    frozen_targets = build_targets(daily, ends, len(bars), FROZEN_FAST, FROZEN_SLOW)
    frozen = evaluate(bars, funding, frozen_targets, splits, benchmarks)
    neighbors = []
    for fast, slow in NEIGHBORS:
        targets = build_targets(daily, ends, len(bars), fast, slow)
        neighbors.append(
            {
                "sma": [fast, slow],
                "metrics": evaluate(bars, funding, targets, splits, benchmarks),
            }
        )
    full_start, full_end = splits["full"]
    audit = replay_segregated(
        bars,
        frozen_targets,
        funding,
        full_start,
        full_end,
        spot_cap=SPOT_CAP,
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        record_equity=True,
    )
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, audit.equity_curve, 100_000.0, start_ms=full_start
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20260930 + block,
        )
        for block in (7, 30, 90)
    }
    tail = tail_audit(strategy_logs, benchmark_logs)
    elapsed = years_between(full_start, bars[-1].end_ms)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "frozen_candidate": {
            "sma_fast": FROZEN_FAST,
            "sma_slow": FROZEN_SLOW,
            "bear_exposure": str(BEAR_EXPOSURE),
            "bull_exposure": str(BULL_EXPOSURE),
            "spot_cap": str(SPOT_CAP),
        },
        "protocol": {
            "signal": "completed UTC daily close; next 15m open",
            "costs": "10 bps fee + 5 bps slippage on changed notional",
            "funding": "historical funding charged on actual futures notional; no funding filter",
            "leverage": "active 15m-open control to <=3x futures-wallet equity",
            "selection_warning": (
                "candidate was frozen after historical exploration; current OOS is not untouched"
            ),
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "benchmarks": benchmarks,
        "frozen_metrics": frozen,
        "neighbors": neighbors,
        "bootstrap": bootstrap,
        "tail_audit": tail,
        "evaluation_years": elapsed,
        "full_cagr": annualized_return(frozen["full"]["net_return"], elapsed),
        "benchmark_full_cagr": annualized_return(benchmarks["full"]["net_return"], elapsed),
        "limitations": [
            "The exploratory scan has seen the historical OOS period; it is not a fresh holdout.",
            "ETH direct application is not robust, so this is a BTC-specific candidate.",
            "Bootstrap is a path sensitivity audit, not an unbiased significance test.",
        ],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_targets(daily, ends, source_count, fast: int, slow: int):
    fast_sma = simple_moving_average(daily, fast)
    slow_sma = simple_moving_average(daily, slow)
    dense = []
    for index, bar in enumerate(daily):
        if fast_sma[index] is None or slow_sma[index] is None:
            dense.append(None)
        elif bar.close < slow_sma[index] and fast_sma[index] < slow_sma[index]:
            dense.append(BEAR_EXPOSURE)
        else:
            dense.append(BULL_EXPOSURE)
    return map_targets_to_source(source_count, tuple(dense), ends)


def evaluate(bars, funding, targets, splits, benchmarks):
    output = {}
    for name, (start, end) in splits.items():
        result = replay_segregated(
            bars,
            targets,
            funding,
            start,
            end,
            spot_cap=SPOT_CAP,
            maintenance_rate=Decimal("0.02"),
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            enforce_effective_leverage_cap=True,
        )
        output[name] = {
            "net_return": result.net_return,
            "max_drawdown": result.max_drawdown,
            "excess": result.net_return - benchmarks[name]["net_return"],
            "fees": result.total_fees,
            "funding": result.total_funding,
            "rebalances": result.rebalances,
            "liquidated": result.liquidated,
            "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        }
    return output


def tail_audit(strategy, benchmark_values):
    paired = list(zip(strategy, benchmark_values, strict=True))
    ranked = sorted(range(len(paired)), key=lambda i: paired[i][0] - paired[i][1], reverse=True)
    rows = []
    for remove in (0, 1, 5, 10, 20):
        removed = set(ranked[:remove])
        strategy_sum = sum(v[0] for i, v in enumerate(paired) if i not in removed)
        benchmark_sum = sum(v[1] for i, v in enumerate(paired) if i not in removed)
        years = (len(paired) - remove) / 365.2425
        strategy_cagr = math.exp(strategy_sum / years) - 1
        benchmark_cagr = math.exp(benchmark_sum / years) - 1
        rows.append(
            {
                "removed_best_relative_days": remove,
                "strategy_cagr": strategy_cagr,
                "benchmark_cagr": benchmark_cagr,
                "annualized_excess": strategy_cagr - benchmark_cagr,
            }
        )
    return rows


def annualized_return(net_return: float, years: float) -> float:
    return (1 + net_return) ** (1 / years) - 1


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value: float) -> str:
    return f"{value:.2%}"


def markdown(payload: dict) -> str:
    frozen = payload["frozen_metrics"]
    lines = [
        "# BTC 日线快速 SMA 杠杆策略（冻结审计）",
        "",
        "候选：日线 SMA 8/40；熊市目标 0.2X，其他状态 1.5X；50% 现货、50% 合约抵押。",
        "",
        "| 区间 | 策略收益 | B&H | 超额 | 策略DD | B&H DD | 盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = frozen[name]
        benchmark_row = payload["benchmarks"][name]
        lines.append(
            f"| {name} | {pct(row['net_return'])} | {pct(benchmark_row['net_return'])} | "
            f"{pct(row['excess'])} | {pct(row['max_drawdown'])} | "
            f"{pct(benchmark_row['max_drawdown'])} | "
            f"{row['maximum_intrabar_leverage']:.2f}X |"
        )
    lines += [
        "",
        "## Bootstrap",
        "",
        "| 区块 | 超过B&H | 收益与DD同时胜出 | 超额P05 |",
        "|---:|---:|---:|---:|",
    ]
    for block, result in payload["bootstrap"].items():
        lines.append(
            f"| {block} | {pct(result['probability_beats_bh_return'])} | "
            f"{pct(result['probability_beats_return_and_drawdown'])} | "
            f"{pct(result['annualized_excess_vs_bh']['p05'])} |"
        )
    lines += [
        "",
        f"完整样本 CAGR：{pct(payload['full_cagr'])}；B&H：{pct(payload['benchmark_full_cagr'])}。",
        "Bootstrap 与历史尾部审计不能替代新数据 forward observation；参数冻结后不得回改。",
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_CANDIDATE**。",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
