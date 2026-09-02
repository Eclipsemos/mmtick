#!/usr/bin/env python3
"""Run reproducible, causal MACD divergence research on Binance kline archives."""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import itertools
import json
import sqlite3
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
    ReplaySummary,
    SignalFilterConfig,
    bootstrap_expectancy,
    divergence_structures,
    entry_signals,
    filter_entry_signals,
    indicator_series,
    monte_carlo,
    r_distribution,
    replay_signals,
    rolling_period_metrics,
    score_quintiles,
    swing_points,
    trade_dict,
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
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/macd_divergence/2026-08-28"),
    )
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--quick", action="store_true", help="run a reduced execution grid")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": protocol(),
        "symbols": {},
    }
    for symbol in args.symbols:
        symbol = symbol.upper()
        print(f"loading {symbol}", flush=True)
        bars = load_market(symbol)
        print(
            f"{symbol}: bars={len(bars)} {timestamp(bars[0].start_ms)} "
            f"to {timestamp(bars[-1].end_ms)}",
            flush=True,
        )
        payload["symbols"][symbol] = research_symbol(symbol, bars, quick=args.quick)
        trade_path = args.output_dir / f"{symbol.lower()}-selected-oos-trades.csv"
        write_trade_csv(trade_path, payload["symbols"][symbol])
        (args.output_dir / "results.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    (args.output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(args.output_dir / "README.md")


def protocol() -> dict[str, Any]:
    return {
        "market": "Binance USD-M perpetual futures",
        "timeframe_minutes": 15,
        "available_start": "2020-01-01",
        "requested_2017_note": (
            "Binance USD-M BTCUSDT/ETHUSDT archives do not provide 2017 history; "
            "this stage starts at the first common 2020 archive instead of fabricating coverage."
        ),
        "splits": {
            "research": ["2020-01-01", "2022-12-31"],
            "validation": ["2023-01-01", "2024-12-31"],
            "oos": ["2025-01-01", "latest completed bar"],
        },
        "indicators": {"macd": [13, 34, 9], "atr": 13},
        "swings": [
            "confirmed pivot left=3 right=3",
            "rolling extremum N=5",
            "rolling extremum N=10",
            "rolling extremum N=20",
        ],
        "divergence_points": [2, 3],
        "histogram_match": "histogram at price swing bar",
        "entry": "first later histogram contraction; fill next completed bar open",
        "same_bar_rule": "stop before take profit and count ambiguous bar",
        "risk_fraction": 0.01,
        "max_notional_leverage": 5.0,
        "fee_bps_per_side": 4.0,
        "slippage_bps_per_side": 2.0,
        "funding": (
            "main stage ranks candidates without funding; frozen candidates are separately "
            "replayed with historical funding in research_macd_divergence_funding.py"
        ),
        "selection": (
            "rank only on research and validation; reveal OOS after development choice is frozen"
        ),
        "random_seed": 20260828,
    }


def research_symbol(symbol: str, bars: list[ResearchBar], *, quick: bool) -> dict[str, Any]:
    indicators = indicator_series(bars)
    boundaries = split_boundaries(bars)
    structural_configs = (
        DivergenceConfig(points=2, swing_method="pivot", pivot_left=3, pivot_right=3),
        DivergenceConfig(points=3, swing_method="pivot", pivot_left=3, pivot_right=3),
        DivergenceConfig(points=2, swing_method="rolling", rolling_window=5),
        DivergenceConfig(points=3, swing_method="rolling", rolling_window=5),
        DivergenceConfig(points=2, swing_method="rolling", rolling_window=10),
        DivergenceConfig(points=3, swing_method="rolling", rolling_window=10),
        DivergenceConfig(points=2, swing_method="rolling", rolling_window=20),
        DivergenceConfig(points=3, swing_method="rolling", rolling_window=20),
    )
    stop_values = (1.0, 1.5) if quick else STOP_ATR_VALUES
    rr_values = (1.0, 2.0, 3.0) if quick else REWARD_RISK_VALUES
    development_rows = []
    signal_cache = {}
    candidate_number = 0
    total_candidates = len(structural_configs) * len(stop_values) * len(rr_values)
    for config in structural_configs:
        key = structure_key(config)
        low_points = swing_points(bars, indicators.histogram, config, "low")
        high_points = swing_points(bars, indicators.histogram, config, "high")
        structures = tuple(
            sorted(
                divergence_structures(low_points, indicators.atr, config.points, "LONG")
                + divergence_structures(high_points, indicators.atr, config.points, "SHORT"),
                key=lambda item: (item.known_at, item.id),
            )
        )
        signals = entry_signals(structures, indicators.histogram)
        signal_cache[key] = signals
        for stop_atr, reward_risk in itertools.product(stop_values, rr_values):
            candidate_number += 1
            execution = ExecutionConfig(stop_atr=stop_atr, reward_risk=reward_risk)
            research = replay_period(
                bars, indicators, signals, execution, symbol, boundaries["research"]
            )
            validation = replay_period(
                bars, indicators, signals, execution, symbol, boundaries["validation"]
            )
            row = {
                "id": f"{key}-atr{stop_atr:g}-rr{reward_risk:g}",
                "structure": asdict(config),
                "execution": asdict(execution),
                "signal_count": len(signals),
                "research": summary_dict(research),
                "validation": summary_dict(validation),
            }
            row["development_score"] = development_score(row)
            development_rows.append(row)
            if candidate_number % 24 == 0 or candidate_number == total_candidates:
                print(f"{symbol}: development {candidate_number}/{total_candidates}", flush=True)

    ranked = sorted(development_rows, key=lambda item: item["development_score"], reverse=True)
    selected = next((row for row in ranked if row["development_score"] > -1e8), ranked[0])
    for number, row in enumerate(ranked, start=1):
        row["development_rank"] = number

    for number, row in enumerate(development_rows, start=1):
        config = DivergenceConfig(**row["structure"])
        execution = ExecutionConfig(**row["execution"])
        row["oos"] = summary_dict(
            replay_period(
                bars,
                indicators,
                signal_cache[structure_key(config)],
                execution,
                symbol,
                boundaries["oos"],
            )
        )
        if number % 48 == 0 or number == len(development_rows):
            print(f"{symbol}: OOS {number}/{len(development_rows)}", flush=True)

    selected = next(row for row in development_rows if row["id"] == selected["id"])
    selected_config = DivergenceConfig(**selected["structure"])
    selected_execution = ExecutionConfig(**selected["execution"])
    selected_signals = signal_cache[structure_key(selected_config)]
    full = replay_signals(
        bars,
        indicators,
        selected_signals,
        selected_execution,
        symbol=symbol,
        timeframe_minutes=15,
    )
    oos = replay_period(
        bars,
        indicators,
        selected_signals,
        selected_execution,
        symbol,
        boundaries["oos"],
    )
    selected["full"] = summary_dict(full)
    selected["yearly"] = yearly_replays(
        bars, indicators, selected_signals, selected_execution, symbol
    )
    selected["risk_sensitivity_oos"] = risk_sensitivity(
        bars, indicators, selected_signals, selected_execution, symbol, boundaries["oos"]
    )
    selected["monte_carlo_oos"] = monte_carlo(oos.trades)
    selected["bootstrap_expectancy_oos"] = bootstrap_expectancy(oos.trades)
    selected["score_quintiles_oos"] = score_quintiles(oos.trades)
    selected["rolling_metrics_oos"] = rolling_period_metrics(
        oos.equity_curve, oos.initial_equity, window=30
    )
    selected["r_distribution_oos"] = r_distribution(oos.trades)
    selected["oos_trades"] = [trade_dict(trade) for trade in oos.trades]
    selected["full_monthly_returns"] = list(full.monthly_returns)
    selected["full_yearly_returns"] = list(full.yearly_returns)
    selected["cost_sensitivity_oos"] = cost_sensitivity(
        bars, indicators, selected_signals, selected_execution, symbol, boundaries["oos"]
    )
    selected["conditional_filter_study"] = conditional_filter_study(
        bars,
        indicators,
        selected_signals,
        selected_execution,
        symbol,
        boundaries,
    )

    return {
        "data": data_summary(bars),
        "candidate_count": len(development_rows),
        "selected_before_oos": selected["id"],
        "selected": selected,
        "top_development": [compact_candidate(row) for row in ranked[:20]],
        "all_candidates": [compact_candidate(row) for row in development_rows],
        "rr_comparison": rr_comparison(development_rows, selected),
        "point_comparison": grouped_best(development_rows, "points"),
        "swing_comparison": grouped_best(development_rows, "swing_method"),
        "stage_status": stage_status(selected),
    }


def replay_period(
    bars: list[ResearchBar],
    indicators,
    signals,
    execution: ExecutionConfig,
    symbol: str,
    period: tuple[int, int],
) -> ReplaySummary:
    return replay_signals(
        bars,
        indicators,
        signals,
        execution,
        symbol=symbol,
        timeframe_minutes=15,
        start_index=period[0],
        end_index=period[1],
    )


def development_score(row: dict[str, Any]) -> float:
    research = row["research"]
    validation = row["validation"]
    if (
        research["total_trades"] < 30
        or validation["total_trades"] < 20
        or research["expectancy_r"] is None
        or validation["expectancy_r"] is None
    ):
        return -1e9
    weaker_expectancy = min(research["expectancy_r"], validation["expectancy_r"])
    weaker_profit_factor = min(research["profit_factor"] or 0.0, validation["profit_factor"] or 0.0)
    drawdown_penalty = max(abs(research["max_drawdown"]), abs(validation["max_drawdown"]))
    return weaker_expectancy + 0.02 * (weaker_profit_factor - 1) - 0.05 * drawdown_penalty


def risk_sensitivity(
    bars: list[ResearchBar], indicators, signals, execution, symbol: str, period: tuple[int, int]
) -> list[dict[str, Any]]:
    rows = []
    for risk_fraction in (0.005, 0.01, 0.02):
        config = ExecutionConfig(**{**asdict(execution), "risk_fraction": risk_fraction})
        rows.append(
            {
                "risk_fraction": risk_fraction,
                **summary_dict(replay_period(bars, indicators, signals, config, symbol, period)),
            }
        )
    return rows


def cost_sensitivity(
    bars: list[ResearchBar], indicators, signals, execution, symbol: str, period: tuple[int, int]
) -> list[dict[str, Any]]:
    rows = []
    for label, fee_bps, slippage_bps in (
        ("frictionless", 0.0, 0.0),
        ("low_slippage", 4.0, 1.0),
        ("default", 4.0, 2.0),
        ("high_slippage", 4.0, 5.0),
        ("high_cost", 5.0, 5.0),
    ):
        config = ExecutionConfig(
            **{
                **asdict(execution),
                "fee_bps": fee_bps,
                "slippage_bps": slippage_bps,
            }
        )
        rows.append(
            {
                "label": label,
                "fee_bps": fee_bps,
                "slippage_bps": slippage_bps,
                **summary_dict(replay_period(bars, indicators, signals, config, symbol, period)),
            }
        )
    return rows


def conditional_filter_study(
    bars: list[ResearchBar],
    indicators,
    signals,
    execution,
    symbol: str,
    boundaries: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    candidates = [("none", SignalFilterConfig())]
    candidates.extend(
        [
            ("ema200_with", SignalFilterConfig(trend="with_ema")),
            ("ema200_against", SignalFilterConfig(trend="against_ema")),
            (
                "rsi_30_70",
                SignalFilterConfig(rsi_long_max=30, rsi_short_min=70),
            ),
            (
                "rsi_35_65",
                SignalFilterConfig(rsi_long_max=35, rsi_short_min=65),
            ),
            (
                "rsi_40_60",
                SignalFilterConfig(rsi_long_max=40, rsi_short_min=60),
            ),
            ("atr_percentile_25", SignalFilterConfig(atr_percentile=0.25)),
            ("atr_percentile_50", SignalFilterConfig(atr_percentile=0.50)),
            ("atr_percentile_75", SignalFilterConfig(atr_percentile=0.75)),
            ("volume_mean_20", SignalFilterConfig(volume_mean_window=20)),
            ("volume_mean_50", SignalFilterConfig(volume_mean_window=50)),
            ("histogram_atr_002", SignalFilterConfig(minimum_histogram_atr=0.02)),
            ("histogram_atr_005", SignalFilterConfig(minimum_histogram_atr=0.05)),
            ("histogram_atr_010", SignalFilterConfig(minimum_histogram_atr=0.10)),
        ]
    )
    rows = []
    filtered_signals = {}
    for identifier, config in candidates:
        selected_signals = filter_entry_signals(bars, indicators, signals, config)
        filtered_signals[identifier] = selected_signals
        row = {
            "id": identifier,
            "config": asdict(config),
            "signal_count": len(selected_signals),
            "research": summary_dict(
                replay_period(
                    bars,
                    indicators,
                    selected_signals,
                    execution,
                    symbol,
                    boundaries["research"],
                )
            ),
            "validation": summary_dict(
                replay_period(
                    bars,
                    indicators,
                    selected_signals,
                    execution,
                    symbol,
                    boundaries["validation"],
                )
            ),
        }
        row["development_score"] = development_score(row)
        rows.append(row)
    ranked = sorted(rows, key=lambda item: item["development_score"], reverse=True)
    selected_id = ranked[0]["id"]
    for rank, row in enumerate(ranked, start=1):
        row["development_rank"] = rank
        row["oos"] = summary_dict(
            replay_period(
                bars,
                indicators,
                filtered_signals[row["id"]],
                execution,
                symbol,
                boundaries["oos"],
            )
        )
    return {"selected_before_oos": selected_id, "candidates": ranked}


def rr_comparison(rows: list[dict[str, Any]], selected: dict[str, Any]) -> list[dict[str, Any]]:
    selected_stop = selected["execution"]["stop_atr"]
    selected_structure = selected["structure"]
    return [
        compact_candidate(row)
        for row in rows
        if row["structure"] == selected_structure
        and row["execution"]["stop_atr"] == selected_stop
        and row["execution"]["reward_risk"] in {1.0, 2.0, 3.0}
    ]


def grouped_best(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    values = sorted({row["structure"][field] for row in rows}, key=str)
    result = []
    for value in values:
        group = [row for row in rows if row["structure"][field] == value]
        selected = max(group, key=lambda item: item["development_score"])
        result.append({"group": value, "selected": compact_candidate(selected)})
    return result


def yearly_replays(
    bars: list[ResearchBar], indicators, signals, execution, symbol: str
) -> list[dict[str, Any]]:
    starts = [bar.start_ms for bar in bars]
    last_year = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).year
    rows = []
    for year in range(2020, last_year + 1):
        start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000)
        end = int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000)
        left = bisect.bisect_left(starts, start)
        right = bisect.bisect_left(starts, end)
        if left >= right:
            continue
        result = replay_signals(
            bars,
            indicators,
            signals,
            execution,
            symbol=symbol,
            timeframe_minutes=15,
            start_index=left,
            end_index=right,
        )
        market_return = float(bars[right - 1].close / bars[left].open - 1)
        regime = (
            "bull" if market_return >= 0.20 else "bear" if market_return <= -0.20 else "sideways"
        )
        rows.append(
            {
                "year": year,
                "market_return": market_return,
                "regime": regime,
                **summary_dict(result),
            }
        )
    return rows


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


