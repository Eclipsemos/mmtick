#!/usr/bin/env python3
"""Rolling-window, bootstrap, and tail-concentration audit for the BTC 3x challenger."""

from __future__ import annotations

import json
import math
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_macro_gated_3x import macro_gated_targets
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_macro_gated_3x_audit/2026-09-02")
PERIODS = (24, 48, 96, 192)
MACRO_PERIOD = 1200
BEAR_EXPOSURE = Decimal("0.5")
BULL_EXPOSURE = Decimal("3")
FUNDING_THRESHOLD = Decimal("0.0001")
WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1095))
STEP_DAYS = 30
BLOCK_DAYS = (7, 30, 90)
BOOTSTRAP_SAMPLES = 10_000
RANDOM_SEED = 20_260_902
INITIAL_EQUITY = 100_000.0


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    targets = build_targets(bars, funding)
    full_start, full_end = split_periods(bars)["full"]
    full_result = replay_segregated(
        bars,
        targets,
        funding,
        full_start,
        full_end,
        spot_cap=Decimal("0"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        record_equity=True,
    )
    baseline = benchmark(bars, full_start, full_end)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars,
        full_result.equity_curve,
        INITIAL_EQUITY,
        start_ms=full_start,
    )
    assert_reconstruction(
        full_result.net_return,
        baseline["net_return"],
        strategy_logs,
        benchmark_logs,
    )
    rolling = {}
    for label, days in WINDOWS:
        rows = evaluate_windows(bars, funding, targets, days)
        rolling[label] = {"summary": summarize_windows(rows), "rows": rows}
        print(f"completed rolling {label}: {len(rows)}", flush=True)
    bootstrap = run_bootstraps(strategy_logs, benchmark_logs)
    tail = tail_concentration(strategy_logs, benchmark_logs)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "periods": PERIODS,
            "macro_period": MACRO_PERIOD,
            "bear_exposure": str(BEAR_EXPOSURE),
            "neutral_exposure": "1",
            "bull_exposure": str(BULL_EXPOSURE),
            "funding_threshold": str(FUNDING_THRESHOLD),
        },
        "protocol": {
            "rolling_windows": {label: days for label, days in WINDOWS},
            "rolling_step_days": STEP_DAYS,
            "bootstrap": (
                f"{BOOTSTRAP_SAMPLES} paired circular moving-block samples for each block size"
            ),
            "bootstrap_block_days": BLOCK_DAYS,
            "tail_test": "remove the best relative UTC days from both paired paths",
            "costs": "10 bps fee + 5 bps slippage; funding on the full futures sleeve",
            "warning": "historically selected candidate; bootstrap is not untouched OOS proof",
        },
        "historical": {
            "strategy_return": full_result.net_return,
            "strategy_max_drawdown": full_result.max_drawdown,
            "benchmark_return": baseline["net_return"],
            "benchmark_max_drawdown": baseline["max_drawdown"],
            "daily_observations": len(strategy_logs),
        },
        "rolling": rolling,
        "bootstrap": bootstrap,
        "tail_concentration": tail,
        "conclusion": conclusion(rolling, bootstrap, tail),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_targets(bars, funding):
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    raw = three_state_targets(aggregate, PERIODS, BEAR_EXPOSURE, BULL_EXPOSURE)
    macro = simple_moving_average(aggregate, MACRO_PERIOD)
    gated = macro_gated_targets(aggregate, raw, macro, BULL_EXPOSURE)
    regime = map_targets_to_source(len(bars), gated, ends)
    return funding_aware_targets(regime, funding, BULL_EXPOSURE, FUNDING_THRESHOLD)


def evaluate_windows(
    bars,
    funding,
    targets,
    window_days,
    *,
    enforce_effective_leverage_cap: bool = False,
):
    rows = []
    start = datetime(2020, 1, 1, tzinfo=UTC)
    last_end = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC)
    while start + timedelta(days=window_days) <= last_end:
        end = start + timedelta(days=window_days) - timedelta(milliseconds=1)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        result = replay_segregated(
            bars,
            targets,
            funding,
            start_ms,
            end_ms,
            spot_cap=Decimal("0"),
            maintenance_rate=Decimal("0.02"),
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            enforce_effective_leverage_cap=enforce_effective_leverage_cap,
        )
        baseline = benchmark(bars, start_ms, end_ms)
        rows.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "strategy_return": result.net_return,
                "benchmark_return": baseline["net_return"],
                "excess_return": result.net_return - baseline["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": baseline["max_drawdown"],
                "beats_return": result.net_return > baseline["net_return"],
                "beats_return_and_drawdown": (
                    result.net_return > baseline["net_return"]
                    and result.max_drawdown >= baseline["max_drawdown"]
                ),
                "liquidated": result.liquidated,
            }
        )
        start += timedelta(days=STEP_DAYS)
    return rows


def summarize_windows(rows):
    excess = [row["excess_return"] for row in rows]
    return {
        "windows": len(rows),
        "return_win_rate": ratio(row["beats_return"] for row in rows),
        "return_and_drawdown_win_rate": ratio(row["beats_return_and_drawdown"] for row in rows),
        "median_excess": median(excess),
        "worst_excess": min(excess),
        "best_excess": max(excess),
        "liquidations": sum(row["liquidated"] for row in rows),
    }


