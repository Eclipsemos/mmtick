#!/usr/bin/env python3
"""Attribute the frozen partial-bear BTC challenger's historical edge."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import as_dict, benchmark, replay_dynamic_incremental
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT_DIR = Path("reports/experiments/btc_mechanism_attribution/2026-09-02")
PERIODS = (25, 50, 100, 200)
BEAR_EXPOSURE = Decimal("0.5")
BULL_EXPOSURE = Decimal("1.75")
FUNDING_THRESHOLD = Decimal("0.0001")
INITIAL_EQUITY = 100_000.0


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    targets = build_variants(bars, funding)
    full_start, full_end = splits["full"]
    split_results = evaluate_splits(bars, funding, targets, splits)
    yearly = evaluate_years(bars, funding, targets)
    full_candidate = replay_dynamic_incremental(
        bars,
        targets["combined_with_funding"],
        funding,
        full_start,
        full_end,
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        funding_on_excess_only=True,
        record_equity=True,
    )
    daily = daily_excess_records(
        bars, full_candidate.equity_curve, INITIAL_EQUITY, start_ms=full_start
    )
    concentration = concentration_analysis(daily)
    leave_year_out = leave_one_year_out(daily)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "periods": PERIODS,
            "variants": {
                "buy_and_hold_stress": "1x throughout, with strategy execution costs",
                "bear_defense_only": "0.5x bear, otherwise 1x",
                "bull_leverage_only": "1.75x bull, otherwise 1x",
                "combined_no_funding": "0.5x bear, 1x neutral, 1.75x bull",
                "combined_with_funding": (
                    "combined exposure; bull leverage allowed only when known funding <=0.01%"
                ),
            },
            "maximum_exposure": str(BULL_EXPOSURE),
            "execution": "completed 4h candle; next 15m open; exposure-delta rebalance",
            "costs": "base 5+2 bps; stress 10+5 bps on changed notional",
            "funding": "charged only above 1x",
        },
        "data": {
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
            "bars": len(bars),
        },
        "splits": split_results,
        "yearly_stress": yearly,
        "candidate_daily_concentration": concentration,
        "candidate_leave_one_year_out": leave_year_out,
        "conclusion": {
            "bear_defense_role": "drawdown reduction",
            "bull_leverage_role": "return enhancement with worse standalone drawdown",
            "funding_filter_role": "avoids expensive/overheated leveraged bull exposure",
            "edge_shape": "lumpy tail-event capture rather than consistent monthly excess",
            "status": "FORWARD_TESTING_REQUIRED",
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_variants(bars, funding):
    aggregate, ends = aggregate_complete_periods(bars, "4h")

    def mapped(bear, bull):
        return map_targets_to_source(
            len(bars),
            three_state_targets(aggregate, PERIODS, bear, bull),
            ends,
        )

    combined = mapped(BEAR_EXPOSURE, BULL_EXPOSURE)
    return {
        "buy_and_hold_stress": tuple(None for _ in bars),
        "bear_defense_only": mapped(BEAR_EXPOSURE, Decimal("1")),
        "bull_leverage_only": mapped(Decimal("1"), BULL_EXPOSURE),
        "combined_no_funding": combined,
        "combined_with_funding": funding_aware_targets(
            combined, funding, BULL_EXPOSURE, FUNDING_THRESHOLD
        ),
    }


def evaluate_splits(bars, funding, targets, splits):
    output = {}
    for split, (start, end) in splits.items():
        baseline = benchmark(bars, start, end)
        output[split] = {"buy_and_hold_price": baseline, "variants": {}}
        for variant_id, variant_targets in targets.items():
            base = replay_dynamic_incremental(
                bars,
                variant_targets,
                funding,
                start,
                end,
                funding_on_excess_only=True,
            )
            stress = replay_dynamic_incremental(
                bars,
                variant_targets,
                funding,
                start,
                end,
                fee_bps=Decimal("10"),
                slippage_bps=Decimal("5"),
                funding_on_excess_only=True,
            )
            output[split]["variants"][variant_id] = {
                "base": as_dict(base),
                "stress": as_dict(stress),
                "stress_excess": stress.net_return - baseline["net_return"],
            }
    return output


def evaluate_years(bars, funding, targets):
    first_year = datetime.fromtimestamp(bars[0].start_ms / 1000, UTC).year
    last_year = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).year
    output = []
    for year in range(first_year, last_year + 1):
        start = max(utc_ms(year, 1, 1), bars[0].start_ms)
        end = min(utc_ms(year + 1, 1, 1) - 1, bars[-1].end_ms)
        baseline = benchmark(bars, start, end)
        row = {"year": year, "buy_and_hold": baseline["net_return"], "variants": {}}
        for variant_id, variant_targets in targets.items():
            result = replay_dynamic_incremental(
                bars,
                variant_targets,
                funding,
                start,
                end,
                fee_bps=Decimal("10"),
                slippage_bps=Decimal("5"),
                funding_on_excess_only=True,
            )
            row["variants"][variant_id] = result.net_return
        output.append(row)
    return output


def daily_excess_records(bars, equity_curve, initial_equity, *, start_ms=None):
    if not bars or not equity_curve:
        raise ValueError("bars and equity curve are required")
    equity_by_day = {}
    for timestamp_ms, equity in equity_curve:
        equity_by_day[timestamp_ms // 86_400_000] = equity
    close_by_day = {}
    for bar in bars:
        close_by_day[bar.end_ms // 86_400_000] = float(bar.close)
    days = sorted(set(equity_by_day) & set(close_by_day))
    previous_equity = initial_equity
    first_bar = next(bar for bar in bars if start_ms is None or bar.start_ms >= start_ms)
    previous_price = float(first_bar.open)
    records = []
    for day in days:
        equity = equity_by_day[day]
        price = close_by_day[day]
        strategy_log = math.log(equity / previous_equity)
        benchmark_log = math.log(price / previous_price)
        moment = datetime.fromtimestamp(day * 86_400, UTC)
        records.append(
            {
                "date": moment.date().isoformat(),
                "year": moment.year,
                "month": moment.strftime("%Y-%m"),
                "strategy_log_return": strategy_log,
                "benchmark_log_return": benchmark_log,
                "excess_log_return": strategy_log - benchmark_log,
            }
        )
        previous_equity = equity
        previous_price = price
    return records


def concentration_analysis(records):
    if not records:
        raise ValueError("records are required")
    ranked = sorted(records, key=lambda row: row["excess_log_return"], reverse=True)
    positive_total = sum(max(0.0, row["excess_log_return"]) for row in records)
    total_excess = sum(row["excess_log_return"] for row in records)
    years = len(records) / 365.2425
    removals = {}
    for count in (5, 10, 20):
        removed = sum(row["excess_log_return"] for row in ranked[:count])
        removals[str(count)] = {
            "share_of_positive_excess": removed / positive_total,
            "remaining_annualized_excess": math.expm1((total_excess - removed) / years),
        }
    monthly = grouped_excess(records, "month")
    yearly = grouped_excess(records, "year")
    return {
        "total_annualized_excess": math.expm1(total_excess / years),
        "positive_month_rate": sum(value > 0 for value in monthly.values()) / len(monthly),
        "positive_year_rate": sum(value > 0 for value in yearly.values()) / len(yearly),
        "top_day_removal": removals,
        "top_10_excess_days": [
            {
                "date": row["date"],
                "strategy_return": math.expm1(row["strategy_log_return"]),
                "benchmark_return": math.expm1(row["benchmark_log_return"]),
                "excess_log_return": row["excess_log_return"],
            }
            for row in ranked[:10]
        ],
    }


def leave_one_year_out(records):
    years = sorted({row["year"] for row in records})
    output = []
    for omitted in years:
        remaining = [row for row in records if row["year"] != omitted]
        elapsed = len(remaining) / 365.2425
        excess = sum(row["excess_log_return"] for row in remaining)
        output.append(
            {
                "omitted_year": omitted,
                "remaining_annualized_excess": math.expm1(excess / elapsed),
                "still_beats_buy_and_hold": excess > 0,
            }
        )
    return output


def grouped_excess(records, key):
    output = {}
    for row in records:
        label = row[key]
        output[label] = output.get(label, 0.0) + row["excess_log_return"]
    return output


def markdown(payload):
    lines = [
        "# BTC 挑战者机制归因",
        "",
        "固定使用4h SMA `25/50/100/200`，逐项拆除熊市降仓、多头加杠杆和Funding过滤。",
        "所有信号使用完整4h K线并在下一根15m开盘执行，最大敞口1.75X。",
        "",
        "## 分段消融",
        "",
        "| 分段 | 方案 | 压力收益 | B&H | 压力超额 | DD |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for split, split_result in payload["splits"].items():
        baseline = split_result["buy_and_hold_price"]
        for variant_id, result in split_result["variants"].items():
            lines.append(
                f"| {split} | `{variant_id}` | {pct(result['stress']['net_return'])} | "
                f"{pct(baseline['net_return'])} | {pct(result['stress_excess'])} | "
                f"{pct(result['stress']['max_drawdown'])} |"
            )
    lines += [
        "",
        "## 候选逐年压力收益",
        "",
        "| 年份 | 完整候选 | B&H | 超额 |",
        "|---:|---:|---:|---:|",
    ]
    for row in payload["yearly_stress"]:
        candidate = row["variants"]["combined_with_funding"]
        lines.append(
            f"| {row['year']} | {pct(candidate)} | {pct(row['buy_and_hold'])} | "
            f"{pct(candidate - row['buy_and_hold'])} |"
        )
    concentration = payload["candidate_daily_concentration"]
    lines += [
        "",
        "## 超额集中度",
        "",
        f"- 正超额月份比例：{pct(concentration['positive_month_rate'])}",
        f"- 正超额年份比例：{pct(concentration['positive_year_rate'])}",
    ]
    for count, result in concentration["top_day_removal"].items():
        lines.append(
            f"- 最佳{count}天占全部正超额贡献："
            f"{pct(result['share_of_positive_excess'])}；剔除后年化超额："
            f"{pct(result['remaining_annualized_excess'])}"
        )
    lines += [
        "",
        "| 最佳相对日 | 策略收益 | B&H收益 | 对数超额 |",
        "|---|---:|---:|---:|",
    ]
    for row in concentration["top_10_excess_days"]:
        lines.append(
            f"| {row['date']} | {pct(row['strategy_return'])} | "
            f"{pct(row['benchmark_return'])} | {pct(row['excess_log_return'])} |"
        )
    lines += [
        "",
        "## Leave-one-year-out",
        "",
        "| 剔除年份 | 剩余年化超额 | 仍超过B&H |",
        "|---:|---:|---|",
    ]
    for row in payload["candidate_leave_one_year_out"]:
        lines.append(
            f"| {row['omitted_year']} | {pct(row['remaining_annualized_excess'])} | "
            f"{'是' if row['still_beats_buy_and_hold'] else '否'} |"
        )
    lines += [
        "",
        "## 结论",
        "",
        "熊市降仓是主要回撤控制来源；单独多头加杠杆提高收益，但会让回撤差于B&H。",
        "Funding过滤使完整组合在全样本中同时提高收益和改善回撤，但OOS阶段尚未产生增量。",
        "超额并不均匀：正超额月份不足一半，且剔除最佳10个相对日期后年化超额转负。",
        "因此该策略的经济逻辑是捕获少量危机防守和趋势爆发事件，必须经历足够长的"
        "前向周期，短期月度落后不能直接判定失效。",
        "",
    ]
    return "\n".join(lines) + "\n"


def utc_ms(year, month, day):
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
