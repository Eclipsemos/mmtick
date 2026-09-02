#!/usr/bin/env python3
"""Evaluate the base MACD divergence grid across requested crypto timeframes."""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import itertools
import json
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.macd_divergence import (
    DivergenceConfig,
    ExecutionConfig,
    divergence_structures,
    entry_signals,
    indicator_series,
    replay_signals,
    swing_points,
)

KLINE_FIELDS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
STOP_ATR_VALUES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
REWARD_RISK_VALUES = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframes", nargs="+", type=int, default=[5, 30, 60, 240])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/macd_divergence/2026-08-28/timeframes"),
    )
    args = parser.parse_args()

    symbol = args.symbol.upper()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source = load_5m_market(symbol)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "source": data_summary(source, 5),
        "timeframes": {},
    }
    for timeframe in args.timeframes:
        bars = source if timeframe == 5 else aggregate_bars(source, timeframe)
        print(f"{symbol} {timeframe}m bars={len(bars)}", flush=True)
        payload["timeframes"][str(timeframe)] = evaluate_timeframe(symbol, timeframe, bars)
        write_payload(args.output_dir, symbol, payload)
    print(args.output_dir / f"{symbol.lower()}-timeframes.md")


def evaluate_timeframe(
    symbol: str, timeframe: int, bars: list[ResearchBar]
) -> dict[str, Any]:
    indicators = indicator_series(bars)
    boundaries = split_boundaries(bars)
    configs = (
        DivergenceConfig(points=2, swing_method="pivot", pivot_left=3, pivot_right=3),
        DivergenceConfig(points=3, swing_method="pivot", pivot_left=3, pivot_right=3),
        DivergenceConfig(points=2, swing_method="rolling", rolling_window=5),
        DivergenceConfig(points=3, swing_method="rolling", rolling_window=5),
        DivergenceConfig(points=2, swing_method="rolling", rolling_window=10),
        DivergenceConfig(points=3, swing_method="rolling", rolling_window=10),
        DivergenceConfig(points=2, swing_method="rolling", rolling_window=20),
        DivergenceConfig(points=3, swing_method="rolling", rolling_window=20),
    )
    rows = []
    signals_by_key = {}
    total = len(configs) * len(STOP_ATR_VALUES) * len(REWARD_RISK_VALUES)
    completed = 0
    for config in configs:
        key = structure_key(config)
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
        signals_by_key[key] = signals
        for stop_atr, reward_risk in itertools.product(STOP_ATR_VALUES, REWARD_RISK_VALUES):
            execution = ExecutionConfig(stop_atr=stop_atr, reward_risk=reward_risk)
            row = {
                "id": f"{key}-atr{stop_atr:g}-rr{reward_risk:g}",
                "structure": asdict(config),
                "execution": asdict(execution),
                "signal_count": len(signals),
                "research": replay_summary(
                    bars,
                    indicators,
                    signals,
                    execution,
                    symbol,
                    timeframe,
                    boundaries["research"],
                ),
                "validation": replay_summary(
                    bars,
                    indicators,
                    signals,
                    execution,
                    symbol,
                    timeframe,
                    boundaries["validation"],
                ),
            }
            row["development_score"] = development_score(row)
            rows.append(row)
            completed += 1
            if completed % 24 == 0:
                print(f"{symbol} {timeframe}m development {completed}/{total}", flush=True)

    ranked = sorted(rows, key=lambda item: item["development_score"], reverse=True)
    selected_id = ranked[0]["id"]
    for rank, row in enumerate(ranked, start=1):
        row["development_rank"] = rank
    for completed, row in enumerate(rows, start=1):
        config = DivergenceConfig(**row["structure"])
        execution = ExecutionConfig(**row["execution"])
        row["oos"] = replay_summary(
            bars,
            indicators,
            signals_by_key[structure_key(config)],
            execution,
            symbol,
            timeframe,
            boundaries["oos"],
        )
        if completed % 48 == 0:
            print(f"{symbol} {timeframe}m OOS {completed}/{len(rows)}", flush=True)
    selected = next(row for row in rows if row["id"] == selected_id)
    positive_development = sum(row["development_score"] > 0 for row in rows)
    positive_oos = sum((row["oos"]["average_r"] or -999) > 0 for row in rows)
    return {
        "data": data_summary(bars, timeframe),
        "candidate_count": len(rows),
        "selected_before_oos": selected_id,
        "selected": selected,
        "positive_development_candidates": positive_development,
        "positive_oos_candidates": positive_oos,
        "top_development": ranked[:10],
        "all_candidates": rows,
    }


def replay_summary(
    bars,
    indicators,
    signals,
    execution,
    symbol: str,
    timeframe: int,
    period: tuple[int, int],
) -> dict[str, Any]:
    result = replay_signals(
        bars,
        indicators,
        signals,
        execution,
        symbol=symbol,
        timeframe_minutes=timeframe,
        start_index=period[0],
        end_index=period[1],
    )
    names = (
        "final_equity",
        "net_return",
        "total_trades",
        "win_rate",
        "average_r",
        "expectancy_r",
        "profit_factor",
        "sharpe",
        "sortino",
        "cagr",
        "max_drawdown",
        "longest_losing_streak",
        "average_holding_bars",
        "exposure",
        "fees_paid",
        "slippage_cost",
        "ambiguous_bars",
    )
    return {name: getattr(result, name) for name in names}


