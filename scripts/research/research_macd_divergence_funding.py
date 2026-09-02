#!/usr/bin/env python3
"""Replay frozen MACD-divergence candidates with historical funding costs."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from research_macd_divergence import load_market
from research_macd_divergence_timeframes import aggregate_bars, load_5m_market

from mastermind_tick.macd_divergence import (
    DivergenceConfig,
    ExecutionConfig,
    divergence_structures,
    entry_signals,
    indicator_series,
    replay_signals,
    swing_points,
)
from mastermind_tick.models import FundingRate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("reports/experiments/macd_divergence/2026-08-28/results.json"),
    )
    parser.add_argument(
        "--timeframe-input",
        type=Path,
        default=Path(
            "reports/experiments/macd_divergence/2026-08-28/timeframes"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/macd_divergence/2026-08-28/funding"),
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    base = json.loads(args.input.read_text(encoding="utf-8"))
    output: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "frozen_candidates_source": str(args.input),
            "funding_source": "Binance USD-M /fapi/v1/fundingRate",
            "blank_mark_price_fallback": "close of completed bar containing event",
            "funding_timing": "apply at containing bar start before intrabar exit checks",
            "selection_uses_funding_oos": False,
        },
        "symbols": {},
    }
    for symbol, item in base["symbols"].items():
        bars = load_market(symbol)
        rates = load_funding(symbol, bars)
        selected = item["selected"]
        output["symbols"][symbol] = evaluate(
            symbol,
            bars,
            rates,
            selected,
            timeframe_label="15m",
        )
    for path in sorted(args.timeframe_input.glob("*-timeframes.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        symbol = payload["symbol"]
        source = load_5m_market(symbol)
        rates = load_funding(symbol, source)
        for timeframe, item in payload["timeframes"].items():
            interval = int(timeframe)
            bars = source if interval == 5 else aggregate_bars(source, interval)
            output["symbols"].setdefault(symbol, {})
            output["symbols"][symbol].setdefault("timeframes", {})[timeframe] = evaluate(
                symbol,
                bars,
                rates,
                item["selected"],
                timeframe_label=f"{timeframe}m",
            )
    (args.output_dir / "results.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(markdown(output), encoding="utf-8")
    print(args.output_dir / "README.md")


def evaluate(
    symbol: str,
    bars,
    rates: list[FundingRate],
    selected: dict[str, Any],
    *,
    timeframe_label: str,
) -> dict[str, Any]:
    config = DivergenceConfig(**selected["structure"])
    execution = ExecutionConfig(**selected["execution"])
    indicators = indicator_series(bars)
    lows = swing_points(bars, indicators.histogram, config, "low")
    highs = swing_points(bars, indicators.histogram, config, "high")
    structures = tuple(
        sorted(
            divergence_structures(lows, indicators.atr, config.points, "LONG")
            + divergence_structures(highs, indicators.atr, config.points, "SHORT"),
            key=lambda item: (item.known_at, item.id),
        )
    )
    signals = entry_signals(structures, indicators.histogram)
    starts = [bar.start_ms for bar in bars]
    split_2023 = bisect.bisect_left(starts, utc_ms(2023, 1, 1))
    split_2025 = bisect.bisect_left(starts, utc_ms(2025, 1, 1))
    periods = {
        "research": (0, split_2023),
        "validation": (split_2023, split_2025),
        "oos": (split_2025, len(bars)),
        "full": (0, len(bars)),
    }
    result = {
        "candidate": selected["id"],
        "timeframe": timeframe_label,
        "bars": len(bars),
        "funding_events": len(rates),
        "signal_count": len(signals),
        "periods": {},
    }
    for name, (left, right) in periods.items():
        if left >= right:
            result["periods"][name] = None
            continue
        no_funding = replay_signals(
            bars,
            indicators,
            signals,
            execution,
            symbol=symbol,
            timeframe_minutes=int(timeframe_label.removesuffix("m")),
            start_index=left,
            end_index=right,
        )
        with_funding = replay_signals(
            bars,
            indicators,
            signals,
            execution,
            symbol=symbol,
            timeframe_minutes=int(timeframe_label.removesuffix("m")),
            funding=rates,
            start_index=left,
            end_index=right,
        )
        result["periods"][name] = {
            "without_funding": summary(no_funding),
            "with_funding": summary(with_funding),
            "funding_delta": with_funding.funding_paid,
        }
    return result


def load_funding(symbol: str, bars) -> list[FundingRate]:
    suffix = "btc" if symbol == "BTCUSDT" else "eth"
    path = Path(f"data/history_{suffix}_funding.csv")
    if not path.exists():
        raise FileNotFoundError(path)
    starts = [bar.start_ms for bar in bars]
    ends = [bar.end_ms for bar in bars]
    events = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp_ms = int(row["timestamp_ms"])
            index = bisect.bisect_left(ends, timestamp_ms)
            if index >= len(bars) or not starts[index] <= timestamp_ms <= ends[index]:
                continue
            mark = Decimal(row["mark_price"]) if row["mark_price"] else bars[index].close
            events.append(FundingRate(timestamp_ms, Decimal(row["rate"]), mark))
    return events


def summary(result) -> dict[str, Any]:
    return {
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "average_r": result.average_r,
        "expectancy_r": result.expectancy_r,
        "profit_factor": result.profit_factor,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "fees_paid": result.fees_paid,
        "funding_paid": result.funding_paid,
        "slippage_cost": result.slippage_cost,
        "ambiguous_bars": result.ambiguous_bars,
    }


def utc_ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 币圈半神 MACD 背离：Funding 复核",
        "",
        "参数候选先在无 Funding 报告中冻结；本报告只比较加入历史 Funding 前后的变化，"
        "不使用 Funding OOS 结果重新选择参数。空 `markPrice` 事件使用所在已完成 K 线收盘价。",
        "",
    ]
    for symbol, item in payload["symbols"].items():
        lines.extend([f"## {symbol}", ""])
        rows = [("15m", item)]
        rows.extend(sorted(item.get("timeframes", {}).items(), key=lambda row: int(row[0])))
        lines.extend(
            [
                "| 周期 | 分区 | 交易 | 无 Funding 收益 | 有 Funding 收益 | "
                "Funding | 有 Funding 平均 R | 有 Funding PF |",
                "|---|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for timeframe, result in rows:
            for period in ("research", "validation", "oos", "full"):
                values = result["periods"].get(period)
                if values is None:
                    continue
                before = values["without_funding"]
                after = values["with_funding"]
                lines.append(
                    f"| {timeframe} | {period} | {after['total_trades']} | "
                    f"{percent(before['net_return'])} | {percent(after['net_return'])} | "
                    f"{number(values['funding_delta'])} | {number(after['average_r'])} | "
                    f"{number(after['profit_factor'])} |"
                )
        lines.extend(["", f"Funding 事件数（落入该周期）：{result['funding_events']:,}。", ""])
    return "\n".join(lines)


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    main()
