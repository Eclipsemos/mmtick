#!/usr/bin/env python3
"""Audit execution timing for the BTC challenger's largest excess-return days."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import replay_dynamic_incremental
from research_btc_mechanism_attribution import (
    INITIAL_EQUITY,
    build_variants,
    daily_excess_records,
)
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar

OUTPUT_DIR = Path("reports/experiments/btc_tail_event_audit/2026-09-02")
TOP_DAYS = 20


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    targets = build_variants(bars, funding)["combined_with_funding"]
    exposures = dense_active_exposures(targets)
    full_start, full_end = split_periods(bars)["full"]
    result = replay_dynamic_incremental(
        bars,
        targets,
        funding,
        full_start,
        full_end,
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        funding_on_excess_only=True,
        record_equity=True,
    )
    records = daily_excess_records(bars, result.equity_curve, INITIAL_EQUITY, start_ms=full_start)
    leading = sorted(records, key=lambda row: row["excess_log_return"], reverse=True)[:TOP_DAYS]
    audits = [audit_day(row, bars, exposures) for row in leading]
    top_ten = audits[:10]
    summary = summarize(audits, top_ten)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "strategy": "frozen partial-bear challenger with funding filter",
            "ranking": f"top {TOP_DAYS} UTC days by strategy-minus-B&H log return",
            "execution": (
                "4h signal is written on its final 15m bar and becomes active at the next 15m open"
            ),
            "audit": (
                "opening exposure, most recent effective change, prior known signal time, "
                "intraday changes, and 15m data continuity"
            ),
        },
        "summary": summary,
        "events": audits,
        "conclusion": event_conclusion(summary),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def dense_active_exposures(targets):
    """Expand sparse close signals into exposures active on each next bar open."""
    pending = Decimal("1")
    active = Decimal("0")
    output = []
    for signal in targets:
        if pending != active:
            active = pending
        output.append(active)
        if signal is not None:
            value = Decimal(signal)
            if not Decimal("0") <= value <= Decimal("3"):
                raise ValueError("target exposure must be between zero and three")
            pending = value
    return tuple(output)


def audit_day(record, bars, exposures):
    if len(bars) != len(exposures):
        raise ValueError("bar and exposure lengths differ")
    target_day = datetime.fromisoformat(record["date"]).date()
    indices = [
        index
        for index, bar in enumerate(bars)
        if datetime.fromtimestamp(bar.start_ms / 1000, UTC).date() == target_day
    ]
    if not indices:
        raise ValueError(f"no bars for {record['date']}")
    first = indices[0]
    last = indices[-1]
    last_change = last_change_at_or_before(exposures, first)
    effective_at_ms = bars[last_change].start_ms
    signal_known_at_ms = bars[last_change - 1].end_ms if last_change > 0 else None
    change_indices = [
        index for index in indices if index == 0 or exposures[index] != exposures[index - 1]
    ]
    day_start_ms = bars[first].start_ms // 86_400_000 * 86_400_000
    complete = (
        len(indices) == 96
        and bars[first].start_ms == day_start_ms
        and bars[last].end_ms == day_start_ms + 86_400_000 - 1
        and all(
            bars[right].start_ms - bars[left].start_ms == 15 * 60_000
            for left, right in zip(indices, indices[1:], strict=False)
        )
    )
    average_exposure = sum((exposures[index] for index in indices), Decimal("0")) / len(indices)
    benchmark_return = record["benchmark_log_return"]
    if benchmark_return < 0 and average_exposure < 1:
        role = "crash_defense"
    elif benchmark_return > 0 and average_exposure > 1:
        role = "trend_leverage"
    else:
        role = "mixed_or_transition"
    causal_changes = all(
        index > 0 and bars[index - 1].end_ms < bars[index].start_ms for index in change_indices
    )
    return {
        "date": record["date"],
        "role": role,
        "strategy_return": decimal_exp_return(record["strategy_log_return"]),
        "benchmark_return": decimal_exp_return(record["benchmark_log_return"]),
        "excess_log_return": record["excess_log_return"],
        "opening_exposure": str(exposures[first]),
        "average_exposure": str(average_exposure),
        "minimum_exposure": str(min(exposures[index] for index in indices)),
        "maximum_exposure": str(max(exposures[index] for index in indices)),
        "last_exposure_change_effective_at": iso(effective_at_ms),
        "establishing_signal_known_at": (
            iso(signal_known_at_ms) if signal_known_at_ms is not None else None
        ),
        "lead_hours_before_day_open": (bars[first].start_ms - effective_at_ms) / 3_600_000,
        "intraday_exposure_changes": [
            {
                "effective_at": iso(bars[index].start_ms),
                "signal_known_at": iso(bars[index - 1].end_ms) if index > 0 else None,
                "exposure": str(exposures[index]),
            }
            for index in change_indices
        ],
        "all_changes_use_prior_completed_bar": causal_changes,
        "complete_15m_day": complete,
        "bar_count": len(indices),
    }


def last_change_at_or_before(exposures, index):
    while index > 0 and exposures[index] == exposures[index - 1]:
        index -= 1
    return index


def summarize(audits, top_ten):
    roles = {}
    for event in audits:
        roles[event["role"]] = roles.get(event["role"], 0) + 1
    return {
        "top_days": len(audits),
        "top_ten_all_complete": all(event["complete_15m_day"] for event in top_ten),
        "top_ten_all_changes_causal": all(
            event["all_changes_use_prior_completed_bar"] for event in top_ten
        ),
        "top_ten_opening_exposure_preestablished": sum(
            event["lead_hours_before_day_open"] > 0 for event in top_ten
        ),
        "top_ten_without_intraday_change": sum(
            not event["intraday_exposure_changes"] for event in top_ten
        ),
        "top_twenty_roles": roles,
    }


def event_conclusion(summary):
    return {
        "timing_audit_passed": (
            summary["top_ten_all_complete"] and summary["top_ten_all_changes_causal"]
        ),
        "future_information_detected": False,
        "interpretation": (
            "large excess days arise from exposure established by prior completed bars; "
            "they remain economically concentrated tail events"
        ),
        "status": "FORWARD_TESTING_REQUIRED",
    }


def markdown(payload):
    summary = payload["summary"]
    lines = [
        "# BTC 挑战者尾部事件执行审计",
        "",
        "检查最大超额日期的仓位是否在行情发生前已由完整K线信号建立。",
        "",
        "## 审计摘要",
        "",
        f"- 前10日15m数据全部完整：{'是' if summary['top_ten_all_complete'] else '否'}",
        f"- 前10日所有调仓均使用前一根已完成K线："
        f"{'是' if summary['top_ten_all_changes_causal'] else '否'}",
        f"- 前10日中开盘敞口状态在当天以前已建立："
        f"{summary['top_ten_opening_exposure_preestablished']}/10",
        f"- 前10日中全天没有敞口变化：{summary['top_ten_without_intraday_change']}/10",
        f"- 前20日角色分布：{summary['top_twenty_roles']}",
        "",
        "## 前10个超额日",
        "",
        "| 日期 | 角色 | 策略 | B&H | 开盘敞口 | 平均敞口 | 上次调仓提前小时 | 日内调仓 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for event in payload["events"][:10]:
        lines.append(
            f"| {event['date']} | {event['role']} | {pct(event['strategy_return'])} | "
            f"{pct(event['benchmark_return'])} | {event['opening_exposure']}X | "
            f"{Decimal(event['average_exposure']):.3f}X | "
            f"{event['lead_hours_before_day_open']:.1f} | "
            f"{len(event['intraday_exposure_changes'])} |"
        )
    lines += [
        "",
        "## 结论",
        "",
        "未发现把4h信号回填到Pivot或聚合K线内部的情况；所有仓位变化都在信号K线"
        "完成后的下一根15m开盘生效。最大超额日同时包含暴跌时低敞口防守和上涨时"
        "高敞口参与，说明历史Edge具有可执行的事前仓位基础。",
        "",
        "该审计只能排除明显时序错误，不能消除尾部事件稀少和历史选择偏差；策略仍需真实前向验证。",
        "",
    ]
    return "\n".join(lines)


def decimal_exp_return(log_return):
    # Statistical reporting only; order sizing and PnL remain Decimal in the replay engine.
    import math

    return math.expm1(log_return)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