def load_market(symbol: str) -> list[ResearchBar]:
    suffix = "btc" if symbol == "BTCUSDT" else "eth"
    archive_dir = Path(f"data/history_{suffix}")
    database = Path(f"data/macd_{suffix}_market.db")
    bars: dict[int, ResearchBar] = {}
    for path in sorted(archive_dir.glob(f"{symbol}-15m-*.zip")):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(names) != 1:
                raise RuntimeError(f"expected one CSV in {path}, found {names}")
            with archive.open(names[0]) as handle:
                reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8"))
                first = next(reader, None)
                if first is None:
                    continue
                rows = reader if first[0] == "open_time" else itertools.chain((first,), reader)
                for row in rows:
                    item = dict(zip(KLINE_FIELDS, row, strict=True))
                    bar = row_to_bar(item)
                    bars[bar.start_ms] = bar
    if database.exists():
        connection = sqlite3.connect(database)
        try:
            rows = connection.execute(
                """
                SELECT start_ms, end_ms, open, high, low, close, volume
                FROM ohlcv_bars
                WHERE symbol = ? AND interval_minutes = 15 AND is_closed = 1
                ORDER BY start_ms
                """,
                (symbol,),
            )
            for row in rows:
                bar = ResearchBar(
                    start_ms=row[0],
                    end_ms=row[1],
                    open=Decimal(row[2]),
                    high=Decimal(row[3]),
                    low=Decimal(row[4]),
                    close=Decimal(row[5]),
                    volume=Decimal(row[6]),
                )
                bars[bar.start_ms] = bar
        finally:
            connection.close()
    ordered = [bars[key] for key in sorted(bars)]
    if not ordered:
        raise RuntimeError(f"no bars found for {symbol}")
    return ordered


