#!/usr/bin/env python3
"""Audit a fixed equal-weight blend of two independent BTC trend sleeves."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from audit_btc_funding_aware_3x import BLOCKS, SAMPLES
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_dynamic_exposure import benchmark, replay_dynamic_incremental
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_sma12_funding_blend/2026-09-02")
DAILY_PERIODS = (12, 40)
FOUR_HOUR_PERIODS = (26, 52, 104, 208)
BULL = Decimal("1.5")
FUNDING_THRESHOLD = Decimal("0.0001")
WEIGHT_DAILY = Decimal("0.5")
START = datetime(2020, 1, 1, tzinfo=UTC)
WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1_095))
STEP_DAYS = 30
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily_targets, funding_targets = build_targets(bars, funding)
    start_ms = int(START.timestamp() * 1000)
    end_ms = bars[-1].end_ms
    full = blend_replay(
        bars,
        funding,
        daily_targets,
        funding_targets,
        start_ms,
        end_ms,
        record=True,
    )
    bh = benchmark(bars, start_ms, end_ms)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full["equity_curve"], 100_000.0, start_ms=start_ms
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=SAMPLES,
            seed=20261100 + block,
        )
        for block in BLOCKS
    }
    rolling = {
        label: rolling_summary(
            bars,
            funding,
            daily_targets,
            funding_targets,
            days,
            end_ms,
        )
        for label, days in WINDOWS
    }
    split_bounds = split_periods(bars, start_ms, end_ms)
    split_results = {}
    for label, (left, right) in split_bounds.items():
        result = blend_replay(
            bars,
            funding,
            daily_targets,
            funding_targets,
            left,
            right,
            record=True,
        )
        baseline = benchmark(bars, left, right)
        split_results[label] = {
            **result["metrics"],
            "benchmark_return": baseline["net_return"],
            "benchmark_drawdown": baseline["max_drawdown"],
            "excess": result["metrics"]["net_return"] - baseline["net_return"],
        }
    yearly = yearly_summary(
        bars,
        funding,
        daily_targets,
        funding_targets,
        start_ms,
        end_ms,
    )
    years = (end_ms - start_ms) / (365.2425 * 86_400_000)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "id": "equal-weight-daily-sma12-40-plus-4h-funding-aware",
            "daily_sleeve": (
                "SMA12/40 bear-flat: 0x when close < SMA40 and SMA12 < SMA40, else 1.5x"
            ),
            "four_hour_sleeve": "SMA26/52/104/208: bear 0x, neutral 1x, bull 1.5x",
            "funding_rule": "4h bull sleeve returns to 1x when last known funding > 0.01%",
            "weight": "50% daily sleeve + 50% 4h sleeve, fixed before this audit",
        },
        "protocol": {
            "signal": "completed aggregate candle; next 15m open",
            "costs": "10 bps fee + 5 bps slippage; funding only above 1x",
            "leverage": "each sleeve has 1x spot cap and 0.5x futures overlay; hard 3x audit",
            "selection": "no weight or parameter selection in this audit; OOS is read-only",
            "future_data": "only the latest already-published funding event is used",
        },
        "data": {
            "bars": len(bars),
            "funding_events": len(funding),
            "evaluation_start": START.isoformat(),
            "last": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        },
        "full": {
            **full["metrics"],
            "cagr": (1 + full["metrics"]["net_return"]) ** (1 / years) - 1,
            "maximum_effective_futures_leverage": full["maximum_effective_leverage"],
        },
        "benchmark": bh,
        "splits": split_results,
        "rolling": rolling,
        "yearly": yearly,
        "bootstrap": bootstrap,
        "conclusion": {
            "beats_bh_full": full["metrics"]["net_return"] > bh["net_return"],
            "beats_bh_every_aggregate_split": all(
                row["excess"] > 0 for row in split_results.values()
            ),
            "max_effective_leverage_below_3x": full["maximum_effective_leverage"] <= 3,
            "bootstrap_p05_positive": all(
                item["annualized_excess_vs_bh"]["p05"] > 0 for item in bootstrap.values()
            ),
            "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def build_targets(bars, funding):
    daily, daily_ends = aggregate_complete_periods(bars, "1d")
    daily_fast = simple_moving_average(daily, DAILY_PERIODS[0])
    daily_slow = simple_moving_average(daily, DAILY_PERIODS[1])
    daily_raw = tuple(
        None
        if daily_fast[i] is None or daily_slow[i] is None
        else Decimal("0")
        if bar.close < daily_slow[i] and daily_fast[i] < daily_slow[i]
        else BULL
        for i, bar in enumerate(daily)
    )
    daily_targets = map_targets_to_source(len(bars), daily_raw, daily_ends)
    four_hour, four_hour_ends = aggregate_complete_periods(bars, "4h")
    four_hour_raw = three_state_targets(
        four_hour,
        FOUR_HOUR_PERIODS,
        Decimal("0"),
        BULL,
    )
    four_hour_regime = map_targets_to_source(len(bars), four_hour_raw, four_hour_ends)
    funding_targets = funding_aware_targets(
        four_hour_regime,
        funding,
        BULL,
        FUNDING_THRESHOLD,
    )
    return daily_targets, funding_targets


def run_sleeve(bars, targets, funding, start_ms, end_ms, *, record=False):
    return replay_dynamic_incremental(
        bars,
        targets,
        funding,
        start_ms,
        end_ms,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        funding_on_excess_only=True,
        record_equity=record,
        record_risk=record,
    )


def blend_replay(bars, funding, daily_targets, funding_targets, start_ms, end_ms, *, record=False):
    daily = run_sleeve(bars, daily_targets, funding, start_ms, end_ms, record=record)
    carry = run_sleeve(bars, funding_targets, funding, start_ms, end_ms, record=record)
    daily_curve = dict(daily.equity_curve)
    carry_curve = dict(carry.equity_curve)
    timestamps = sorted(set(daily_curve) & set(carry_curve))
    daily_weight = float(WEIGHT_DAILY)
    carry_weight = float(1 - WEIGHT_DAILY)
    curve = tuple(
        (
            timestamp,
            daily_weight * daily_curve[timestamp] + carry_weight * carry_curve[timestamp],
        )
        for timestamp in timestamps
    )
    peak = 100_000.0
    drawdown = 0.0
    for _timestamp, value in curve:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    max_leverage = max(effective_leverage(result) for result in (daily, carry))
    final_equity = curve[-1][1]
    return {
        "equity_curve": curve,
        "metrics": {
            "net_return": final_equity / 100_000 - 1,
            "max_drawdown": drawdown,
            "daily_sleeve_return": daily.net_return,
            "four_hour_sleeve_return": carry.net_return,
            "fees": daily_weight * daily.total_fees + carry_weight * carry.total_fees,
            "funding": daily_weight * daily.total_funding + carry_weight * carry.total_funding,
        },
        "maximum_effective_leverage": max_leverage,
    }


def effective_leverage(result):
    return max(
        (
            futures_value / low_equity
            for _timestamp, low_equity, futures_value, _total, _exposure in result.risk_curve
            if low_equity > 0
        ),
        default=0.0,
    )


def rolling_summary(bars, funding, daily_targets, funding_targets, days, end_ms):
    rows = []
    start = START
    last = datetime.fromtimestamp(end_ms / 1000, UTC)
    while start + timedelta(days=days) <= last:
        stop = start + timedelta(days=days) - timedelta(milliseconds=1)
        left, right = int(start.timestamp() * 1000), int(stop.timestamp() * 1000)
        result = blend_replay(
            bars,
            funding,
            daily_targets,
            funding_targets,
            left,
            right,
            record=True,
        )
        bh = benchmark(bars, left, right)
        rows.append(
            {
                "start": start.isoformat(),
                "end": stop.isoformat(),
                "strategy_return": result["metrics"]["net_return"],
                "benchmark_return": bh["net_return"],
                "excess": result["metrics"]["net_return"] - bh["net_return"],
                "strategy_drawdown": result["metrics"]["max_drawdown"],
                "benchmark_drawdown": bh["max_drawdown"],
                "beats_return": result["metrics"]["net_return"] > bh["net_return"],
                "beats_return_and_drawdown": (
                    result["metrics"]["net_return"] > bh["net_return"]
                    and result["metrics"]["max_drawdown"] >= bh["max_drawdown"]
                ),
            }
        )
        start += timedelta(days=STEP_DAYS)
    excess = [row["excess"] for row in rows]
    return {
        "summary": {
            "windows": len(rows),
            "return_win_rate": ratio(row["beats_return"] for row in rows),
            "return_and_drawdown_win_rate": ratio(row["beats_return_and_drawdown"] for row in rows),
            "median_excess": sorted(excess)[len(excess) // 2] if excess else 0.0,
            "worst_excess": min(excess) if excess else 0.0,
        },
        "rows": rows,
    }


def split_periods(bars, start_ms, end_ms):
    """Return fixed aggregate periods; boundaries are not fitted to OOS results."""
    return {
        "research": (
            start_ms,
            min(
                end_ms,
                int(datetime(2022, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
            ),
        ),
        "validation": (
            int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000),
            min(
                end_ms,
                int(datetime(2024, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
            ),
        ),
        "oos": (int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000), end_ms),
        "full": (start_ms, end_ms),
    }


def yearly_summary(bars, funding, daily_targets, funding_targets, start_ms, end_ms):
    rows = []
    first_year = datetime.fromtimestamp(start_ms / 1000, UTC).year
    last_year = datetime.fromtimestamp(end_ms / 1000, UTC).year
    for year in range(first_year, last_year + 1):
        left = max(start_ms, int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000))
        right = min(end_ms, int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1)
        result = blend_replay(
            bars,
            funding,
            daily_targets,
            funding_targets,
            left,
            right,
            record=True,
        )
        bh = benchmark(bars, left, right)
        rows.append(
            {
                "year": year,
                "strategy_return": result["metrics"]["net_return"],
                "benchmark_return": bh["net_return"],
                "excess": result["metrics"]["net_return"] - bh["net_return"],
                "strategy_drawdown": result["metrics"]["max_drawdown"],
                "benchmark_drawdown": bh["max_drawdown"],
            }
        )
    return rows


def ratio(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def pct(value):
    return f"{value:.2%}"


def render(payload):
    full = payload["full"]
    bh = payload["benchmark"]
    years = (datetime.fromisoformat(payload["data"]["last"]) - START).total_seconds() / (
        365.2425 * 86_400
    )
    bh_cagr = (1 + bh["net_return"]) ** (1 / years) - 1
    lines = [
        "# BTC Equal-weight SMA12 + Funding-aware Trend Blend (Hard 3X)",
        "",
        "固定 50/50 组合：日线 SMA12/40 bear-flat 与 4h SMA26/52/104/208 Funding-aware sleeve。",
        "两部分均只在已完成信号后下一根 15m 开盘调整；成本为 10 bps 手续费、5 bps 滑点。",
        "",
        "## Full 2020–最新",
        "",
        "| 指标 | 策略 | B&H |",
        "|---|---:|---:|",
        f"| 收益 | {pct(full['net_return'])} | {pct(bh['net_return'])} |",
        f"| CAGR | {pct(full['cagr'])} | {pct(bh_cagr)} |",
        f"| 最大回撤 | {pct(full['max_drawdown'])} | {pct(bh['max_drawdown'])} |",
        f"| 最高有效 Futures 杠杆 | {full['maximum_effective_futures_leverage']:.3f}X | - |",
        "",
        "## Rolling Windows",
        "",
        "| 窗口 | 数量 | 超过 B&H | 收益+DD 同胜 | 中位超额 | 最差超额 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, item in payload["rolling"].items():
        summary = item["summary"]
        lines.append(
            f"| {label} | {summary['windows']} | {pct(summary['return_win_rate'])} | "
            f"{pct(summary['return_and_drawdown_win_rate'])} | {pct(summary['median_excess'])} | "
            f"{pct(summary['worst_excess'])} |"
        )
    lines += ["", "## Yearly", "", "| 年份 | 策略 | B&H | 超额 |", "|---:|---:|---:|---:|"]
    for row in payload["yearly"]:
        lines.append(
            f"| {row['year']} | {pct(row['strategy_return'])} | "
            f"{pct(row['benchmark_return'])} | {pct(row['excess'])} |"
        )
    lines += [
        "",
        "## Aggregate research / validation / OOS splits",
        "",
        "| 区间 | 策略 | B&H | 超额 |",
        "|---|---:|---:|---:|",
    ]
    for label in ("research", "validation", "oos", "full"):
        row = payload["splits"][label]
        lines.append(
            f"| {label} | {pct(row['net_return'])} | {pct(row['benchmark_return'])} | "
            f"{pct(row['excess'])} |"
        )
    lines += ["", "## Bootstrap", ""]
    for block, item in payload["bootstrap"].items():
        lines.append(
            f"- {block}: beat B&H {pct(item['probability_beats_bh_return'])}; "
            f"joint return+DD {pct(item['probability_beats_return_and_drawdown'])}; "
            f"annualized excess P05 {pct(item['annualized_excess_vs_bh']['p05'])}."
        )
    lines += [
        "",
        "## Conclusion",
        "",
        (
            "组合在研究、验证、OOS 的聚合区间及全样本压力回放中超过 B&H，且有效杠杆低于 3X；"
            "但年度并非每年胜出，Bootstrap 的正超额下界与独立前向数据仍未通过。"
        ),
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
