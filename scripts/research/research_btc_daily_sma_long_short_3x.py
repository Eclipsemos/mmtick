#!/usr/bin/env python3
"""Freeze and audit the BTC daily SMA 8/40 long/short candidate under a 3x cap."""

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

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_long_short_3x/2026-09-02")
SPOT_CAP = Decimal("0.5")
BEAR_EXPOSURE = Decimal("-0.1")
BULL_EXPOSURE = Decimal("1.5")
FROZEN_FAST = 8
FROZEN_SLOW = 40
NEIGHBORS = ((7, 35), (8, 40), (9, 45), (10, 50))
CAP_SENSITIVITY = (
    Decimal("2"),
    Decimal("2.25"),
    Decimal("2.5"),
    Decimal("2.75"),
    Decimal("3"),
)


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


def replay(
    bars,
    funding,
    targets,
    start,
    end,
    *,
    record_equity: bool = False,
    leverage_cap: Decimal = Decimal("3"),
):
    return replay_segregated(
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
        record_equity=record_equity,
        maximum_futures_leverage=leverage_cap,
    )


def metrics(
    bars,
    funding,
    targets,
    splits,
    benchmarks,
    *,
    leverage_cap: Decimal = Decimal("3"),
):
    output = {}
    for name, (start, end) in splits.items():
        result = replay(bars, funding, targets, start, end, leverage_cap=leverage_cap)
        output[name] = {
            "net_return": result.net_return,
            "max_drawdown": result.max_drawdown,
            "excess": result.net_return - benchmarks[name]["net_return"],
            "fees": result.total_fees,
            "funding": result.total_funding,
            "rebalances": result.rebalances,
            "liquidated": result.liquidated,
            "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
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


def pct(value: float) -> str:
    return f"{value:.2%}"


def annualized_return(net_return: float, years: float) -> float:
    return (1 + net_return) ** (1 / years) - 1


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    daily, ends = aggregate_complete_periods(bars, "1d")
    frozen_targets = build_targets(daily, ends, len(bars), FROZEN_FAST, FROZEN_SLOW)
    frozen = metrics(bars, funding, frozen_targets, splits, benchmarks)
    cap_sensitivity = []
    for cap in CAP_SENSITIVITY:
        cap_metrics = metrics(
            bars,
            funding,
            frozen_targets,
            splits,
            benchmarks,
            leverage_cap=cap,
        )
        cap_sensitivity.append(
            {
                "leverage_cap": str(cap),
                "metrics": cap_metrics,
            }
        )
    neighbors = []
    for fast, slow in NEIGHBORS:
        targets = build_targets(daily, ends, len(bars), fast, slow)
        neighbors.append(
            {
                "sma": [fast, slow],
                "metrics": metrics(bars, funding, targets, splits, benchmarks),
            }
        )

    full_start, full_end = splits["full"]
    audit = replay(bars, funding, frozen_targets, full_start, full_end, record_equity=True)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, audit.equity_curve, 100_000.0, start_ms=full_start
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs, benchmark_logs, block_days=block, samples=10_000, seed=20260930 + block
        )
        for block in (7, 30, 90)
    }
    elapsed = years_between(full_start, bars[-1].end_ms)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "frozen_candidate": {
            "symbol": "BTCUSDT",
            "timeframe": "daily signal, 15m execution",
            "sma_fast": FROZEN_FAST,
            "sma_slow": FROZEN_SLOW,
            "bear_exposure": str(BEAR_EXPOSURE),
            "bull_exposure": str(BULL_EXPOSURE),
            "spot_cap": str(SPOT_CAP),
        },
        "protocol": {
            "signal": "completed UTC daily close; next 15m open",
            "costs": "10 bps fee + 5 bps slippage on changed notional",
            "funding": "historical funding charged on actual futures notional",
            "leverage": "active 15m-open control to <=3x futures-wallet equity",
            "intrabar_audit": "15m OHLC low used to measure effective leverage excursions",
            "selection": "parameters frozen before forward observation; OOS not used for selection",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "benchmarks": benchmarks,
        "frozen_metrics": frozen,
        "cap_sensitivity": cap_sensitivity,
        "neighbors": neighbors,
        "bootstrap": bootstrap,
        "tail_audit": tail_audit(strategy_logs, benchmark_logs),
        "evaluation_years": elapsed,
        "full_cagr": annualized_return(frozen["full"]["net_return"], elapsed),
        "benchmark_full_cagr": annualized_return(benchmarks["full"]["net_return"], elapsed),
        "limitations": [
            (
                "The historical OOS period was visible during prior exploration; "
                "it is not a fresh holdout."
            ),
            "This candidate has not demonstrated cross-asset robustness on ETH.",
            (
                "A 3x opening cap does not guarantee no intrabar leverage excursion "
                "after a gap or loss."
            ),
        ],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def markdown(payload: dict) -> str:
    frozen = payload["frozen_metrics"]
    lines = [
        "# BTC 日线 SMA 8/40 多空策略（严格 3X 冻结审计）",
        "",
        "熊市目标暴露为 -0.1X，其余状态为 +1.5X；50% 现货、50% 合约抵押。",
        "信号使用已完成 UTC 日线，下一根 15m 开盘调仓；压力成本为 10 bps 手续费 + 5 bps 滑点。",
        "",
        "| 区间 | 策略收益 | B&H | 超额 | 策略DD | B&H DD | 盘中最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = frozen[name]
        b = payload["benchmarks"][name]
        lines.append(
            f"| {name} | {pct(row['net_return'])} | {pct(b['net_return'])} | "
            f"{pct(row['excess'])} | {pct(row['max_drawdown'])} | "
            f"{pct(b['max_drawdown'])} | {row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        (
            f"Full CAGR：{pct(payload['full_cagr'])}；"
            f"B&H CAGR：{pct(payload['benchmark_full_cagr'])}。"
        ),
        "",
        "## 参数邻域",
        "",
        "| SMA | Research超额 | Validation超额 | OOS超额 | Full DD |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in payload["neighbors"]:
        m = row["metrics"]
        lines.append(
            f"| {row['sma'][0]}/{row['sma'][1]} | {pct(m['research']['excess'])} | "
            f"{pct(m['validation']['excess'])} | {pct(m['oos']['excess'])} | "
            f"{pct(m['full']['max_drawdown'])} |"
        )
    lines += [
        "",
        "## 杠杆上限敏感性",
        "",
        "| 上限 | Full收益 | Full DD | OOS超额 | 盘中最高杠杆 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in payload["cap_sensitivity"]:
        m = row["metrics"]
        lines.append(
            f"| {row['leverage_cap']}X | {pct(m['full']['net_return'])} | "
            f"{pct(m['full']['max_drawdown'])} | {pct(m['oos']['excess'])} | "
            f"{m['full']['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "## 结论",
        "",
        "该候选在历史分段和 3X 杠杆审计下击败 BTC B&H，但最大回撤仍很高，且存在尾部依赖。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。参数冻结后不得依据 OOS 回改。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
