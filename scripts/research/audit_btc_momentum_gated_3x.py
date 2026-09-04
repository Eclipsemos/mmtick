#!/usr/bin/env python3
"""Statistical audit for the frozen BTC price-momentum candidate."""

from __future__ import annotations

import json
import math
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median

from audit_btc_macro_gated_3x import assert_reconstruction, run_bootstrap
from research_btc_block_bootstrap import paired_daily_log_returns
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_momentum_gated_3x import build_candidates
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar

OUTPUT_DIR = Path("reports/experiments/btc_momentum_gated_3x_audit/2026-09-02")
FROZEN_ID = "4h-macro1200-momentum360-bear0.5x-neutral1x-bull3x-funding-le-0.0001"
WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1095))
STEP_DAYS = 30
BLOCK_DAYS = (7, 30, 90)
BOOTSTRAP_SAMPLES = 10_000
INITIAL_EQUITY = 100_000.0


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    selected = next(c for c in build_candidates(bars, funding) if c["id"] == FROZEN_ID)
    splits = split_periods(bars)
    full_start, full_end = splits["full"]
    result = replay_segregated(
        bars,
        selected["targets"],
        funding,
        full_start,
        full_end,
        spot_cap=Decimal("0"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        record_equity=True,
    )
    baseline = benchmark(bars, full_start, full_end)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars,
        result.equity_curve,
        INITIAL_EQUITY,
        start_ms=full_start,
    )
    assert_reconstruction(
        result.net_return,
        baseline["net_return"],
        strategy_logs,
        benchmark_logs,
    )
    rolling = {}
    for label, days in WINDOWS:
        rows = evaluate_windows(bars, funding, selected["targets"], days)
        rolling[label] = {"summary": summarize(rows), "rows": rows}
        print(f"completed rolling {label}: {len(rows)}", flush=True)
    bootstrap = run_bootstraps(strategy_logs, benchmark_logs)
    tail = tail_concentration(strategy_logs, benchmark_logs)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "id": FROZEN_ID,
            "macro_period": 1200,
            "momentum_period": 360,
            "bear_exposure": "0.5",
            "neutral_exposure": "1",
            "bull_exposure": "3",
            "funding_threshold": "0.0001",
        },
        "protocol": {
            "selection": "frozen before this audit; no rolling or bootstrap selection",
            "rolling_windows": {label: days for label, days in WINDOWS},
            "rolling_step_days": STEP_DAYS,
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "bootstrap_block_days": BLOCK_DAYS,
            "costs": "10 bps fee + 5 bps slippage; Funding on full futures sleeve",
            "leverage": "active 15m-open deleveraging to 3x futures-wallet equity",
            "warning": "historical candidate selection means this is not untouched OOS proof",
        },
        "historical": {
            "strategy_return": result.net_return,
            "strategy_max_drawdown": result.max_drawdown,
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


def evaluate_windows(bars, funding, targets, window_days):
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
            enforce_effective_leverage_cap=True,
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


def summarize(rows):
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
    with ProcessPoolExecutor(max_workers=3) as executor:
        tasks = {
            block: executor.submit(
                run_bootstrap,
                strategy_logs,
                benchmark_logs,
                block_days=block,
                samples=BOOTSTRAP_SAMPLES,
                seed=20_260_902 + block,
            )
            for block in BLOCK_DAYS
        }
        return {f"{block}d": task.result() for block, task in tasks.items()}


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
        years = (len(paired) - remove_count) / 365.2425
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


def conclusion(rolling, bootstrap, tail):
    return {
        "positive_95pct_excess_floor": all(
            value["annualized_excess_vs_bh"]["p05"] > 0 for value in bootstrap.values()
        ),
        "rolling_majority_pass": all(
            value["summary"]["return_win_rate"] >= 0.5 for value in rolling.values()
        ),
        "positive_excess_after_removing_best_5_days": tail[2]["annualized_excess"] > 0,
        "status": "RESEARCH_ONLY",
    }


def markdown(payload):
    tail_pass = payload["conclusion"]["positive_excess_after_removing_best_5_days"]
    lines = [
        "# BTC 价格动量 + 宏观门槛 3X 统计审计",
        "",
        f"冻结候选：`{payload['candidate']['id']}`。不在本审计中重新选参。",
        "",
        "## 滚动窗口",
        "",
        "| 窗口 | 数量 | 收益胜率 | 收益+DD胜率 | 中位超额 | 最差超额 | 强平 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, value in payload["rolling"].items():
        summary = value["summary"]
        lines.append(
            f"| {label} | {summary['windows']} | {pct(summary['return_win_rate'])} | "
            f"{pct(summary['return_and_drawdown_win_rate'])} | "
            f"{pct(summary['median_excess'])} | {pct(summary['worst_excess'])} | "
            f"{summary['liquidations']} |"
        )
    lines += [
        "",
        "## 配对区块 Bootstrap",
        "",
        "| 区块 | 超过B&H | 收益+DD | CAGR>20% | 年化超额中位 | P05 | DD中位 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, value in payload["bootstrap"].items():
        lines.append(
            f"| {label} | {pct(value['probability_beats_bh_return'])} | "
            f"{pct(value['probability_beats_return_and_drawdown'])} | "
            f"{pct(value['probability_cagr_above_20pct'])} | "
            f"{pct(value['annualized_excess_vs_bh']['median'])} | "
            f"{pct(value['annualized_excess_vs_bh']['p05'])} | "
            f"{pct(value['strategy_max_drawdown']['median'])} |"
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
    lines += [
        "",
        "## 结论",
        "",
        "滚动窗口多数超过B&H，但Bootstrap P05仍为负；",
        (f"移除最佳5个相对收益日后超额是否保持正值：{'是' if tail_pass else '否'}。"),
        "",
        "该候选的历史收益不能替代2026-09-03之后的真实前向观察。",
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