def development_score(row: dict[str, Any]) -> float:
    research = row["research"]
    validation = row["validation"]
    if (
        research["total_trades"] < 30
        or validation["total_trades"] < 20
        or research["average_r"] is None
        or validation["average_r"] is None
    ):
        return -1e9
    weaker = min(research["average_r"], validation["average_r"])
    weaker_pf = min(research["profit_factor"] or 0, validation["profit_factor"] or 0)
    drawdown = max(abs(research["max_drawdown"]), abs(validation["max_drawdown"]))
    return weaker + 0.02 * (weaker_pf - 1) - 0.05 * drawdown


def load_5m_market(symbol: str) -> list[ResearchBar]:
    suffix = "btc" if symbol == "BTCUSDT" else "eth"
    directory = Path(f"data/history_{suffix}_5m")
    bars: dict[int, ResearchBar] = {}
    for path in sorted(directory.glob(f"{symbol}-5m-*.zip")):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(names) != 1:
                raise RuntimeError(f"expected one CSV in {path}, found {names}")
            with archive.open(names[0]) as handle:
                read_rows(handle, bars)
    current = directory / f"{symbol}-5m-current.csv"
    if current.exists():
        with current.open("rb") as handle:
            read_rows(handle, bars)
    return [bars[key] for key in sorted(bars)]


def read_rows(handle, bars: dict[int, ResearchBar]) -> None:
    reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8"))
    first = next(reader, None)
    if first is None:
        return
    rows = reader if first[0] == "open_time" else itertools.chain((first,), reader)
    for values in rows:
        row = dict(zip(KLINE_FIELDS, values, strict=True))
        bar = ResearchBar(
            start_ms=int(row["open_time"]),
            end_ms=int(row["close_time"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
        )
        bars[bar.start_ms] = bar


def aggregate_bars(source: list[ResearchBar], interval_minutes: int) -> list[ResearchBar]:
    if interval_minutes < 5 or interval_minutes % 5:
        raise ValueError("interval must be a positive multiple of five minutes")
    interval_ms = interval_minutes * 60_000
    expected = interval_minutes // 5
    result = []
    group: list[ResearchBar] = []
    bucket: int | None = None

    def finish() -> None:
        if (
            len(group) == expected
            and group[0].start_ms == bucket
            and all(
                right.start_ms - left.start_ms == 5 * 60_000
                for left, right in zip(group, group[1:], strict=False)
            )
        ):
            result.append(
                ResearchBar(
                    start_ms=group[0].start_ms,
                    end_ms=group[-1].end_ms,
                    open=group[0].open,
                    high=max(bar.high for bar in group),
                    low=min(bar.low for bar in group),
                    close=group[-1].close,
                    volume=sum((bar.volume for bar in group), Decimal("0")),
                )
            )

    for bar in source:
        current = bar.start_ms // interval_ms * interval_ms
        if bucket is None or current != bucket:
            if group:
                finish()
            group = [bar]
            bucket = current
        else:
            group.append(bar)
    if group:
        finish()
    return result


def split_boundaries(bars: list[ResearchBar]) -> dict[str, tuple[int, int]]:
    starts = [bar.start_ms for bar in bars]
    split_2023 = bisect.bisect_left(
        starts, int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000)
    )
    split_2025 = bisect.bisect_left(
        starts, int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000)
    )
    return {
        "research": (0, split_2023),
        "validation": (split_2023, split_2025),
        "oos": (split_2025, len(bars)),
    }


def structure_key(config: DivergenceConfig) -> str:
    swing = (
        f"pivot-{config.pivot_left}-{config.pivot_right}"
        if config.swing_method == "pivot"
        else f"rolling-{config.rolling_window}"
    )
    return f"{swing}-{config.points}point-{config.histogram_match}"


def data_summary(bars: list[ResearchBar], timeframe: int) -> dict[str, Any]:
    step = timeframe * 60_000
    gaps = sum(
        right.start_ms - left.start_ms != step
        for left, right in zip(bars, bars[1:], strict=False)
    )
    return {
        "bars": len(bars),
        "first_bar": timestamp(bars[0].start_ms),
        "last_bar": timestamp(bars[-1].end_ms),
        "unexpected_gaps": gaps,
    }


def write_payload(directory: Path, symbol: str, payload: dict[str, Any]) -> None:
    (directory / f"{symbol.lower()}-timeframes.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        f"# {symbol} MACD 背离多周期验证",
        "",
        f"5m 源数据：{payload['source']['bars']:,} 根，{payload['source']['first_bar']} 至 "
        f"{payload['source']['last_bar']}。",
        "",
        "| 周期 | 冻结候选 | Research R | Validation R | OOS R | OOS PF | OOS 交易 |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for timeframe, item in payload["timeframes"].items():
        selected = item["selected"]
        lines.append(
            f"| {timeframe}m | `{item['selected_before_oos']}` | "
            f"{number(selected['research']['average_r'])} | "
            f"{number(selected['validation']['average_r'])} | "
            f"{number(selected['oos']['average_r'])} | "
            f"{number(selected['oos']['profit_factor'])} | "
            f"{selected['oos']['total_trades']} |"
        )
    lines.extend(
        [
            "",
            "候选按 Research 与 Validation 排序，OOS 不参与选择。所有周期使用同一 5m "
            "底层档案聚合；同柱冲突按 Stop 优先，费用与滑点沿用主报告默认值。",
            "",
        ]
    )
    (directory / f"{symbol.lower()}-timeframes.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    main()
