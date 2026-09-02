#!/usr/bin/env python3
"""Audit MACD/ATR/exit parameter neighborhoods around frozen candidates."""

from __future__ import annotations

import argparse
import bisect
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_macd_divergence import load_market

from mastermind_tick.macd_divergence import (
    DivergenceConfig,
    ExecutionConfig,
    IndicatorConfig,
    divergence_structures,
    entry_signals,
    indicator_series,
    replay_signals,
    swing_points,
)

MACD_NEIGHBORS = (
    (12, 34, 9),
    (13, 34, 9),
    (14, 34, 9),
    (13, 32, 9),
    (13, 36, 9),
    (13, 34, 7),
    (13, 34, 12),
    (10, 30, 9),
    (15, 40, 9),
)
MACD_FAST_VALUES = (8, 10, 12, 13, 15, 18)
MACD_SLOW_VALUES = (21, 26, 30, 34, 40, 50)
MACD_SIGNAL_VALUES = (5, 7, 9, 12)
ATR_PERIODS = (7, 10, 13, 14, 20)
EXIT_NEIGHBORS = (
    (1.0, 2.0),
    (1.0, 2.5),
    (1.0, 3.0),
    (1.25, 2.0),
    (1.25, 2.5),
    (1.25, 3.0),
    (1.5, 2.0),
    (1.5, 2.5),
    (1.5, 3.0),
)
HISTOGRAM_MATCHES = ("at_swing", "confirmed_window")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("reports/experiments/macd_divergence/2026-08-28/results.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/macd_divergence/2026-08-28/robustness"),
    )
    parser.add_argument(
        "--full-macd-grid",
        action="store_true",
        help="also evaluate all requested fast/slow/signal MACD combinations",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source = json.loads(args.input.read_text(encoding="utf-8"))
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "candidate_source": str(args.input),
            "selection": "frozen candidate from cost-inclusive 15m report; OOS not used",
            "macd_neighbors": MACD_NEIGHBORS,
            "atr_periods": ATR_PERIODS,
            "exit_neighbors": EXIT_NEIGHBORS,
        },
        "symbols": {},
    }
    for symbol, item in source["symbols"].items():
        print(f"loading {symbol}", flush=True)
        bars = load_market(symbol)
        payload["symbols"][symbol] = evaluate(
            symbol, bars, item["selected"], full_macd_grid=args.full_macd_grid
        )
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(args.output_dir / "README.md")


def evaluate(
    symbol: str,
    bars,
    frozen: dict[str, Any],
    *,
    full_macd_grid: bool = False,
) -> dict[str, Any]:
    divergence = DivergenceConfig(**frozen["structure"])
    base_execution = ExecutionConfig(**frozen["execution"])
    starts = [bar.start_ms for bar in bars]
    split_2023 = bisect.bisect_left(starts, utc_ms(2023, 1, 1))
    split_2025 = bisect.bisect_left(starts, utc_ms(2025, 1, 1))
    periods = {
        "research": (0, split_2023),
        "validation": (split_2023, split_2025),
        "oos": (split_2025, len(bars)),
    }
    rows: list[dict[str, Any]] = []
    for fast, slow, signal in MACD_NEIGHBORS:
        config = IndicatorConfig(fast, slow, signal, 13)
        rows.append(
            evaluate_one(
                symbol,
                bars,
                divergence,
                base_execution,
                config,
                periods,
                family="macd",
                identifier=f"macd-{fast}-{slow}-{signal}",
            )
        )
    for histogram_match in HISTOGRAM_MATCHES:
        config = DivergenceConfig(**{**asdict(divergence), "histogram_match": histogram_match})
        rows.append(
            evaluate_one(
                symbol,
                bars,
                config,
                base_execution,
                IndicatorConfig(),
                periods,
                family="histogram_match",
                identifier=f"histogram-{histogram_match}",
            )
        )
    full_rows: list[dict[str, Any]] = []
    if full_macd_grid:
        for fast in MACD_FAST_VALUES:
            for slow in MACD_SLOW_VALUES:
                for signal in MACD_SIGNAL_VALUES:
                    config = IndicatorConfig(fast, slow, signal, 13)
                    full_rows.append(
                        evaluate_one(
                            symbol,
                            bars,
                            divergence,
                            base_execution,
                            config,
                            periods,
                            family="macd_full",
                            identifier=f"macd-{fast}-{slow}-{signal}",
                        )
                    )
    for atr_period in ATR_PERIODS:
        config = IndicatorConfig(
            macd_fast=13,
            macd_slow=34,
            macd_signal=9,
            atr_period=atr_period,
        )
        rows.append(
            evaluate_one(
                symbol,
                bars,
                divergence,
                base_execution,
                config,
                periods,
                family="atr_period",
                identifier=f"atr-period-{atr_period}",
            )
        )
    for stop_atr, reward_risk in EXIT_NEIGHBORS:
        execution = ExecutionConfig(**asdict(base_execution))
        execution = ExecutionConfig(
            stop_atr=stop_atr,
            reward_risk=reward_risk,
            risk_fraction=execution.risk_fraction,
            fee_bps=execution.fee_bps,
            slippage_bps=execution.slippage_bps,
            max_leverage=execution.max_leverage,
            initial_equity=execution.initial_equity,
        )
        rows.append(
            evaluate_one(
                symbol,
                bars,
                divergence,
                execution,
                IndicatorConfig(),
                periods,
                family="exit",
                identifier=f"exit-atr{stop_atr:g}-rr{reward_risk:g}",
            )
        )
    result = {
        "frozen_candidate": frozen["id"],
        "candidate_count": len(rows),
        "positive_research_validation": sum(
            row["research"]["average_r"] is not None
            and row["research"]["average_r"] > 0
            and row["validation"]["average_r"] is not None
            and row["validation"]["average_r"] > 0
            for row in rows
        ),
        "positive_oos": sum(
            row["oos"]["average_r"] is not None and row["oos"]["average_r"] > 0 for row in rows
        ),
        "rows": rows,
    }
    if full_macd_grid:
        oos_values = [row["oos"]["average_r"] for row in full_rows]
        development_sorted = sorted(
            full_rows,
            key=lambda row: (
                row["research"]["average_r"] or -float("inf"),
                row["validation"]["average_r"] or -float("inf"),
            ),
            reverse=True,
        )
        result["full_macd_grid"] = {
            "count": len(full_rows),
            "parameter_values": {
                "fast": MACD_FAST_VALUES,
                "slow": MACD_SLOW_VALUES,
                "signal": MACD_SIGNAL_VALUES,
            },
            "positive_research": sum(
                row["research"]["average_r"] is not None and row["research"]["average_r"] > 0
                for row in full_rows
            ),
            "positive_validation": sum(
                row["validation"]["average_r"] is not None and row["validation"]["average_r"] > 0
                for row in full_rows
            ),
            "positive_research_validation": sum(
                row["research"]["average_r"] is not None
                and row["research"]["average_r"] > 0
                and row["validation"]["average_r"] is not None
                and row["validation"]["average_r"] > 0
                for row in full_rows
            ),
            "positive_oos": sum(value is not None and value > 0 for value in oos_values),
            "oos_range": [min(oos_values), max(oos_values)],
            "top_development": [compact_candidate(row) for row in development_sorted[:10]],
            "rows": full_rows,
        }
    return result


