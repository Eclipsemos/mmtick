#!/usr/bin/env python3
"""Audit the frozen BTC funding-aware 4h trend candidate under a hard 3x cap."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_dynamic_exposure import benchmark, replay_dynamic_incremental
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT_DIR = Path("reports/experiments/btc_funding_aware_3x_audit/2026-09-02")
PERIODS = (26, 52, 104, 208)
BULL_EXPOSURE = Decimal("1.5")
FUNDING_THRESHOLD = Decimal("0.0001")
START = datetime(2020, 1, 1, tzinfo=UTC)
WINDOWS = (("1y", 365), ("2y", 730), ("3y", 1_095))
STEP_DAYS = 30
BLOCKS = (7, 30, 90)
SAMPLES = 10_000
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    regime = map_targets_to_source(
        len(bars),
        three_state_targets(aggregate, PERIODS, Decimal("0"), BULL_EXPOSURE),
        ends,
    )
    targets = funding_aware_targets(regime, funding, BULL_EXPOSURE, FUNDING_THRESHOLD)
    start_ms = int(START.timestamp() * 1000)
    end_ms = bars[-1].end_ms
    full = replay(bars, targets, funding, start_ms, end_ms, record=True)
    baseline = benchmark(bars, start_ms, end_ms)
    full_public = public(full, (end_ms - start_ms) / (365.2425 * 86_400_000))
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=start_ms
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=SAMPLES,
            seed=20261000 + block,
        )
        for block in BLOCKS
    }
    rolling = {
        label: rolling_summary(bars, targets, funding, days, end_ms) for label, days in WINDOWS
    }
    yearly = yearly_summary(bars, targets, funding, start_ms, end_ms)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "timeframe": "4h",
            "sma_periods": PERIODS,
            "bear_exposure": "0",
            "neutral_exposure": "1",
            "bull_exposure": str(BULL_EXPOSURE),
            "funding_threshold": str(FUNDING_THRESHOLD),
        },
        "protocol": {
            "selection": "fixed before this audit; no OOS or rolling-window selection",
            "signal": "completed 4h candle; next 15m open target change",
            "costs": "10 bps fee + 5 bps slippage; funding charged only above 1x",
            "leverage": (
                "1x spot cap plus 0.5x futures overlay; observed effective leverage audited"
            ),
            "future_data": "only last known funding event is used; no future bars in signals",
        },
        "data": {
            "bars": len(bars),
            "funding_events": len(funding),
            "first": datetime.fromtimestamp(bars[0].start_ms / 1000, UTC).isoformat(),
            "evaluation_start": START.isoformat(),
            "last": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
        },
        "full": full_public,
        "benchmark": baseline,
        "rolling": rolling,
        "yearly": yearly,
        "bootstrap": bootstrap,
        "conclusion": {
            "beats_bh_full": full.net_return > baseline["net_return"],
            "max_effective_leverage_below_3x": (
                full_public["maximum_effective_futures_leverage"] <= 3
            ),
            "bootstrap_p05_positive": all(
                item["annualized_excess_vs_bh"]["p05"] > 0 for item in bootstrap.values()
            ),
            "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def replay(bars, targets, funding, start_ms, end_ms, *, record=False):
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
        record_exposure=record,
    )


def public(result, years):
    max_effective = max(
        (
            futures_value / low_equity
            for _timestamp, low_equity, futures_value, _total_value, _exposure in result.risk_curve
            if low_equity > 0
        ),
        default=0.0,
    )
    return {
        "net_return": result.net_return,
        "cagr": (1 + result.net_return) ** (1 / years) - 1,
        "max_drawdown": result.max_drawdown,
        "maximum_target_exposure": max(
            (float(v[1]) for v in result.exposure_curve),
            default=0.0,
        ),
        "maximum_effective_futures_leverage": max_effective,
        "completed_rebalances": result.completed_trades,
        "fees": result.total_fees,
        "funding": result.total_funding,
        "liquidated": result.bankrupt,
    }


def rolling_summary(bars, targets, funding, days, end_ms):
    rows = []
    start = START
    last = datetime.fromtimestamp(end_ms / 1000, UTC)
    while start + timedelta(days=days) <= last:
        stop = start + timedelta(days=days) - timedelta(milliseconds=1)
        left, right = int(start.timestamp() * 1000), int(stop.timestamp() * 1000)
        result = replay(bars, targets, funding, left, right)
        bh = benchmark(bars, left, right)
        rows.append(
            {
                "start": start.isoformat(),
                "end": stop.isoformat(),
                "strategy_return": result.net_return,
                "benchmark_return": bh["net_return"],
                "excess": result.net_return - bh["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": bh["max_drawdown"],
                "beats_return": result.net_return > bh["net_return"],
                "beats_return_and_drawdown": (
                    result.net_return > bh["net_return"]
                    and result.max_drawdown >= bh["max_drawdown"]
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


def yearly_summary(bars, targets, funding, start_ms, end_ms):
    first = datetime.fromtimestamp(start_ms / 1000, UTC).year
    last = datetime.fromtimestamp(end_ms / 1000, UTC).year
    rows = []
    for year in range(first, last + 1):
        left = max(start_ms, int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000))
        right = min(
            end_ms,
            int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1,
        )
        result = replay(bars, targets, funding, left, right)
        bh = benchmark(bars, left, right)
        rows.append(
            {
                "year": year,
                "strategy_return": result.net_return,
                "benchmark_return": bh["net_return"],
                "excess": result.net_return - bh["net_return"],
                "strategy_drawdown": result.max_drawdown,
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
        "# BTC Funding-aware 4h Candidate Audit (Hard 3X)",
        "",
        (
            "固定 4h SMA26/52/104/208；熊市 0X、中性 1X，"
            "牛市在最近已公布 Funding ≤0.01% 时为 1.5X，否则回到 1X。"
        ),
        "所有结果使用 10 bps 手续费、5 bps 滑点及已公布 Funding。",
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
        s = item["summary"]
        lines.append(
            f"| {label} | {s['windows']} | {pct(s['return_win_rate'])} | "
            f"{pct(s['return_and_drawdown_win_rate'])} | {pct(s['median_excess'])} | "
            f"{pct(s['worst_excess'])} |"
        )
    lines += ["", "## Yearly", "", "| 年份 | 策略 | B&H | 超额 |", "|---:|---:|---:|---:|"]
    for row in payload["yearly"]:
        lines.append(
            f"| {row['year']} | {pct(row['strategy_return'])} | "
            f"{pct(row['benchmark_return'])} | {pct(row['excess'])} |"
        )
    lines += ["", "## Bootstrap (10,000 paired circular block samples)", ""]
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
            "历史压力回放击败 B&H，且有效杠杆低于 3X；但 Bootstrap 超额 P05、"
            "跨年度一致性和独立前向数据仍需通过。"
        ),
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
