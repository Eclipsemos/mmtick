#!/usr/bin/env python3
"""Research causal BTCUSDT four-SMA ordering across timeframes and durations."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from research_macd_divergence import load_market

from mastermind_tick.bar_research import ResearchBar, evaluate_targets, funding_by_bar
from mastermind_tick.models import FundingRate
from mastermind_tick.sma_trend import (
    TIMEFRAME_MINUTES,
    aggregate_complete_periods,
    four_sma_targets,
    map_targets_to_source,
)

PERIOD_GRID = (
    (5, 10, 20, 40),
    (8, 13, 21, 34),
    (10, 20, 30, 40),
    (10, 30, 60, 120),
    (20, 40, 80, 160),
    (5, 20, 50, 100),
    (10, 25, 50, 100),
    (15, 30, 60, 120),
    # Neighbors around the leading slow 4h configuration, for robustness checks.
    (16, 32, 64, 128),
    (18, 36, 72, 144),
    (22, 44, 88, 176),
    (24, 48, 96, 192),
    (25, 50, 100, 200),
    (26, 52, 104, 208),
    (28, 56, 112, 224),
    (30, 60, 120, 240),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("reports/experiments/btc_sma_trend/2026-09-01")
    )
    args = parser.parse_args()
    symbol = args.symbol.upper()
    if symbol != "BTCUSDT":
        raise ValueError("this study currently supports BTCUSDT only")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market(symbol)
    funding_rates = load_funding(symbol, bars)
    funding = funding_by_bar(bars, funding_rates)
    periods = split_periods(bars)
    rows: list[dict[str, Any]] = []
    total = len(TIMEFRAME_MINUTES) * len(PERIOD_GRID) * 2
    done = 0
    for timeframe in TIMEFRAME_MINUTES:
        aggregated, source_indices = aggregate_complete_periods(bars, timeframe)
        for sma_periods in PERIOD_GRID:
            for confirmation in (False, True):
                period_targets = four_sma_targets(
                    aggregated, sma_periods, require_price_confirmation=confirmation
                )
                targets = map_targets_to_source(len(bars), period_targets, source_indices)
                result = evaluate_variant(bars, targets, funding, periods)
                row = {
                    "id": (
                        f"{timeframe}-{'-'.join(map(str, sma_periods))}-price"
                        f"{'-yes' if confirmation else '-no'}"
                    ),
                    "timeframe": timeframe,
                    "sma_periods": sma_periods,
                    "price_confirmation": confirmation,
                    "aggregated_bars": len(aggregated),
                    "signal_count": sum(value is not None for value in targets),
                    "periods": result,
                }
                rows.append(row)
                done += 1
                if done % 8 == 0 or done == total:
                    print(f"completed {done}/{total}", flush=True)
    rows.sort(key=lambda row: score(row), reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTCUSDT four-SMA ordering, long-only; exit when ordering breaks",
        "protocol": {
            "source": "Binance USD-M BTCUSDT completed 15m bars stored locally",
            "timeframes": list(TIMEFRAME_MINUTES),
            "period_grid": PERIOD_GRID,
            "signal": "SMA1>SMA2>SMA3>SMA4; optional close above all four",
            "timing": "completed aggregate candle; target executes at next 15m open",
            "costs": "5 bps fee and 2 bps slippage per fill; historical funding included",
            "direction": "long-only (ordering failure exits to flat)",
            "splits": {name: [iso(start), iso(end)] for name, (start, end) in periods.items()},
        },
        "data": {
            "source_bars": len(bars),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
            "funding_events": len(funding_rates),
        },
        "buy_and_hold": {
            name: benchmark(bars, start, end) for name, (start, end) in periods.items()
        },
        "results": rows,
        "top_oos": [compact(row) for row in rows[:15]],
    }
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(args.output_dir / "README.md")


def evaluate_variant(bars, targets, funding, periods):
    output = {}
    for name, (start, end) in periods.items():
        base = evaluate_targets(bars, targets, start_ms=start, end_ms=end, funding=funding)
        output[name] = summary(base, include_trades=name == "oos")
    return output


def summary(result, *, include_trades=False):
    payload = asdict(result)
    trades = payload.pop("trades")
    if include_trades:
        payload["trades"] = [
            {
                **trade,
                "entry_price": str(trade["entry_price"]),
                "exit_price": str(trade["exit_price"]),
                "fees": str(trade["fees"]),
                "funding": str(trade["funding"]),
                "net_pnl": str(trade["net_pnl"]),
            }
            for trade in trades
        ]
    payload["monthly_returns"] = [
        {"month": label, "return": value} for label, value in result.monthly_returns
    ]
    return payload


def split_periods(bars):
    research_start = utc_ms(2020, 1, 1)
    return {
        "research": (research_start, utc_ms(2022, 12, 31, 23, 59, 59, 999)),
        "validation": (utc_ms(2023, 1, 1), utc_ms(2024, 12, 31, 23, 59, 59, 999)),
        "oos": (utc_ms(2025, 1, 1), bars[-1].end_ms),
        "full": (max(research_start, bars[0].start_ms), bars[-1].end_ms),
    }


def load_funding(symbol: str, bars: list[ResearchBar]) -> list[FundingRate]:
    path = Path("data/history_btc_funding.csv")
    if not path.exists():
        return []
    ends = [bar.end_ms for bar in bars]
    events = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp_ms = int(row["timestamp_ms"])
            index = bisect.bisect_left(ends, timestamp_ms)
            if index >= len(bars) or not bars[index].start_ms <= timestamp_ms <= bars[index].end_ms:
                continue
            mark = Decimal(row["mark_price"]) if row["mark_price"] else bars[index].close
            events.append(FundingRate(timestamp_ms, Decimal(row["rate"]), mark))
    return events


def benchmark(bars, start_ms, end_ms):
    selected = [bar for bar in bars if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        return {"return": None, "max_drawdown": None}
    first = selected[0].close
    curve = [float(bar.close / first) for bar in selected]
    peak = curve[0]
    drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return {"return": curve[-1] - 1, "max_drawdown": drawdown}


def score(row):
    oos = row["periods"]["oos"]
    return oos["net_return"] if oos["completed_trades"] >= 3 else -999.0


def compact(row):
    oos = row["periods"]["oos"]
    return {
        "id": row["id"],
        "oos_return": oos["net_return"],
        "oos_dd": oos["max_drawdown"],
        "trades": oos["completed_trades"],
        "pf": oos["profit_factor"],
    }


def markdown(payload):
    lines = [
        "# BTCUSDT 多周期四 SMA 趋势研究",
        "",
        f"生成时间：{payload['generated_at']}",
        "",
        "只在四条 SMA 严格升序（快线到慢线）时持有多头；"
        "排序失效后下一根 15m 开盘平仓。信号仅使用已完成聚合 K 线，"
        "计入 5 bps 手续费、2 bps 滑点和 Funding。周期数字以对应聚合 K 线根数计。",
        "",
        f"数据：{payload['data']['source_bars']:,} 根 15m，"
        f"{payload['data']['first']} 至 {payload['data']['last']}，"
        f"Funding 事件 {payload['data']['funding_events']:,}。",
        "",
        "## OOS 排名（2025 至最新）",
        "",
        "| 配置 | OOS收益 | OOS最大回撤 | 交易数 | PF | 研究 | 验证 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"][:30]:
        oos, research, validation = (
            row["periods"]["oos"],
            row["periods"]["research"],
            row["periods"]["validation"],
        )
        lines.append(
            f"| `{row['id']}` | {pct(oos['net_return'])} | "
            f"{pct(oos['max_drawdown'])} | {oos['completed_trades']} | "
            f"{num(oos['profit_factor'])} | {pct(research['net_return'])} | "
            f"{pct(validation['net_return'])} |"
        )
    lines += [
        "",
        "## 解读",
        "",
        "排名按 OOS 收益展示，不把 OOS 结果用于重新选择参数。重点看跨研究、"
        "验证、OOS 是否同向，以及交易数和回撤是否足够可靠。",
        "",
    ]
    for name, value in payload["buy_and_hold"].items():
        lines.append(
            f"- 买入持有 {name}：收益 {pct(value['return'])}，"
            f"最大回撤 {pct(value['max_drawdown'])}。"
        )
    positive = [
        r
        for r in payload["results"]
        if r["periods"]["oos"]["net_return"] > 0 and r["periods"]["oos"]["completed_trades"] >= 3
    ]
    lines += [
        "",
        f"正 OOS 收益配置：{len(positive)}/{len(payload['results'])}。"
        "这只是探索结果，不能替代前向测试或实盘批准。",
        "",
    ]
    return "\n".join(lines)


def utc_ms(year, month, day, hour=0, minute=0, second=0, millisecond=0):
    return int(
        datetime(year, month, day, hour, minute, second, millisecond * 1000, tzinfo=UTC).timestamp()
        * 1000
    )


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return "n/a" if value is None else f"{value:.2%}"


def num(value):
    return "n/a" if value is None else f"{value:.2f}"


if __name__ == "__main__":
    main()