def evaluate_one(
    symbol,
    bars,
    divergence,
    execution,
    indicator_config,
    periods,
    *,
    family,
    identifier,
) -> dict[str, Any]:
    indicators = indicator_series(bars, indicator_config)
    lows = swing_points(bars, indicators.histogram, divergence, "low")
    highs = swing_points(bars, indicators.histogram, divergence, "high")
    structures = tuple(
        sorted(
            divergence_structures(lows, indicators.atr, divergence.points, "LONG")
            + divergence_structures(highs, indicators.atr, divergence.points, "SHORT"),
            key=lambda item: (item.known_at, item.id),
        )
    )
    signals = entry_signals(structures, indicators.histogram)
    result = {
        "id": identifier,
        "family": family,
        "indicators": asdict(indicator_config),
        "execution": asdict(execution),
        "signal_count": len(signals),
    }
    for name, period in periods.items():
        replay = replay_signals(
            bars,
            indicators,
            signals,
            execution,
            symbol=symbol,
            timeframe_minutes=15,
            start_index=period[0],
            end_index=period[1],
        )
        result[name] = {
            "total_trades": replay.total_trades,
            "average_r": replay.average_r,
            "profit_factor": replay.profit_factor,
            "net_return": replay.net_return,
            "max_drawdown": replay.max_drawdown,
        }
    return result


def utc_ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def compact_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "id",
            "indicators",
            "execution",
            "signal_count",
            "research",
            "validation",
            "oos",
        )
        if key in row
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# MACD/ATR 参数邻域稳健性",
        "",
        "候选先从无 Funding 主报告冻结；本报告只做邻域审计，OOS 不参与选择。",
        "",
    ]
    for symbol, item in payload["symbols"].items():
        lines.extend(
            [
                f"## {symbol}",
                "",
                f"冻结候选：`{item['frozen_candidate']}`；"
                f"Research+Validation 同时正值：{item['positive_research_validation']}/"
                f"{item['candidate_count']}；OOS 正值：{item['positive_oos']}/"
                f"{item['candidate_count']}。",
                "",
                "| Family | Candidate | Research R | Validation R | OOS R | OOS PF |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for row in item["rows"]:
            lines.append(
                f"| {row['family']} | `{row['id']}` | {number(row['research']['average_r'])} | "
                f"{number(row['validation']['average_r'])} | {number(row['oos']['average_r'])} | "
                f"{number(row['oos']['profit_factor'])} |"
            )
        lines.append("")
        full = item.get("full_macd_grid")
        if full is not None:
            lines.extend(
                [
                    "### 完整 MACD 参数网格",
                    "",
                    f"共 `{full['count']}` 个组合；Research/Validation 同时为正："
                    f"`{full['positive_research_validation']}`；OOS 为正："
                    f"`{full['positive_oos']}`。OOS 平均 R 范围："
                    f"`{number(full['oos_range'][0])}` 至 `{number(full['oos_range'][1])}`。"
                    "该网格只用于稳健性审计，不按 OOS 选参。",
                    "",
                ]
            )
    return "\n".join(lines)


def number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    main()
