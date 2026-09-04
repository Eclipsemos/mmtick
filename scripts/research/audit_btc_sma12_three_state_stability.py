#!/usr/bin/env python3
"""Audit yearly and tail-event stability of the frozen BTC three-state candidate."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_macro_gated_3x import tail_concentration
from research_btc_block_bootstrap import paired_daily_log_returns
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_mechanism_attribution import (
    concentration_analysis,
    daily_excess_records,
    leave_one_year_out,
)
from research_btc_sma12_three_state import build_dense_targets
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_tail_event_audit import audit_day, dense_active_exposures

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT = Path("reports/experiments/btc_sma12_three_state_stability/2026-09-03")
INITIAL_EQUITY = 100_000.0


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    targets = map_targets_to_source(
        len(bars),
        build_dense_targets(daily, Decimal("1.25"), Decimal("1.5")),
        ends,
    )
    full_start, full_end = split_periods(bars)["full"]
    full = replay(bars, targets, funding, full_start, full_end, record_equity=True)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, INITIAL_EQUITY, start_ms=full_start
    )
    tail = tail_concentration(strategy_logs, benchmark_logs)
    records = daily_excess_records(bars, full.equity_curve, INITIAL_EQUITY, start_ms=full_start)
    concentration = concentration_analysis(records)
    exposures = dense_active_exposures(targets)
    top_records = sorted(records, key=lambda row: row["excess_log_return"], reverse=True)[:10]
    top_day_timing = [audit_day(row, bars, exposures) for row in top_records]
    yearly = yearly_results(bars, targets, funding, full_start, full_end)
    yearly_wins = sum(row["excess"] > 0 for row in yearly)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "candidate": "SMA12/40: bear 0X, neutral 1.25X, bull 1.5X",
            "signal": "completed UTC daily candle; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "tail_test": "remove best relative UTC days from both paired paths",
            "year_test": "independent calendar-year replays with exact one-sided sign test",
        },
        "data": {"bars": len(bars), "records": len(records), "last": iso(bars[-1].end_ms)},
        "yearly": yearly,
        "yearly_summary": {
            "years": len(yearly),
            "wins": yearly_wins,
            "win_rate": yearly_wins / len(yearly),
            "one_sided_sign_pvalue": exact_sign_pvalue(yearly_wins, len(yearly)),
        },
        "tail_concentration": tail,
        "concentration": concentration,
        "top_day_timing": top_day_timing,
        "leave_one_year_out": leave_one_year_out(records),
        "decision": (
            "historical superiority remains economically concentrated unless both tail-day "
            "removal and independent forward evidence remain positive"
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


def yearly_results(bars, targets, funding, start_ms, end_ms):
    first_year = datetime.fromtimestamp(start_ms / 1000, UTC).year
    last_year = datetime.fromtimestamp(end_ms / 1000, UTC).year
    rows = []
    for year in range(first_year, last_year + 1):
        start = max(start_ms, int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000))
        end = min(
            end_ms,
            int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1,
        )
        result = replay(bars, targets, funding, start, end)
        baseline = benchmark(bars, start, end)
        rows.append(
            {
                "year": year,
                "strategy_return": result.net_return,
                "benchmark_return": baseline["net_return"],
                "excess": result.net_return - baseline["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": baseline["max_drawdown"],
            }
        )
    return rows


def exact_sign_pvalue(wins, trials):
    if not 0 <= wins <= trials:
        raise ValueError("wins must be between zero and trials")
    return sum(math.comb(trials, count) for count in range(wins, trials + 1)) / (2**trials)


def render(payload):
    summary = payload["yearly_summary"]
    lines = [
        "# BTC SMA12/40 Three-State Stability Audit",
        "",
        "## Yearly Results",
        "",
        "| 年份 | 策略 | B&H | 超额 | 策略DD | B&H DD |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["yearly"]:
        lines.append(
            f"| {row['year']} | {row['strategy_return']:.2%} | "
            f"{row['benchmark_return']:.2%} | {row['excess']:.2%} | "
            f"{row['strategy_drawdown']:.2%} | {row['benchmark_drawdown']:.2%} |"
        )
    lines += [
        "",
        f"逐年跑赢 {summary['wins']}/{summary['years']}；精确单侧符号检验 "
        f"p={summary['one_sided_sign_pvalue']:.4f}。",
        "",
        "## Tail Concentration",
        "",
        "| 移除最佳相对收益日 | 策略CAGR | B&H CAGR | 年化超额 |",
        "|---:|---:|---:|---:|",
    ]
    for row in payload["tail_concentration"]:
        lines.append(
            f"| {row['removed_best_relative_days']} | {row['strategy_cagr']:.2%} | "
            f"{row['benchmark_cagr']:.2%} | {row['annualized_excess']:.2%} |"
        )
    leave_one_out = payload["leave_one_year_out"]
    remaining = [row["remaining_annualized_excess"] for row in leave_one_out]
    lines += [
        "",
        f"正超额月份比例：{payload['concentration']['positive_month_rate']:.2%}；"
        f"正超额年份比例：{payload['concentration']['positive_year_rate']:.2%}。",
        "",
        f"留一年度后仍跑赢：{sum(row['still_beats_buy_and_hold'] for row in leave_one_out)}/"
        f"{len(leave_one_out)}；剩余年化超额范围 {min(remaining):.2%} 至 {max(remaining):.2%}。",
        "",
        "## Largest Relative Days",
        "",
        "| 日期 | 策略 | B&H |",
        "|---|---:|---:|",
    ]
    for row in payload["concentration"]["top_10_excess_days"][:5]:
        lines.append(
            f"| {row['date']} | {row['strategy_return']:.2%} | {row['benchmark_return']:.2%} |"
        )
    timing = payload["top_day_timing"]
    timing_causal = all(row["all_changes_use_prior_completed_bar"] for row in timing)
    timing_unchanged = all(not row["intraday_exposure_changes"] for row in timing)
    minimum_lead_hours = min(row["lead_hours_before_day_open"] for row in timing)
    lines += [
        "",
        f"前10个相对收益日全部使用前序完整 K 线："
        f"{'是' if timing_causal else '否'}；"
        f"全天保持既定暴露：{'是' if timing_unchanged else '否'}。",
        f"这些日期的 0X 状态最少提前 {minimum_lead_hours:.0f} 小时建立。",
        "",
        "历史收益仍需通过冻结后的独立路径确认；年度数量不足且最佳日期可能集中。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
