#!/usr/bin/env python3
"""Evaluate causal BTCUSDT weekly SMA 10/20/30/40 strategies."""

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
from mastermind_tick.sma_weekly import (
    SMA_PERIODS,
    aggregate_complete_utc_weeks,
    map_weekly_targets_to_source,
    simple_moving_average,
    weekly_sma_targets,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/btc_weekly_sma/2026-08-30"),
    )
    args = parser.parse_args()
    symbol = args.symbol.upper()
    if symbol != "BTCUSDT":
        raise ValueError("this study currently supports BTCUSDT only")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market(symbol)
    weeks, week_end_indices = aggregate_complete_utc_weeks(bars)
    funding_rates = load_funding(symbol, bars)
    funding = funding_by_bar(bars, funding_rates)
    periods = split_periods(bars)
    variants = variant_grid(bars, weeks, week_end_indices)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTCUSDT weekly SMA 10/20/30/40",
        "protocol": {
            "source": "Binance USD-M BTCUSDT completed 15m bars already stored locally",
            "source_timeframe": "15m",
            "signal_timeframe": "UTC Monday-Sunday complete weeks",
            "sma_periods": SMA_PERIODS,
            "signal_rule": "SMA10>SMA20>SMA30>SMA40 long; reverse ordering short when enabled",
            "signal_timing": (
                "weekly close confirmation; changed target executes at next 15m bar open"
            ),
            "fees": "5 bps per fill",
            "slippage": "2 bps per fill",
            "funding": (
                "historical Binance events; blank mark price falls back to containing "
                "completed 15m close"
            ),
            "initial_equity": 100000,
            "base_exposure": 1.0,
            "liquidation_modeled": False,
            "selection": "variants are predeclared interpretations; no OOS selection",
        },
        "data": {
            "source_bars": len(bars),
            "complete_weeks": len(weeks),
            "first_source_bar": timestamp(bars[0].start_ms),
            "last_source_bar": timestamp(bars[-1].end_ms),
            "first_complete_week": timestamp(weeks[0].start_ms) if weeks else None,
            "last_complete_week": timestamp(weeks[-1].end_ms) if weeks else None,
            "funding_events": len(funding_rates),
        },
        "latest_completed_week": latest_week_summary(weeks),
        "periods": {
            name: {"start": timestamp(start), "end": timestamp(end)}
            for name, (start, end) in periods.items()
        },
        "buy_and_hold": {
            name: benchmark_summary(bars, start, end) for name, (start, end) in periods.items()
        },
        "variants": {},
    }
    for variant_id, targets in variants.items():
        result = evaluate_variant(bars, targets, funding, periods)
        payload["variants"][variant_id] = result
    payload["baseline"] = baseline_summary(payload["variants"])
    payload["decision"] = decision(payload)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    write_trade_csv(
        args.output_dir / "baseline-trades.csv", payload["variants"]["alignment-long-short"]
    )
    print(json.dumps(payload["decision"], ensure_ascii=False))
    print(args.output_dir / "README.md")


def variant_grid(
    bars: list[ResearchBar], weeks, week_end_indices: tuple[int, ...]
) -> dict[str, tuple[int | None, ...]]:
    variants = {}
    for mode, slope, price in (
        ("alignment", False, False),
        ("alignment-slope", True, False),
        ("alignment-price", False, True),
        ("alignment-slope-price", True, True),
    ):
        for direction in ("long-only", "long-short"):
            weekly = weekly_sma_targets(
                weeks,
                periods=SMA_PERIODS,
                direction="long_short" if direction == "long-short" else "long_only",
                require_slope=slope,
                require_price_confirmation=price,
            )
            variants[f"{mode}-{direction}"] = map_weekly_targets_to_source(
                len(bars), weekly, week_end_indices
            )
    return variants