def row_to_bar(row: dict[str, str]) -> ResearchBar:
    return ResearchBar(
        start_ms=int(row["open_time"]),
        end_ms=int(row["close_time"]),
        open=Decimal(row["open"]),
        high=Decimal(row["high"]),
        low=Decimal(row["low"]),
        close=Decimal(row["close"]),
        volume=Decimal(row["volume"]),
    )


def data_summary(bars: list[ResearchBar]) -> dict[str, Any]:
    expected_step = 15 * 60_000
    gaps = [
        right.start_ms - left.start_ms
        for left, right in zip(bars, bars[1:], strict=False)
        if right.start_ms - left.start_ms != expected_step
    ]
    return {
        "bars": len(bars),
        "first_bar": timestamp(bars[0].start_ms),
        "last_bar": timestamp(bars[-1].end_ms),
        "non_15m_gaps": len(gaps),
        "largest_gap_minutes": max(gaps, default=expected_step) / 60_000,
    }


def summary_dict(result: ReplaySummary) -> dict[str, Any]:
    names = (
        "initial_equity",
        "final_equity",
        "net_return",
        "total_trades",
        "win_rate",
        "average_win_r",
        "average_loss_r",
        "average_r",
        "expectancy_r",
        "profit_factor",
        "sharpe",
        "sortino",
        "cagr",
        "max_drawdown",
        "calmar",
        "longest_losing_streak",
        "longest_winning_streak",
        "average_holding_bars",
        "median_holding_bars",
        "exposure",
        "fees_paid",
        "slippage_cost",
        "ambiguous_bars",
        "leverage_capped_trades",
    )
    return {name: getattr(result, name) for name in names}