def run_bootstraps(strategy_logs, benchmark_logs):
    output = {}
    with ProcessPoolExecutor(max_workers=3) as executor:
        tasks = {
            block_days: executor.submit(
                run_bootstrap,
                strategy_logs,
                benchmark_logs,
                block_days=block_days,
                samples=BOOTSTRAP_SAMPLES,
                seed=RANDOM_SEED + block_days,
            )
            for block_days in BLOCK_DAYS
        }
        for block_days, task in tasks.items():
            output[f"{block_days}d"] = task.result()
            print(f"completed bootstrap {block_days}d", flush=True)
    return output


def tail_concentration(strategy_logs, benchmark_logs):
    paired = list(zip(strategy_logs, benchmark_logs, strict=True))
    ranked = sorted(
        range(len(paired)),
        key=lambda index: paired[index][0] - paired[index][1],
        reverse=True,
    )
    output = []
    for remove_count in (0, 1, 5, 10, 20):
        removed = set(ranked[:remove_count])
        strategy_sum = sum(value[0] for index, value in enumerate(paired) if index not in removed)
        benchmark_sum = sum(value[1] for index, value in enumerate(paired) if index not in removed)
        observations = len(paired) - remove_count
        years = observations / 365.2425
        strategy_cagr = math.exp(strategy_sum / years) - 1
        benchmark_cagr = math.exp(benchmark_sum / years) - 1
        output.append(
            {
                "removed_best_relative_days": remove_count,
                "strategy_cagr": strategy_cagr,
                "benchmark_cagr": benchmark_cagr,
                "annualized_excess": strategy_cagr - benchmark_cagr,
            }
        )
    return output


def assert_reconstruction(strategy_return, benchmark_return, strategy_logs, benchmark_logs):
    reconstructed_strategy = math.exp(sum(strategy_logs)) - 1
    reconstructed_benchmark = math.exp(sum(benchmark_logs)) - 1
    if not math.isclose(reconstructed_strategy, strategy_return, rel_tol=1e-10, abs_tol=1e-10):
        raise RuntimeError("daily strategy path does not reconstruct historical return")
    if not math.isclose(reconstructed_benchmark, benchmark_return, rel_tol=1e-10, abs_tol=1e-10):
        raise RuntimeError("daily benchmark path does not reconstruct historical return")


def conclusion(rolling, bootstrap, tail):
    positive_floor = all(
        result["annualized_excess_vs_bh"]["p05"] > 0 for result in bootstrap.values()
    )
    robust_rolling = all(value["summary"]["return_win_rate"] >= 0.5 for value in rolling.values())
    tail_survives = tail[-1]["annualized_excess"] > 0
    return {
        "positive_95pct_excess_floor": positive_floor,
        "rolling_majority_pass": robust_rolling,
        "positive_excess_after_removing_best_20_days": tail_survives,
        "status": (
            "FORWARD_OBSERVATION_CANDIDATE" if robust_rolling and tail_survives else "RESEARCH_ONLY"
        ),
    }


def markdown(payload):
    lines = [
        "# BTC 3X 宏观门槛候选统计审计",
        "",
        "固定候选后检查滚动窗口、配对区块Bootstrap与最佳相对收益日依赖。",
        "",
        "## 滚动窗口",
        "",
        "| 窗口 | 数量 | 收益胜率 | 收益+DD胜率 | 中位超额 | 最差超额 | 强平 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, value in payload["rolling"].items():
        summary = value["summary"]
        lines.append(
            f"| {label} | {summary['windows']} | "
            f"{pct(summary['return_win_rate'])} | "
            f"{pct(summary['return_and_drawdown_win_rate'])} | "
            f"{pct(summary['median_excess'])} | {pct(summary['worst_excess'])} | "
            f"{summary['liquidations']} |"
        )
    lines += [
        "",
        "## 配对区块 Bootstrap",
        "",
        "| 区块 | 超过B&H概率 | 收益+DD胜出 | CAGR>20% | 年化超额中位 | 年化超额P05 | DD中位 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, result in payload["bootstrap"].items():
        lines.append(
            f"| {label} | {pct(result['probability_beats_bh_return'])} | "
            f"{pct(result['probability_beats_return_and_drawdown'])} | "
            f"{pct(result['probability_cagr_above_20pct'])} | "
            f"{pct(result['annualized_excess_vs_bh']['median'])} | "
            f"{pct(result['annualized_excess_vs_bh']['p05'])} | "
            f"{pct(result['strategy_max_drawdown']['median'])} |"
        )
    lines += [
        "",
        "## 尾部依赖",
        "",
        "| 移除最佳相对日 | 策略CAGR | B&H CAGR | 年化超额 |",
        "|---:|---:|---:|---:|",
    ]
    for row in payload["tail_concentration"]:
        lines.append(
            f"| {row['removed_best_relative_days']} | {pct(row['strategy_cagr'])} | "
            f"{pct(row['benchmark_cagr'])} | {pct(row['annualized_excess'])} |"
        )
    conclusion_value = payload["conclusion"]
    lines += [
        "",
        "## 结论",
        "",
        "95%正超额下界："
        f"{'通过' if conclusion_value['positive_95pct_excess_floor'] else '未通过'}；"
        f"滚动多数窗口：{'通过' if conclusion_value['rolling_majority_pass'] else '未通过'}；"
        "移除最佳20个相对日后仍有正超额："
        f"{'是' if conclusion_value['positive_excess_after_removing_best_20_days'] else '否'}。",
        "",
        f"状态：**{conclusion_value['status']}**。Bootstrap不能替代2026-09-03之后的真实前向数据。",
        "",
    ]
    return "\n".join(lines)


def ratio(values):
    items = list(values)
    return sum(items) / len(items)


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