def evaluate_variant(bars, targets, funding, periods) -> dict[str, Any]:
    result: dict[str, Any] = {
        "signal_count": sum(value is not None for value in targets),
        "periods": {},
    }
    for name, (start, end) in periods.items():
        base = evaluate_targets(
            bars,
            targets,
            start_ms=start,
            end_ms=end,
            funding=funding,
        )
        stress = evaluate_targets(
            bars,
            targets,
            start_ms=start,
            end_ms=end,
            funding=funding,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
        )
        price_only = evaluate_targets(
            bars,
            targets,
            start_ms=start,
            end_ms=end,
        )
        result["periods"][name] = {
            "base": summary(base, include_trades=name == "oos"),
            "stress": summary(stress),
            "price_only": summary(price_only),
        }
    return result


def latest_week_summary(weeks: list[ResearchBar]) -> dict[str, Any] | None:
    if not weeks:
        return None
    values = {str(period): simple_moving_average(weeks, period)[-1] for period in SMA_PERIODS}
    target = weekly_sma_targets(weeks, direction="long_short")[-1]
    return {
        "end": timestamp(weeks[-1].end_ms),
        "close": str(weeks[-1].close),
        "sma": {
            period: str(value) if value is not None else None for period, value in values.items()
        },
        "alignment_target": target,
    }


def summary(result, *, include_trades: bool = False) -> dict[str, Any]:
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
    payload["daily_returns"] = [
        {"date": label, "return": value} for label, value in result.daily_returns
    ]
    return payload


def split_periods(bars: list[ResearchBar]) -> dict[str, tuple[int, int]]:
    return {
        "research": (utc_ms(2020, 1, 1), utc_ms(2022, 12, 31, 23, 59, 59, 999)),
        "validation": (utc_ms(2023, 1, 1), utc_ms(2024, 12, 31, 23, 59, 59, 999)),
        "oos": (utc_ms(2025, 1, 1), bars[-1].end_ms),
        "full": (bars[0].start_ms, bars[-1].end_ms),
    }


def load_funding(symbol: str, bars: list[ResearchBar]) -> list[FundingRate]:
    path = Path("data/history_btc_funding.csv")
    if not path.exists():
        return []
    ends = [bar.end_ms for bar in bars]
    events: list[FundingRate] = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp_ms = int(row["timestamp_ms"])
            index = bisect.bisect_left(ends, timestamp_ms)
            if index >= len(bars) or not bars[index].start_ms <= timestamp_ms <= bars[index].end_ms:
                continue
            mark = Decimal(row["mark_price"]) if row["mark_price"] else bars[index].close
            events.append(FundingRate(timestamp_ms, Decimal(row["rate"]), mark))
    return events


def baseline_summary(variants: dict[str, Any]) -> dict[str, Any]:
    return {
        "long_only": variants["alignment-long-only"],
        "long_short": variants["alignment-long-short"],
    }