def compact_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "id",
            "structure",
            "execution",
            "signal_count",
            "development_score",
            "development_rank",
            "research",
            "validation",
            "oos",
        )
        if key in row
    }


def structure_key(config: DivergenceConfig) -> str:
    if config.swing_method == "pivot":
        swing = f"pivot-{config.pivot_left}-{config.pivot_right}"
    else:
        swing = f"rolling-{config.rolling_window}"
    return f"{swing}-{config.points}point-{config.histogram_match}"


def stage_status(selected: dict[str, Any]) -> dict[str, str]:
    oos = selected["oos"]
    if (
        oos["total_trades"] < 30
        or (oos["expectancy_r"] or 0) <= 0
        or (oos["profit_factor"] or 0) <= 1
    ):
        return {
            "status": "REJECTED",
            "reason": (
                "development-selected 15m base candidate failed OOS trade-count/expectancy/PF gates"
            ),
        }
    return {
        "status": "RESEARCH_ONLY",
        "reason": (
            "15m base passed preliminary OOS gates; other timeframes, filters, "
            "and robustness remain"
        ),
    }


def write_trade_csv(path: Path, symbol_payload: dict[str, Any]) -> None:
    trades = symbol_payload["selected"].get("oos_trades", [])
    if not trades:
        path.write_text("", encoding="utf-8")
        return
    rows = []
    for trade in trades:
        row = dict(trade)
        row["entry_at"] = timestamp(row["entry_at_ms"])
        row["exit_at"] = timestamp(row["exit_at_ms"])
        for name in ("point_indices", "prices", "histograms"):
            row[name] = json.dumps(row[name])
        rows.append(row)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 币圈半神 MACD 背离策略：15m Base 验证",
        "",
        f"生成时间：{payload['generated_at']}",
        "",
        "## 研究纪律",
        "",
        "- 数据为 Binance USD-M 已完成 15m K 线；可用共同历史从 2020 年开始。",
        "- Confirmed Pivot 只在右侧 3 根 K 线结束后确认，信号不回填到 Pivot 时刻。",
        "- 背离确认后等待后续 Histogram 首次收缩，下一根 K 线 Open 成交。",
        "- 同柱同时触及 SL/TP 时按 Stop 优先；费用 4 bps/边，滑点 2 bps/边。",
        "- 参数只按 2020-2022 Research 与 2023-2024 Validation 排序，之后揭示 2025+ OOS。",
        "- Funding 使用独立复核报告加入；本报告仍未完成 MACD/ATR 参数邻域稳健性与案例图。",
        "",
    ]
    for symbol, item in payload["symbols"].items():
        selected = item["selected"]
        lines.extend(
            [
                f"## {symbol}",
                "",
                f"数据：{item['data']['bars']:,} 根，{item['data']['first_bar']} 至 "
                f"{item['data']['last_bar']}；非 15m 间隔：{item['data']['non_15m_gaps']}。",
                "",
                f"开发期冻结候选：`{item['selected_before_oos']}`",
                "",
                "| 分区 | 交易 | 胜率 | 平均 R | PF | 收益 | 最大回撤 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        periods = (("research", "2020-2022"), ("validation", "2023-2024"), ("oos", "2025+"))
        for key, label in periods:
            value = selected[key]
            lines.append(
                f"| {label} | {value['total_trades']} | {percent(value['win_rate'])} | "
                f"{number(value['average_r'])} | {number(value['profit_factor'])} | "
                f"{percent(value['net_return'])} | {percent(value['max_drawdown'])} |"
            )
        lines.extend(
            [
                "",
                f"阶段状态：**{item['stage_status']['status']}**。"
                f" {item['stage_status']['reason']}",
                "",
                "### 年度独立回放",
                "",
                "| 年份 | 市场环境 | 标的收益 | 交易 | 平均 R | PF | 策略收益 | 最大回撤 |",
                "|---:|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for year in selected["yearly"]:
            lines.append(
                f"| {year['year']} | {year['regime']} | {percent(year['market_return'])} | "
                f"{year['total_trades']} | {number(year['average_r'])} | "
                f"{number(year['profit_factor'])} | {percent(year['net_return'])} | "
                f"{percent(year['max_drawdown'])} |"
            )
        lines.extend(
            [
                "",
                "### 开发期前五名（OOS 不参与排名）",
                "",
                "| Rank | Candidate | Research R | Validation R | OOS R | OOS PF |",
                "|---:|---|---:|---:|---:|---:|",
            ]
        )
        for row in item["top_development"][:5]:
            lines.append(
                f"| {row['development_rank']} | `{row['id']}` | "
                f"{number(row['research']['average_r'])} | "
                f"{number(row['validation']['average_r'])} | "
                f"{number(row['oos']['average_r'])} | {number(row['oos']['profit_factor'])} |"
            )
        mc = selected["monte_carlo_oos"]
        bootstrap = selected["bootstrap_expectancy_oos"]
        lines.extend(
            [
                "",
                "### OOS Monte Carlo",
                "",
                f"10,000 次交易顺序重排：中位最大回撤 {percent(mc.get('median_max_drawdown'))}，"
                f"95% 分位 {percent(mc.get('p95_max_drawdown'))}，"
                f"99% 分位 {percent(mc.get('p99_max_drawdown'))}；"
                f"发生 30% 回撤概率 {percent(mc.get('probability_30pct_drawdown'))}。",
                "",
                f"OOS 平均 R bootstrap 95% 区间："
                f"[{number(bootstrap.get('p025_mean_r'))}, "
                f"{number(bootstrap.get('p975_mean_r'))}]；"
                f"平均 R 为正概率 {percent(bootstrap.get('probability_mean_r_positive'))}。",
                "",
                "### 成本敏感性（冻结候选 OOS）",
                "",
                "| 成本情景 | Fee/边 | Slippage/边 | 平均 R | PF | 收益 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in selected["cost_sensitivity_oos"]:
            lines.append(
                f"| {row['label']} | {row['fee_bps']:.1f} bps | "
                f"{row['slippage_bps']:.1f} bps | {number(row['average_r'])} | "
                f"{number(row['profit_factor'])} | {percent(row['net_return'])} |"
            )
        filter_study = selected["conditional_filter_study"]
        lines.extend(
            [
                "",
                "### 单过滤器研究",
                "",
                f"开发期冻结过滤器：`{filter_study['selected_before_oos']}`。"
                " 此研究固定 Base 的 Swing/ATR/RR，仅逐个改变过滤器。",
                "",
                "| Rank | Filter | Research R | Validation R | OOS R | OOS PF | OOS 交易 |",
                "|---:|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in filter_study["candidates"][:5]:
            lines.append(
                f"| {row['development_rank']} | `{row['id']}` | "
                f"{number(row['research']['average_r'])} | "
                f"{number(row['validation']['average_r'])} | "
                f"{number(row['oos']['average_r'])} | "
                f"{number(row['oos']['profit_factor'])} | {row['oos']['total_trades']} |"
            )
        rolling = selected.get("rolling_metrics_oos", [])
        distribution = selected.get("r_distribution_oos", {})
        lines.extend(["", "### OOS rolling diagnostics", ""])
        if rolling:
            latest = rolling[-1]
            lines.append(
                f"30 日 rolling Sharpe（截至 `{latest['period_end']}`）："
                f"`{number(latest['rolling_sharpe'])}`；rolling Win Rate："
                f"`{percent(latest['rolling_win_rate'])}`。完整序列保存在 `results.json`。"
            )
        else:
            lines.append("OOS 日收益不足 30 天，无法计算 rolling 指标。")
        lines.extend(["", "R 分布（固定区间）：", "", "| 区间 | 交易 | 占比 |", "|---|---:|---:|"])
        for bucket in distribution.get("bins", []):
            lines.append(
                f"| `{bucket['label']}` | {bucket['count']} | {percent(bucket['fraction'])} |"
            )
        lines.extend(
            [
                "",
                "### RR=1/2/3（同一冻结 Swing 与 ATR Stop）",
                "",
                "| RR | Research 胜率 | Research R | Validation R | OOS 胜率 | OOS R |",
                "|---:|---:|---:|---:|---:|---:|",
            ]
        )
        rr_rows = sorted(item["rr_comparison"], key=lambda value: value["execution"]["reward_risk"])
        for row in rr_rows:
            lines.append(
                f"| {row['execution']['reward_risk']:g} | "
                f"{percent(row['research']['win_rate'])} | "
                f"{number(row['research']['average_r'])} | "
                f"{number(row['validation']['average_r'])} | "
                f"{percent(row['oos']['win_rate'])} | {number(row['oos']['average_r'])} |"
            )
        lines.extend(
            [
                "",
                "### Double vs Triple",
                "",
                "| 点数 | 开发期冻结候选 | Research R | Validation R | OOS R | OOS PF |",
                "|---:|---|---:|---:|---:|---:|",
            ]
        )
        for comparison in item["point_comparison"]:
            row = comparison["selected"]
            lines.append(
                f"| {comparison['group']} | `{row['id']}` | "
                f"{number(row['research']['average_r'])} | "
                f"{number(row['validation']['average_r'])} | "
                f"{number(row['oos']['average_r'])} | {number(row['oos']['profit_factor'])} |"
            )
        lines.append("")
    lines.extend(
        [
            "## 当前结论边界",
            "",
            "该阶段只能判断默认 MACD/ATR 下的 15m Base 是否值得继续，不能验证网上宣传的"
            "“95% 胜率”或直接批准实盘。下一阶段必须完成 5m/30m/1h/4h、单过滤器、"
            "参数邻域、成本敏感性、完整 Funding 与随机盈亏案例图。",
            "",
        ]
    )
    return "\n".join(lines)


def timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    main()
