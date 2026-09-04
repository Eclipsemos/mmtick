#!/usr/bin/env python3
"""Audit the frozen funding-gated BTC short-side candidate."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_funding_short_gate_3x import (
    FAST,
    FEE_BPS,
    FUNDING_THRESHOLDS,
    HOLDOUT_END,
    HOLDOUT_START,
    MAINTENANCE,
    MAX_LEVERAGE,
    SLIPPAGE_BPS,
    SLOW,
    SPOT_CAP,
    build_regime,
    funding_gate_targets,
    pct,
)
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_funding_short_gate_audit/2026-09-02")
LOOKBACK = 5
THRESHOLD = FUNDING_THRESHOLDS[3]  # 0.0001, fixed before this audit
ROLLING_START = datetime(2020, 1, 1, tzinfo=UTC)
ROLLING_WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1_095))
STEP_DAYS = 30
BOOTSTRAP_BLOCKS = (7, 30, 90)
BOOTSTRAP_SAMPLES = 10_000


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding_rates = load_funding("BTCUSDT", bars)
    funding = funding_by_bar(bars, funding_rates)
    daily, ends = aggregate_complete_periods(bars, "1d")
    regime = build_regime(
        daily,
        simple_moving_average(daily, FAST),
        simple_moving_average(daily, SLOW),
        LOOKBACK,
    )
    source_regime = map_targets_to_source(len(bars), regime, ends)
    targets = funding_gate_targets(source_regime, funding, THRESHOLD)
    first = max(ROLLING_START, datetime.fromtimestamp(bars[0].start_ms / 1000, UTC))
    last = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC)
    rolling = {
        label: rolling_summary(bars, targets, funding, days, first, last)
        for label, days in ROLLING_WINDOWS
    }
    full = replay(bars, targets, funding, int(first.timestamp() * 1000), bars[-1].end_ms, True)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=int(first.timestamp() * 1000)
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=BOOTSTRAP_SAMPLES,
            seed=20260920 + block,
        )
        for block in BOOTSTRAP_BLOCKS
    }
    holdout = replay(bars, targets, funding, HOLDOUT_START, HOLDOUT_END, False)
    holdout_bh = benchmark(bars, HOLDOUT_START, HOLDOUT_END)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": f"daily-sma{FAST}-{SLOW}-slope{LOOKBACK}d-funding>{THRESHOLD}",
        "protocol": {
            "selection": "candidate fixed before audit; OOS and 2019 holdout excluded",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "hard_leverage": "maximum observed open and intrabar-low futures leverage <=3x",
        },
        "rolling": rolling,
        "bootstrap": bootstrap,
        "holdout_2019": {
            "strategy": public(holdout),
            "benchmark": holdout_bh,
            "excess": holdout.net_return - holdout_bh["net_return"],
        },
        "full": public(full),
        "conclusion": {
            "rolling_return_majority": all(
                item["summary"]["return_win_rate"] >= 0.5 for item in rolling.values()
            ),
            "bootstrap_p05_positive": all(
                item["annualized_excess_vs_bh"]["p05"] > 0 for item in bootstrap.values()
            ),
            "holdout_positive": holdout.net_return > holdout_bh["net_return"],
            "status": "RESEARCH_ONLY",
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def replay(bars, targets, funding, start, end, record_equity):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=SPOT_CAP,
        maintenance_rate=MAINTENANCE,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=MAX_LEVERAGE,
    )


def rolling_summary(bars, targets, funding, window_days, first, last):
    rows = []
    start = first
    while start + timedelta(days=window_days) <= last:
        end = start + timedelta(days=window_days) - timedelta(milliseconds=1)
        start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        result = replay(bars, targets, funding, start_ms, end_ms, False)
        base = benchmark(bars, start_ms, end_ms)
        rows.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "strategy_return": result.net_return,
                "benchmark_return": base["net_return"],
                "excess": result.net_return - base["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": base["max_drawdown"],
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                "liquidated": result.liquidated,
            }
        )
        start += timedelta(days=STEP_DAYS)
    excess = [row["excess"] for row in rows]
    return {
        "summary": {
            "windows": len(rows),
            "return_win_rate": ratio(row["excess"] > 0 for row in rows),
            "return_and_drawdown_win_rate": ratio(
                row["excess"] > 0 and row["strategy_drawdown"] >= row["benchmark_drawdown"]
                for row in rows
            ),
            "median_excess": sorted(excess)[len(excess) // 2] if excess else 0,
            "worst_excess": min(excess) if excess else 0,
            "maximum_intrabar_leverage": max(
                (row["maximum_intrabar_leverage"] for row in rows), default=0
            ),
            "liquidations": sum(row["liquidated"] for row in rows),
        },
        "rows": rows,
    }


def ratio(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def public(result):
    return {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
        "equity_curve_points": len(result.equity_curve),
    }


def render(payload):
    lines = [
        "# BTC Funding Short Gate Audit (Hard 3X)",
        "",
        f"固定候选：`{payload['candidate']}`；本审计不重新选参。",
        "",
        "| 窗口 | 数量 | 超过B&H | 收益+DD胜出 | 中位超额 | 最差超额 | 盘中最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, item in payload["rolling"].items():
        s = item["summary"]
        lines.append(
            f"| {label} | {s['windows']} | {pct(s['return_win_rate'])} | "
            f"{pct(s['return_and_drawdown_win_rate'])} | {pct(s['median_excess'])} | "
            f"{pct(s['worst_excess'])} | {s['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += ["", "## Bootstrap 与留出", ""]
    for block, item in payload["bootstrap"].items():
        lines.append(
            f"- {block}：超过 B&H {pct(item['probability_beats_bh_return'])}；"
            f"年化超额 P05 {pct(item['annualized_excess_vs_bh']['p05'])}；"
            f"收益与 DD 同胜 {pct(item['probability_beats_return_and_drawdown'])}。"
        )
    lines += [
        f"- 2019 独立留出超额：{pct(payload['holdout_2019']['excess'])}。",
        "",
        "结论：当前仍为 **RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