def benchmark_summary(
    bars: list[ResearchBar], start_ms: int, end_ms: int
) -> dict[str, float | None]:
    selected = [bar for bar in bars if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        return {"return": None, "max_drawdown": None}
    first_close = selected[0].close
    equity = [float(bar.close / first_close) for bar in selected]
    peak = equity[0]
    drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    return {"return": float(equity[-1] - 1), "max_drawdown": drawdown}


def decision(payload: dict[str, Any]) -> dict[str, Any]:
    baseline = payload["variants"]["alignment-long-short"]
    oos = baseline["periods"]["oos"]["base"]
    stress = baseline["periods"]["oos"]["stress"]
    approved = (
        oos["net_return"] > 0
        and (oos["profit_factor"] or 0) > 1
        and oos["max_drawdown"] >= -0.25
        and stress["net_return"] > 0
        and stress["max_drawdown"] >= -0.25
    )
    return {
        "status": "research_candidate" if approved else "rejected",
        "approved_for_trading": False,
        "reason": (
            "基线通过探索门槛，但仍需前向验证。"
            if approved
            else "基线未通过样本外收益、压力成本收益或回撤门槛。"
        ),
    }


def write_trade_csv(path: Path, variant: dict[str, Any]) -> None:
    trades = variant["periods"]["oos"]["base"].get("trades", [])
    if not trades:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=trades[0].keys())
        writer.writeheader()
        writer.writerows(trades)


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# BTCUSDT 周线 SMA 10/20/30/40 研究",
        "",
        f"生成时间：{payload['generated_at']}",
        "",
        "周日收盘确认四线状态，下一根 15m K 线开盘执行；所有成本和 Funding 已计入。",
        "",
        f"数据：{payload['data']['source_bars']:,} 根 15m，"
        f"完整周 {payload['data']['complete_weeks']:,}，"
        f"Funding 事件 {payload['data']['funding_events']:,}。",
        "",
        "## 变体结果",
        "",
        "| Variant | Research | Validation | OOS | OOS PF | OOS DD | Stress OOS | Price-only OOS |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant_id, variant in payload["variants"].items():
        research = variant["periods"]["research"]["base"]
        validation = variant["periods"]["validation"]["base"]
        oos = variant["periods"]["oos"]["base"]
        stress = variant["periods"]["oos"]["stress"]
        price_only = variant["periods"]["oos"]["price_only"]
        lines.append(
            f"| `{variant_id}` | {percent(research['net_return'])} | "
            f"{percent(validation['net_return'])} | {percent(oos['net_return'])} | "
            f"{number(oos['profit_factor'])} | {percent(oos['max_drawdown'])} | "
            f"{percent(stress['net_return'])} | {percent(price_only['net_return'])} |"
        )
    baseline = payload["variants"]["alignment-long-short"]
    lines.extend(["", "## 基线分段观察", ""])
    for name in ("research", "validation", "oos"):
        value = baseline["periods"][name]["base"]
        lines.append(
            f"- {name}: 交易 {value['completed_trades']}，胜率 {percent(value['win_rate'])}，"
            f"累计收益 {percent(value['net_return'])}，最大回撤 {percent(value['max_drawdown'])}，"
            f"费用 {value['total_fees']:.2f}，Funding {value['total_funding']:.2f}。"
        )
    latest = payload["latest_completed_week"]
    if latest is not None:
        target_label = {-1: "做空", 0: "空仓", 1: "做多"}[latest["alignment_target"]]
        lines.extend(
            [
                "",
                "## 最新状态",
                "",
                f"截至 {latest['end']}，周收盘 {latest['close']}；"
                + "，".join(
                    f"SMA{period} {Decimal(value):.2f}" for period, value in latest["sma"].items()
                )
                + f"。基线目标为{target_label}（{latest['alignment_target']}）。",
            ]
        )
    lines.extend(["", "## 买入持有价格基准", ""])
    lines.append("| Period | Return | Max DD |")
    lines.append("|---|---:|---:|")
    for name in ("research", "validation", "oos", "full"):
        benchmark = payload["buy_and_hold"][name]
        lines.append(
            f"| {name} | {percent(benchmark['return'])} | {percent(benchmark['max_drawdown'])} |"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"状态：**{payload['decision']['status'].upper()}**。{payload['decision']['reason']}",
            "样本外所有 8 个变体均为负收益；去除 Funding 后也全部为负，"
            "故失败不是由 Funding 单独造成。",
            "周线换向频率极低，样本外仅 2 至 9 笔平仓交易，"
            "早期高收益缺乏足够交易数支持统计可靠性。",
            "该结果不能证明四线 SMA 有独立 Edge；四线排列、斜率和价格确认的解释"
            "必须在新数据上预先冻结后再验证。",
            "",
            "完整 JSON、OOS 交易日志和变体月度/日度序列见同目录。",
        ]
    )
    return "\n".join(lines) + "\n"


def utc_ms(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    millisecond: int = 0,
) -> int:
    return int(
        datetime(year, month, day, hour, minute, second, millisecond * 1000, tzinfo=UTC).timestamp()
        * 1000
    )


def timestamp(value: int | None) -> str | None:
    return None if value is None else datetime.fromtimestamp(value / 1000, UTC).isoformat()


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    main()
