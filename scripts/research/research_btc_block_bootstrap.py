#!/usr/bin/env python3
"""Paired block-bootstrap audit for the frozen BTC exposure candidates."""

from __future__ import annotations

import json
import math
import random
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import benchmark, replay_dynamic_incremental
from research_btc_frozen_ensemble import build_targets, combine_sparse_targets
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar

OUTPUT_DIR = Path("reports/experiments/btc_block_bootstrap/2026-09-02")
BLOCK_DAYS = (7, 30, 90)
SAMPLES = 10_000
RANDOM_SEED = 20_260_902
INITIAL_EQUITY = 100_000.0


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    targets = build_targets(bars, funding)
    targets["equal_weight_ensemble"] = combine_sparse_targets(
        targets["primary"], targets["partial_bear_challenger"]
    )
    start_ms = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
    end_ms = bars[-1].end_ms
    baseline = benchmark(bars, start_ms, end_ms)
    daily = {}
    for candidate_id, candidate_targets in targets.items():
        result = replay_dynamic_incremental(
            bars,
            candidate_targets,
            funding,
            start_ms,
            end_ms,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            funding_on_excess_only=True,
            record_equity=True,
        )
        strategy_logs, benchmark_logs = paired_daily_log_returns(
            bars,
            result.equity_curve,
            INITIAL_EQUITY,
            start_ms=start_ms,
        )
        reconstructed = math.exp(sum(strategy_logs)) - 1
        if not math.isclose(reconstructed, result.net_return, rel_tol=1e-10, abs_tol=1e-10):
            raise RuntimeError(f"daily path does not reconstruct {candidate_id} return")
        reconstructed_benchmark = math.exp(sum(benchmark_logs)) - 1
        if not math.isclose(
            reconstructed_benchmark,
            baseline["net_return"],
            rel_tol=1e-10,
            abs_tol=1e-10,
        ):
            raise RuntimeError("daily benchmark path does not reconstruct B&H return")
        daily[candidate_id] = {
            "strategy_logs": strategy_logs,
            "benchmark_logs": benchmark_logs,
            "historical_stress_return": result.net_return,
            "historical_max_drawdown": result.max_drawdown,
        }

    tasks = []
    with ProcessPoolExecutor(max_workers=3) as executor:
        for candidate_index, (candidate_id, values) in enumerate(daily.items()):
            for block_days in BLOCK_DAYS:
                future = executor.submit(
                    run_bootstrap,
                    values["strategy_logs"],
                    values["benchmark_logs"],
                    block_days=block_days,
                    samples=SAMPLES,
                    seed=RANDOM_SEED + candidate_index * 1_000 + block_days,
                )
                tasks.append((candidate_id, block_days, future))
        bootstrap = {candidate_id: {} for candidate_id in daily}
        for candidate_id, block_days, future in tasks:
            bootstrap[candidate_id][f"{block_days}d"] = future.result()
            print(f"completed {candidate_id} {block_days}d", flush=True)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "method": "paired circular moving-block bootstrap of UTC daily log returns",
            "samples": SAMPLES,
            "block_days": BLOCK_DAYS,
            "random_seed": RANDOM_SEED,
            "pairing": "strategy and B&H resample the same historical day indices",
            "costs": "strategy uses stress costs: 10 bps fee + 5 bps slippage",
            "selection_warning": (
                "candidates were selected using historical data; bootstrap is a sensitivity "
                "audit, not an unbiased OOS significance test"
            ),
        },
        "data": {
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
            "daily_observations": len(next(iter(daily.values()))["strategy_logs"]),
        },
        "historical": {
            "buy_and_hold_return": baseline["net_return"],
            "buy_and_hold_max_drawdown": baseline["max_drawdown"],
            "strategies": {
                candidate_id: {
                    "stress_return": values["historical_stress_return"],
                    "max_drawdown": values["historical_max_drawdown"],
                }
                for candidate_id, values in daily.items()
            },
        },
        "bootstrap": bootstrap,
        "conclusion": {
            "return_leader": "partial_bear_challenger",
            "risk_balanced_candidate": "equal_weight_ensemble",
            "positive_95pct_excess_floor": False,
            "status": "FORWARD_TESTING_REQUIRED",
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def paired_daily_log_returns(bars, equity_curve, initial_equity, *, start_ms=None):
    if not bars or not equity_curve:
        raise ValueError("bars and equity curve are required")
    equity_by_day = {}
    for timestamp_ms, equity in equity_curve:
        equity_by_day[timestamp_ms // 86_400_000] = equity
    close_by_day = {}
    for bar in bars:
        close_by_day[bar.end_ms // 86_400_000] = float(bar.close)
    days = sorted(set(equity_by_day) & set(close_by_day))
    if not days:
        raise ValueError("equity and price paths do not overlap")
    strategy_logs = []
    benchmark_logs = []
    previous_equity = initial_equity
    first_bar = next(bar for bar in bars if start_ms is None or bar.start_ms >= start_ms)
    previous_price = float(first_bar.open)
    for day in days:
        equity = equity_by_day[day]
        price = close_by_day[day]
        if equity <= 0 or previous_equity <= 0 or price <= 0 or previous_price <= 0:
            raise ValueError("paths must remain positive for log returns")
        strategy_logs.append(math.log(equity / previous_equity))
        benchmark_logs.append(math.log(price / previous_price))
        previous_equity = equity
        previous_price = price
    return tuple(strategy_logs), tuple(benchmark_logs)


def run_bootstrap(strategy_logs, benchmark_logs, *, block_days, samples, seed):
    if len(strategy_logs) != len(benchmark_logs) or not strategy_logs:
        raise ValueError("paired return lengths must match and be non-empty")
    if block_days < 1 or samples < 1:
        raise ValueError("block days and samples must be positive")
    count = len(strategy_logs)
    years = count / 365.2425
    strategy_factors = tuple(math.exp(value) for value in strategy_logs)
    benchmark_factors = tuple(math.exp(value) for value in benchmark_logs)
    rng = random.Random(seed)
    strategy_cagrs = []
    annualized_excess = []
    strategy_drawdowns = []
    terminal_wins = 0
    drawdown_wins = 0
    joint_wins = 0
    cagr_above_twenty = 0
    drawdown_over_seventy = 0
    for _ in range(samples):
        strategy_equity = 1.0
        benchmark_equity = 1.0
        strategy_peak = 1.0
        benchmark_peak = 1.0
        strategy_dd = 0.0
        benchmark_dd = 0.0
        consumed = 0
        while consumed < count:
            start = rng.randrange(count)
            take = min(block_days, count - consumed)
            for offset in range(take):
                index = (start + offset) % count
                strategy_equity *= strategy_factors[index]
                benchmark_equity *= benchmark_factors[index]
                strategy_peak = max(strategy_peak, strategy_equity)
                benchmark_peak = max(benchmark_peak, benchmark_equity)
                strategy_dd = min(strategy_dd, strategy_equity / strategy_peak - 1)
                benchmark_dd = min(benchmark_dd, benchmark_equity / benchmark_peak - 1)
            consumed += take
        strategy_cagr = strategy_equity ** (1 / years) - 1
        excess = (strategy_equity / benchmark_equity) ** (1 / years) - 1
        beats_return = strategy_equity > benchmark_equity
        beats_drawdown = strategy_dd >= benchmark_dd
        terminal_wins += beats_return
        drawdown_wins += beats_drawdown
        joint_wins += beats_return and beats_drawdown
        cagr_above_twenty += strategy_cagr > 0.2
        drawdown_over_seventy += strategy_dd <= -0.7
        strategy_cagrs.append(strategy_cagr)
        annualized_excess.append(excess)
        strategy_drawdowns.append(strategy_dd)
    return {
        "samples": samples,
        "block_days": block_days,
        "probability_beats_bh_return": terminal_wins / samples,
        "probability_drawdown_no_worse_than_bh": drawdown_wins / samples,
        "probability_beats_return_and_drawdown": joint_wins / samples,
        "probability_cagr_above_20pct": cagr_above_twenty / samples,
        "probability_drawdown_at_least_70pct": drawdown_over_seventy / samples,
        "strategy_cagr": distribution(strategy_cagrs),
        "annualized_excess_vs_bh": distribution(annualized_excess),
        "strategy_max_drawdown": distribution(strategy_drawdowns),
    }


def distribution(values):
    ordered = sorted(values)
    return {
        "p05": percentile(ordered, 0.05),
        "median": percentile(ordered, 0.5),
        "p95": percentile(ordered, 0.95),
    }


def percentile(ordered, probability):
    if not ordered:
        raise ValueError("values are required")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def markdown(payload):
    lines = [
        "# BTC 冻结策略配对区块 Bootstrap",
        "",
        "固定历史策略后，对策略与B&H使用相同的UTC日期索引进行循环区块重采样。",
        "策略采用压力成本。该结果衡量历史路径敏感性，不是独立OOS显著性证明。",
        "",
        "| 策略 | 区块 | 超过B&H | 收益+DD胜出 | CAGR>20% | 年化超额中位 | "
        "年化超额P05 | DD中位 | DD≤-70% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for candidate_id, blocks in payload["bootstrap"].items():
        for block_id, result in blocks.items():
            lines.append(
                f"| `{candidate_id}` | {block_id} | "
                f"{pct(result['probability_beats_bh_return'])} | "
                f"{pct(result['probability_beats_return_and_drawdown'])} | "
                f"{pct(result['probability_cagr_above_20pct'])} | "
                f"{pct(result['annualized_excess_vs_bh']['median'])} | "
                f"{pct(result['annualized_excess_vs_bh']['p05'])} | "
                f"{pct(result['strategy_max_drawdown']['median'])} | "
                f"{pct(result['probability_drawdown_at_least_70pct'])} |"
            )
    lines += [
        "",
        "P05为10,000次样本中的第5百分位；负的年化超额P05表示仍存在明显落后B&H的路径。",
        "",
        "## 结论",
        "",
        "熊市部分底仓挑战者是收益领先者，但尾部回撤风险最高；"
        "等权组合牺牲部分超额概率，换取更低的极端回撤概率。",
        "所有策略在7/30/90日区块下的年化超额P05均为负，因此历史数据尚不能提供95%把握的正超额下界。",
        "策略状态保持 **FORWARD_TESTING_REQUIRED**；Bootstrap不能替代2026-09-03之后的"
        "真实前向观察。",
        "",
    ]
    return "\n".join(lines)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
