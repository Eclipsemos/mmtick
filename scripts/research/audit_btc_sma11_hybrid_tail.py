#!/usr/bin/env python3
"""Audit whether the frozen BTC SMA11 hybrid's excess is tail-event concentrated."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as candidate

OUTPUT = Path("reports/experiments/btc_sma11_enter2_active150_hybrid/2026-09-03/tail-audit")
INITIAL_EQUITY = 100_000.0
REMOVE_COUNTS = (5, 10, 20)


def main() -> None:
    spot, futures, daily, target_indices, funding = candidate.load_hybrid_inputs()
    bars = spot + futures
    targets = candidate.map_targets(
        len(bars),
        target_indices,
        candidate.build_targets(daily, fast_period=11, enter_bear_days=2, active=candidate.ACTIVE),
    )
    start, end = candidate.periods(bars[-1].end_ms, spot[-1].end_ms)["full"]
    result = candidate.replay(bars, targets, funding, start, end, record_equity=True)
    rows = paired_daily_rows(bars, result.equity_curve, start_ms=start)
    removed = {str(count): remove_top_excess(rows, count) for count in REMOVE_COUNTS}
    leave_year_out = leave_one_year_out(rows)
    top = sorted(rows, key=lambda row: row["excess_log_return"], reverse=True)
    positive_excess = sum(row["excess_log_return"] for row in rows if row["excess_log_return"] > 0)
    concentration = {
        str(count): sum(row["excess_log_return"] for row in top[:count]) / positive_excess
        for count in REMOVE_COUNTS
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "candidate": "frozen SMA11/40 enter2-exit1 active1.5X",
            "source": "strict stitched spot/15m perpetual replay, default costs and Funding",
            "pairing": "UTC daily strategy and B&H log returns from the same strict path",
            "tail_test": "remove the top strategy-minus-B&H daily log returns without retuning",
            "leave_one_year_out": (
                "recalculate the aggregate path after excluding each UTC calendar year"
            ),
        },
        "data": {
            "first": rows[0]["date"],
            "last": rows[-1]["date"],
            "daily_observations": len(rows),
            "strict_return": result.net_return,
            "strict_drawdown": result.max_drawdown,
        },
        "concentration": concentration,
        "tail_removal": removed,
        "leave_one_year_out": leave_year_out,
        "top_excess_days": top[:10],
        "conclusion": conclusion(removed, leave_year_out),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def paired_daily_rows(bars, equity_curve, *, start_ms: int):
    if not equity_curve:
        raise ValueError("equity curve is required")
    equity_by_day = {timestamp // 86_400_000: equity for timestamp, equity in equity_curve}
    close_by_day = {bar.end_ms // 86_400_000: float(bar.close) for bar in bars}
    counts_by_day = {}
    for bar in bars:
        day = bar.end_ms // 86_400_000
        counts_by_day[day] = counts_by_day.get(day, 0) + 1
    futures_start_day = int(candidate.FUTURES_START.timestamp() // 86_400)
    days = [
        day
        for day in sorted(set(equity_by_day) & set(close_by_day))
        if (day < futures_start_day and counts_by_day[day] == 1)
        or (day >= futures_start_day and counts_by_day[day] == 96)
    ]
    first = next(bar for bar in bars if bar.start_ms >= start_ms)
    previous_equity = INITIAL_EQUITY
    previous_price = float(first.open)
    output = []
    for day in days:
        equity = equity_by_day[day]
        price = close_by_day[day]
        if min(equity, previous_equity, price, previous_price) <= 0:
            raise ValueError("paths must remain positive")
        strategy = math.log(equity / previous_equity)
        benchmark = math.log(price / previous_price)
        output.append(
            {
                "date": datetime.fromtimestamp(day * 86_400, UTC).date().isoformat(),
                "strategy_log_return": strategy,
                "benchmark_log_return": benchmark,
                "excess_log_return": strategy - benchmark,
            }
        )
        previous_equity = equity
        previous_price = price
    return output


def remove_top_excess(rows, count: int) -> dict:
    if count < 1 or count >= len(rows):
        raise ValueError("count must be positive and smaller than the daily sample")
    leading = sorted(rows, key=lambda row: row["excess_log_return"], reverse=True)
    removed = {row["date"] for row in leading[:count]}
    kept = [row for row in rows if row["date"] not in removed]
    return aggregate(kept, removed_days=sorted(removed))


def leave_one_year_out(rows) -> list[dict]:
    years = sorted({row["date"][:4] for row in rows})
    return [
        {
            "excluded_year": year,
            **aggregate([row for row in rows if not row["date"].startswith(year)]),
        }
        for year in years
    ]


def aggregate(rows, *, removed_days: list[str] | None = None) -> dict:
    if not rows:
        raise ValueError("at least one daily row is required")
    strategy_return = math.exp(sum(row["strategy_log_return"] for row in rows)) - 1
    benchmark_return = math.exp(sum(row["benchmark_log_return"] for row in rows)) - 1
    years = len(rows) / 365.2425
    return {
        "days": len(rows),
        "strategy_return": strategy_return,
        "benchmark_return": benchmark_return,
        "excess": strategy_return - benchmark_return,
        "strategy_cagr": (1 + strategy_return) ** (1 / years) - 1,
        "benchmark_cagr": (1 + benchmark_return) ** (1 / years) - 1,
        "annualized_excess": ((1 + strategy_return) / (1 + benchmark_return)) ** (1 / years) - 1,
        **({"removed_days": removed_days} if removed_days is not None else {}),
    }


def conclusion(removed, leave_year_out) -> dict:
    return {
        "top_10_removal_still_beats_bh": removed["10"]["excess"] > 0,
        "all_leave_one_year_out_still_beats_bh": all(row["excess"] > 0 for row in leave_year_out),
        "interpretation": (
            "Tail removal is a concentration stress test, not an attainable trading path. "
            "It cannot replace independent forward observation."
        ),
    }


def render(payload) -> str:
    lines = [
        "# Frozen BTC SMA11 Hybrid Tail-Concentration Audit",
        "",
        "从冻结候选的严格 15m 净值路径构造 UTC 日度配对收益；剔除最强超额日只作压力审计，"
        "不改变任何参数。",
        "",
        "## Tail Removal",
        "",
        "| 剔除最强超额日 | 占正超额贡献 | 策略 | B&H | 超额 | 策略CAGR | 年化超额 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for count, row in payload["tail_removal"].items():
        lines.append(
            f"| {count} | {payload['concentration'][count]:.2%} | {row['strategy_return']:.2%} | "
            f"{row['benchmark_return']:.2%} | {row['excess']:.2%} | {row['strategy_cagr']:.2%} | "
            f"{row['annualized_excess']:.2%} |"
        )
    lines += [
        "",
        "## Leave-One-Year-Out",
        "",
        "| 剔除年份 | 策略 | B&H | 超额 | 年化超额 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in payload["leave_one_year_out"]:
        lines.append(
            f"| {row['excluded_year']} | {row['strategy_return']:.2%} | "
            f"{row['benchmark_return']:.2%} | {row['excess']:.2%} | "
            f"{row['annualized_excess']:.2%} |"
        )
    lines += [
        "",
        "## Conclusion",
        "",
        "- 剔除最强 10 日后仍超过 B&H："
        f"{'是' if payload['conclusion']['top_10_removal_still_beats_bh'] else '否'}。",
        f"- 剔除任意一个完整年份后仍超过 B&H："
        f"{'是' if payload['conclusion']['all_leave_one_year_out_still_beats_bh'] else '否'}。",
        "- 尾部审计不能生成可交易的未来路径，也不降低历史最大回撤；状态保持 "
        "**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
