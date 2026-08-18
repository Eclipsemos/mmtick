#!/usr/bin/env python3
"""Explore SOXLUSDT volatility-spread strategies with a frozen August holdout."""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import statistics
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import Any

from mastermind_tick.models import FundingRate
from mastermind_tick.volatility_spread import (
    SpreadBar,
    SpreadExecution,
    SpreadParameters,
    SpreadResult,
    build_spread_features,
    daily_path_metrics,
    evaluate_spread,
    pearson_correlation,
)


def load_market(
    database: Path,
) -> tuple[list[SpreadBar], list[list[FundingRate]], list[SpreadExecution | None]]:
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        bars = [
            SpreadBar(
                start_ms=int(row["start_ms"]),
                end_ms=int(row["end_ms"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
            )
            for row in connection.execute(
                """
                SELECT start_ms, end_ms, open, high, low, close, volume
                FROM ohlcv_bars
                WHERE instrument_id = 'soxl_perp' AND interval_minutes = 15 AND is_closed = 1
                ORDER BY start_ms
                """
            )
        ]
        funding = [
            FundingRate(
                timestamp_ms=int(row["timestamp_ms"]),
                rate=Decimal(row["rate"]),
                mark_price=Decimal(row["mark_price"]),
            )
            for row in connection.execute(
                """
                SELECT timestamp_ms, rate, mark_price FROM funding_rates
                WHERE instrument_id = 'soxl_perp' ORDER BY timestamp_ms
                """
            )
        ]
        execution_rows = connection.execute(
            """
            SELECT bar.start_ms, trade.timestamp_ms, trade.price
            FROM ohlcv_bars AS bar
            LEFT JOIN agg_trades AS trade ON trade.rowid = (
                SELECT candidate.rowid
                FROM agg_trades AS candidate
                WHERE candidate.instrument_id = 'soxl_perp'
                  AND candidate.timestamp_ms >= bar.start_ms
                  AND candidate.timestamp_ms <= bar.end_ms
                ORDER BY candidate.timestamp_ms
                LIMIT 1
            )
            WHERE bar.instrument_id = 'soxl_perp'
              AND bar.interval_minutes = 15
              AND bar.is_closed = 1
            ORDER BY bar.start_ms
            """
        ).fetchall()
    if len(bars) < 500:
        raise ValueError("SOXLUSDT requires at least 500 closed 15m bars")
    bar_ends = [bar.end_ms for bar in bars]
    funding_by_bar: list[list[FundingRate]] = [[] for _ in bars]
    for event in funding:
        index = bisect.bisect_left(bar_ends, event.timestamp_ms)
        if index < len(bars):
            funding_by_bar[index].append(event)
    if len(execution_rows) != len(bars):
        raise ValueError("SOXLUSDT execution Tick rows do not align with closed bars")
    missing_execution_ticks = sum(row["timestamp_ms"] is None for row in execution_rows)
    if missing_execution_ticks:
        raise ValueError(
            f"SOXLUSDT has {missing_execution_ticks} closed bars without an execution Tick; "
            "repair aggTrades before research replay"
        )
    execution_by_bar = [
        (
            SpreadExecution(timestamp_ms=int(row["timestamp_ms"]), price=Decimal(row["price"]))
            if row["timestamp_ms"] is not None
            else None
        )
        for row in execution_rows
    ]
    return bars, funding_by_bar, execution_by_bar


def candidate_grid() -> list[SpreadParameters]:
    broad = [
        SpreadParameters(
            variant=variant,
            direction=direction,
            fast_window=fast,
            slow_window=slow,
            entry_ratio=entry,
            exit_ratio=exit_ratio,
            breakout_window=breakout,
            stop_atr=stop,
            max_hold_bars=max_hold,
        )
        for variant, direction, fast, slow, entry, exit_ratio, breakout, stop, max_hold in product(
            ("expansion_breakout", "compression_release"),
            ("long_only", "long_short"),
            (4, 8, 12),
            (32, 64),
            (1.05, 1.2, 1.4),
            (0.8, 1.0),
            (4, 12, 24),
            (1.5, 2.5),
            (8, 24),
        )
    ]
    long_duration = [
        SpreadParameters(
            variant=variant,
            direction="long_only",
            fast_window=fast,
            slow_window=slow,
            entry_ratio=entry,
            exit_ratio=exit_ratio,
            breakout_window=breakout,
            stop_atr=stop,
            max_hold_bars=max_hold,
        )
        for variant, fast, slow, entry, exit_ratio, breakout, stop, max_hold in product(
            ("expansion_breakout", "compression_release"),
            (8, 12, 24),
            (64, 96),
            (1.0, 1.2, 1.4),
            (0.0, 0.8),
            (12, 24, 48),
            (2.5, 3.5),
            (24, 48, 96),
        )
    ]
    return list(dict.fromkeys([*broad, *long_duration]))


def run() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14"),
    )
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument(
        "--atr-baseline-report",
        type=Path,
        default=Path("reports/backtests/atr-20260811-091257-723465.json"),
    )
    args = parser.parse_args()

    bars, funding_by_bar, execution_by_bar = load_market(args.database)
    train_start = bars[200].start_ms
    splits = {
        "train": (train_start, _day_end(date(2026, 6, 30))),
        "validation": (_day_start(date(2026, 7, 1)), _day_end(date(2026, 7, 31))),
        "holdout": (
            _day_start(date(2026, 8, 1)),
            min(_day_end(date(2026, 8, 10)), bars[-1].end_ms),
        ),
        "full": (train_start, min(_day_end(date(2026, 8, 10)), bars[-1].end_ms)),
    }
    feature_cache = {}
    evaluations = []
    grid = candidate_grid()
    for index, parameters in enumerate(grid, start=1):
        key = (
            parameters.spread_measure,
            parameters.fast_window,
            parameters.slow_window,
            parameters.breakout_window,
            parameters.compression_ratio,
            parameters.compression_lookback,
        )
        features = feature_cache.get(key)
        if features is None:
            features = build_spread_features(
                bars,
                fast_window=parameters.fast_window,
                slow_window=parameters.slow_window,
                breakout_window=parameters.breakout_window,
                compression_ratio=parameters.compression_ratio,
                compression_lookback=parameters.compression_lookback,
                spread_measure=parameters.spread_measure,
            )
            feature_cache[key] = features
        train = _evaluate(bars, funding_by_bar, features, parameters, splits["train"])
        validation = _evaluate(bars, funding_by_bar, features, parameters, splits["validation"])
        score = _selection_score(train, validation)
        evaluations.append(
            {
                "parameters": asdict(parameters),
                "selection_score": score,
                "train": _summary(train),
                "validation": _summary(validation),
            }
        )
        if index % 250 == 0:
            print(f"evaluated {index}/{len(grid)} candidates", flush=True)

    ranked = sorted(evaluations, key=lambda item: item["selection_score"], reverse=True)
    eligible = [
        item
        for item in ranked
        if item["train"]["completed_trades"] >= 3
        and item["validation"]["completed_trades"] >= 3
        and not item["train"]["bankrupt"]
        and not item["validation"]["bankrupt"]
    ]
    if not eligible:
        raise RuntimeError("no volatility-spread candidate passed minimum trade requirements")
    selected_parameters = SpreadParameters(**eligible[0]["parameters"])
    selected_features = feature_cache[
        (
            selected_parameters.spread_measure,
            selected_parameters.fast_window,
            selected_parameters.slow_window,
            selected_parameters.breakout_window,
            selected_parameters.compression_ratio,
            selected_parameters.compression_lookback,
        )
    ]

    finalists = []
    for item in eligible[: args.top]:
        parameters = SpreadParameters(**item["parameters"])
        features = feature_cache[
            (
                parameters.spread_measure,
                parameters.fast_window,
                parameters.slow_window,
                parameters.breakout_window,
                parameters.compression_ratio,
                parameters.compression_lookback,
            )
        ]
        finalists.append(
            {
                **item,
                "holdout": _summary(
                    _evaluate(bars, funding_by_bar, features, parameters, splits["holdout"])
                ),
            }
        )

    positive_holdouts = sum(item["holdout"]["net_return"] > 0 for item in finalists)
    selected_holdout = finalists[0]["holdout"]
    passed_preliminary_holdout = (
        selected_holdout["net_return"] > 0
        and (selected_holdout["profit_factor"] or 0) > 1
        and selected_holdout["completed_trades"] >= 3
    )

    selected_results = {
        name: _evaluate(bars, funding_by_bar, selected_features, selected_parameters, period)
        for name, period in splits.items()
    }
    selected = {
        "parameters": asdict(selected_parameters),
        **{name: _summary(result) for name, result in selected_results.items()},
        "daily": [
            {"date": day, "return": value} for day, value in selected_results["full"].daily_returns
        ],
    }
    risk_ladder = []
    for exposure in (0.5, 1.0, 1.25, 1.5, 2.0):
        parameters = replace(selected_parameters, exposure=exposure)
        risk_ladder.append(
            {
                "exposure": exposure,
                "full": _summary(
                    _evaluate(bars, funding_by_bar, selected_features, parameters, splits["full"])
                ),
                "holdout": _summary(
                    _evaluate(
                        bars, funding_by_bar, selected_features, parameters, splits["holdout"]
                    )
                ),
            }
        )

    neighborhood = _parameter_neighborhood(
        bars,
        funding_by_bar,
        selected_parameters,
        splits,
    )
    walk_forward = _walk_forward_summary(evaluations, finalists)
    baseline_comparison = _baseline_comparison(
        args.atr_baseline_report,
        selected_results["full"],
    )
    tick_fill_results = {
        name: evaluate_spread(
            bars,
            selected_features,
            selected_parameters,
            start_ms=period[0],
            end_ms=period[1],
            funding_by_bar=funding_by_bar,
            execution_by_bar=execution_by_bar,
        )
        for name, period in splits.items()
        if name in {"holdout", "full"}
    }
    tick_fill_check = _tick_fill_comparison(selected_results, tick_fill_results)
    stress_ladder = []
    for exposure in (2.0, 3.0, 5.0, 10.0, 15.0, 20.0):
        result = evaluate_spread(
            bars,
            selected_features,
            replace(selected_parameters, exposure=exposure),
            start_ms=splits["full"][0],
            end_ms=splits["full"][1],
            funding_by_bar=funding_by_bar,
            execution_by_bar=execution_by_bar,
        )
        stress_ladder.append({"exposure": exposure, **_summary(result)})
    concentration = selected["full"]["top_five_profit_concentration"]
    evidence_gates = {
        "holdout_at_least_20_trades": selected["holdout"]["completed_trades"] >= 20,
        "top_five_gross_profit_below_50_percent": (
            concentration is not None and concentration <= 0.5
        ),
        "positive_holdout": selected["holdout"]["net_return"] > 0,
        "positive_neighbor_holdout_rate_at_least_70_percent": (
            neighborhood["positive_holdout_rate"] >= 0.7
        ),
        "geometric_daily_return_at_least_5_percent": (
            selected["full"]["geometric_daily_return"] >= 0.05
        ),
    }

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "SOXLUSDT 15m volatility-spread breakout",
        "definition": (
            "short-window normalized true range / long-window normalized true range; "
            "closed-bar breakout signal, next-bar-open parameter search with an independent "
            "next-persisted-Tick fill check"
        ),
        "data": {
            "bars": len(bars),
            "first_bar": _timestamp(bars[0].start_ms),
            "last_bar": _timestamp(bars[-1].end_ms),
            "warmup_bars": 200,
            "bars_with_execution_tick": sum(item is not None for item in execution_by_bar),
        },
        "costs": {
            "fee_bps_per_fill": 5,
            "slippage_bps_per_fill": 2,
            "funding_events": sum(len(item) for item in funding_by_bar),
            "initial_equity": 100000,
        },
        "splits": {
            name: {"start": _timestamp(period[0]), "end": _timestamp(period[1])}
            for name, period in splits.items()
        },
        "selection": {
            "candidate_count": len(grid),
            "holdout_used_for_selection": False,
            "rule": (
                "maximize the weaker train/validation geometric daily return, then penalize "
                "worst drawdown; require at least three completed trades in both periods"
            ),
        },
        "target": {
            "daily_return": 0.05,
            "achieved": selected["full"]["geometric_daily_return"] >= 0.05,
            "note": "5% per active day is a research aspiration, not an acceptance override",
        },
        "decision": {
            "status": (
                "provisional_tick_replay_candidate"
                if passed_preliminary_holdout
                else "rejected_after_holdout"
            ),
            "positive_holdouts_among_top": positive_holdouts,
            "finalists_checked": len(finalists),
            "reason": (
                "positive frozen holdout, but sample size and profit concentration are insufficient"
                if passed_preliminary_holdout
                else "selected train/validation finalist failed the frozen holdout"
            ),
            "evidence_gates": evidence_gates,
        },
        "selected": selected,
        "finalists": finalists,
        "risk_ladder": risk_ladder,
        "parameter_neighborhood": neighborhood,
        "walk_forward": walk_forward,
        "atr_baseline_comparison": baseline_comparison,
        "next_persisted_tick_fill_check": tick_fill_check,
        "high_exposure_stress": {
            "liquidation_modeled": False,
            "marking_frequency": "15m close",
            "rows": stress_ladder,
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(_markdown(payload), encoding="utf-8")
    print(args.output_dir / "README.md")
    return payload


def _evaluate(bars, funding_by_bar, features, parameters, period) -> SpreadResult:
    return evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=period[0],
        end_ms=period[1],
        funding_by_bar=funding_by_bar,
    )


def _selection_score(train: SpreadResult, validation: SpreadResult) -> float:
    if train.completed_trades < 3 or validation.completed_trades < 3:
        return -1000.0
    if train.bankrupt or validation.bankrupt:
        return -1000.0
    weaker_daily = min(train.geometric_daily_return, validation.geometric_daily_return)
    worst_drawdown = min(train.max_drawdown, validation.max_drawdown)
    return weaker_daily + 0.05 * worst_drawdown


def _summary(result: SpreadResult) -> dict[str, Any]:
    value = asdict(result)
    value.pop("trades")
    value.pop("daily_returns")
    return value


def _parameter_neighborhood(
    bars: list[SpreadBar],
    funding_by_bar: list[list[FundingRate]],
    selected: SpreadParameters,
    splits: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    values = {
        "fast_window": (16, 20, 24, 28, 32),
        "slow_window": (48, 56, 64, 72, 80, 96),
        "entry_ratio": (0.9, 0.95, 1.0, 1.05, 1.1),
        "exit_ratio": (0.6, 0.7, 0.8, 0.9, 1.0),
        "breakout_window": (16, 20, 24, 28, 32),
        "stop_atr": (2.5, 3.0, 3.5, 4.0, 4.5),
        "max_hold_bars": (64, 80, 96, 112, 128),
        "compression_ratio": (0.75, 0.8, 0.85, 0.9, 0.95),
        "compression_lookback": (8, 12, 16, 20, 24, 32),
    }
    feature_cache: dict[tuple[str, int, int, int, float, int], Any] = {}
    rows = []
    seen: set[SpreadParameters] = set()
    for parameter, candidates in values.items():
        for value in candidates:
            parameters = replace(selected, **{parameter: value})
            if parameters in seen:
                continue
            try:
                parameters.validate()
            except ValueError:
                continue
            seen.add(parameters)
            key = (
                parameters.spread_measure,
                parameters.fast_window,
                parameters.slow_window,
                parameters.breakout_window,
                parameters.compression_ratio,
                parameters.compression_lookback,
            )
            features = feature_cache.get(key)
            if features is None:
                features = build_spread_features(
                    bars,
                    fast_window=parameters.fast_window,
                    slow_window=parameters.slow_window,
                    breakout_window=parameters.breakout_window,
                    compression_ratio=parameters.compression_ratio,
                    compression_lookback=parameters.compression_lookback,
                    spread_measure=parameters.spread_measure,
                )
                feature_cache[key] = features
            results = {
                name: _summary(_evaluate(bars, funding_by_bar, features, parameters, splits[name]))
                for name in ("validation", "holdout", "full")
            }
            rows.append(
                {
                    "changed_parameter": parameter if parameters != selected else "selected",
                    "parameters": asdict(parameters),
                    **results,
                }
            )
    positive_holdouts = sum(row["holdout"]["net_return"] > 0 for row in rows)
    positive_all_segments = sum(
        row["validation"]["net_return"] > 0 and row["holdout"]["net_return"] > 0 for row in rows
    )
    return {
        "method": "one parameter varied at a time around the selected candidate",
        "candidate_count": len(rows),
        "positive_holdout_count": positive_holdouts,
        "positive_holdout_rate": positive_holdouts / len(rows),
        "positive_validation_and_holdout_count": positive_all_segments,
        "median_full_return": statistics.median(row["full"]["net_return"] for row in rows),
        "median_holdout_return": statistics.median(row["holdout"]["net_return"] for row in rows),
        "rows": rows,
    }


def _walk_forward_summary(
    evaluations: list[dict[str, Any]], finalists: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    train_ranked = sorted(
        (
            item
            for item in evaluations
            if item["train"]["completed_trades"] >= 3 and not item["train"]["bankrupt"]
        ),
        key=lambda item: _single_period_score(item["train"]),
        reverse=True,
    )
    first = train_ranked[0]
    second = finalists[0]
    return [
        {
            "development": "2026-05-17 through 2026-06-30",
            "test": "2026-07-01 through 2026-07-31",
            "selection_rule": "development geometric daily return with drawdown penalty",
            "parameters": first["parameters"],
            "development_result": first["train"],
            "test_result": first["validation"],
        },
        {
            "development": "2026-05-17 through 2026-07-31",
            "test": "2026-08-01 through 2026-08-10",
            "selection_rule": "weaker train/validation daily return with drawdown penalty",
            "parameters": second["parameters"],
            "development_train_result": second["train"],
            "development_validation_result": second["validation"],
            "test_result": second["holdout"],
        },
    ]


def _single_period_score(result: dict[str, Any]) -> float:
    return result["geometric_daily_return"] + 0.05 * result["max_drawdown"]


def _baseline_comparison(path: Path, spread: SpreadResult) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    baseline = next(
        (
            item
            for item in payload["candidates"]
            if item["parameters"]["atr_period"] == 32
            and item["parameters"]["atr_multiplier"] == 3.0
            and item["parameters"].get("profit_activation_atr") is None
            and item["parameters"].get("continuation_reentry_atr") is None
        ),
        None,
    )
    if baseline is None:
        raise ValueError(f"ATR(32) x 3 baseline is absent from {path}")
    atr_daily = {item["label"]: item["return"] for item in baseline["daily"]}
    spread_daily = dict(spread.daily_returns)
    days = sorted(atr_daily.keys() & spread_daily.keys())
    atr_values = [atr_daily[day] for day in days]
    spread_values = [spread_daily[day] for day in days]
    mixes = []
    for spread_weight in (0.25, 0.5, 0.75):
        values = [
            (1 - spread_weight) * atr_return + spread_weight * spread_return
            for atr_return, spread_return in zip(atr_values, spread_values, strict=True)
        ]
        mixes.append(
            {
                "spread_weight": spread_weight,
                "atr_weight": 1 - spread_weight,
                **daily_path_metrics(values),
            }
        )
    return {
        "source_report": str(path),
        "baseline": "ATR(32) x 3 long-only Tick replay, 1.25x exposure",
        "overlap_days": len(days),
        "pearson_daily_return_correlation": pearson_correlation(atr_values, spread_values),
        "joint_loss_days": sum(
            atr_return < 0 and spread_return < 0
            for atr_return, spread_return in zip(atr_values, spread_values, strict=True)
        ),
        "atr_daily_path": daily_path_metrics(atr_values),
        "spread_daily_path": daily_path_metrics(spread_values),
        "daily_rebalanced_mixes": mixes,
        "caveat": (
            "Daily-path comparison only: ATR uses Tick replay while volatility spread uses "
            "official 15m bars and next-open fills. Intraday drawdowns are not comparable."
        ),
    }


def _tick_fill_comparison(
    bar_open_results: dict[str, SpreadResult],
    tick_fill_results: dict[str, SpreadResult],
) -> dict[str, Any]:
    periods = {}
    for name, tick_result in tick_fill_results.items():
        bar_result = bar_open_results[name]
        paired_trades = list(zip(bar_result.trades, tick_result.trades, strict=False))
        entry_delays = [tick.entry_at_ms - bar.entry_at_ms for bar, tick in paired_trades]
        exit_delays = [tick.exit_at_ms - bar.exit_at_ms for bar, tick in paired_trades]
        price_differences_bps = [
            abs(float(tick.entry_price / bar.entry_price - Decimal("1"))) * 10_000
            for bar, tick in paired_trades
            if bar.entry_price
        ] + [
            abs(float(tick.exit_price / bar.exit_price - Decimal("1"))) * 10_000
            for bar, tick in paired_trades
            if bar.exit_price
        ]
        periods[name] = {
            "bar_open": _summary(bar_result),
            "next_persisted_tick": _summary(tick_result),
            "return_difference": tick_result.net_return - bar_result.net_return,
            "trade_count_aligned": len(bar_result.trades) == len(tick_result.trades),
            "direction_sequence_aligned": [item.direction for item in bar_result.trades]
            == [item.direction for item in tick_result.trades],
            "maximum_execution_delay_ms": max(entry_delays + exit_delays, default=0),
            "median_absolute_fill_difference_bps": (
                statistics.median(price_differences_bps) if price_differences_bps else 0.0
            ),
            "maximum_absolute_fill_difference_bps": max(price_differences_bps, default=0.0),
        }
    return {
        "method": (
            "Signals and risk checks remain on closed official 15m bars; each pending action "
            "fills on the first persisted 250ms aggregate Tick inside the next bar."
        ),
        "periods": periods,
        "scope_limit": (
            "This validates fill timing and price only. Drawdown remains marked on 15m closes, "
            "not on every intrabar Tick."
        ),
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selected"]
    parameters = selected["parameters"]
    full = selected["full"]
    lines = [
        "# SOXLUSDT Volatility-Spread Exploration",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Method",
        "",
        (
            "The volatility spread is the ratio of short-window to long-window normalized true "
            "range. Entries require a prior-channel breakout. `compression_release` additionally "
            "requires a low-volatility observation during the preceding 16 bars. Signals use "
            "closed bars and execute at the next bar open."
        ),
        "",
        (
            "Costs: 5 bps fee and 2 bps slippage per fill, Binance funding included. The first "
            "200 bars are indicator warmup. August is a frozen holdout and did not participate "
            "in selection."
        ),
        "",
        "## Selected Research Candidate",
        "",
        "```json",
        json.dumps(parameters, indent=2),
        "```",
        "",
        f"Decision: **{payload['decision']['status']}**. {payload['decision']['reason']}.",
        "",
        (
            f"Positive holdouts among the top train/validation finalists: "
            f"{payload['decision']['positive_holdouts_among_top']}/"
            f"{payload['decision']['finalists_checked']}."
        ),
        "",
        "| Period | Return | Geometric/day | Days >= 5% | Win rate | Trades | Max DD | PF |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "validation", "holdout", "full"):
        result = selected[name]
        lines.append(
            f"| {name} | {_pct(result['net_return'])} | "
            f"{_pct(result['geometric_daily_return'])} | {_pct(result['target_day_rate'])} | "
            f"{_pct(result['win_rate'])} | {result['completed_trades']} | "
            f"{_pct(result['max_drawdown'])} | {_number(result['profit_factor'])} |"
        )
    lines.extend(
        [
            "",
            "## Evidence Quality",
            "",
            (
                f"The full replay completed only {full['completed_trades']} trades. The five "
                f"largest winning trades account for "
                f"{_pct(full['top_five_profit_concentration'])} of gross profit; the frozen "
                f"holdout contains only {selected['holdout']['completed_trades']} trades. "
                "These are insufficient observations for production promotion."
            ),
            "",
            "| Gate | Passed |",
            "|---|:---:|",
        ]
    )
    gate_labels = {
        "holdout_at_least_20_trades": "At least 20 holdout trades",
        "top_five_gross_profit_below_50_percent": "Top-five profit concentration <= 50%",
        "positive_holdout": "Positive frozen holdout",
        "positive_neighbor_holdout_rate_at_least_70_percent": (
            "At least 70% of one-at-a-time neighbors positive in holdout"
        ),
        "geometric_daily_return_at_least_5_percent": "Geometric daily return >= 5%",
    }
    for key, passed in payload["decision"]["evidence_gates"].items():
        lines.append(f"| {gate_labels[key]} | {'yes' if passed else 'no'} |")

    neighborhood = payload["parameter_neighborhood"]
    lines.extend(
        [
            "",
            "## Parameter Neighborhood",
            "",
            (
                f"One parameter was changed at a time across {neighborhood['candidate_count']} "
                f"nearby configurations. {neighborhood['positive_holdout_count']} "
                f"({_pct(neighborhood['positive_holdout_rate'])}) were positive in the frozen "
                f"holdout; {neighborhood['positive_validation_and_holdout_count']} were positive "
                "in both July and the frozen holdout."
            ),
            "",
            (
                f"Median full return was {_pct(neighborhood['median_full_return'])}; median "
                f"holdout return was {_pct(neighborhood['median_holdout_return'])}. Some fast- "
                "and slow-window neighbors still lost money, so this is local support rather "
                "than proof of robust parameters. Full rows are in `results.json`."
            ),
            "",
            "## Walk-Forward Checks",
            "",
            "| Development | Forward test | Direction | Test return | Trades | Max DD |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for fold in payload["walk_forward"]:
        test = fold["test_result"]
        lines.append(
            f"| {fold['development']} | {fold['test']} | "
            f"{fold['parameters']['direction']} | {_pct(test['net_return'])} | "
            f"{test['completed_trades']} | {_pct(test['max_drawdown'])} |"
        )

    tick_fill = payload["next_persisted_tick_fill_check"]
    lines.extend(
        [
            "",
            "## Next-Persisted-Tick Fill Check",
            "",
            tick_fill["method"],
            "",
            "| Period | Bar-open return | Tick-fill return | Difference | "
            "Median fill delta | Max fill delta | Max delay |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name in ("holdout", "full"):
        check = tick_fill["periods"][name]
        lines.append(
            f"| {name} | {_pct(check['bar_open']['net_return'])} | "
            f"{_pct(check['next_persisted_tick']['net_return'])} | "
            f"{_pct(check['return_difference'])} | "
            f"{check['median_absolute_fill_difference_bps']:.3f} bps | "
            f"{check['maximum_absolute_fill_difference_bps']:.3f} bps | "
            f"{check['maximum_execution_delay_ms']} ms |"
        )
    lines.extend(["", tick_fill["scope_limit"]])

    comparison = payload["atr_baseline_comparison"]
    lines.extend(
        [
            "",
            "## ATR Baseline Diversification",
            "",
            (
                f"Across {comparison['overlap_days']} UTC days, Pearson daily-return correlation "
                f"with the ATR(32) x 3 long-only Tick replay was "
                f"{comparison['pearson_daily_return_correlation']:.3f}. Both paths lost on "
                f"{comparison['joint_loss_days']} days."
            ),
            "",
            "| Daily-rebalanced path | Return | Geometric/day | Daily-close max DD |",
            "|---|---:|---:|---:|",
            (
                f"| ATR baseline | {_pct(comparison['atr_daily_path']['net_return'])} | "
                f"{_pct(comparison['atr_daily_path']['geometric_daily_return'])} | "
                f"{_pct(comparison['atr_daily_path']['max_daily_close_drawdown'])} |"
            ),
            (
                f"| Volatility spread | {_pct(comparison['spread_daily_path']['net_return'])} | "
                f"{_pct(comparison['spread_daily_path']['geometric_daily_return'])} | "
                f"{_pct(comparison['spread_daily_path']['max_daily_close_drawdown'])} |"
            ),
        ]
    )
    for mix in comparison["daily_rebalanced_mixes"]:
        lines.append(
            f"| {mix['atr_weight']:.0%} ATR / {mix['spread_weight']:.0%} spread | "
            f"{_pct(mix['net_return'])} | {_pct(mix['geometric_daily_return'])} | "
            f"{_pct(mix['max_daily_close_drawdown'])} |"
        )
    lines.extend(
        [
            "",
            comparison["caveat"],
            "",
            "## Exposure Ladder",
            "",
            "| Exposure | Full return | Full max DD | Holdout return | Holdout max DD |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for item in payload["risk_ladder"]:
        lines.append(
            f"| {item['exposure']:.2f}x | {_pct(item['full']['net_return'])} | "
            f"{_pct(item['full']['max_drawdown'])} | {_pct(item['holdout']['net_return'])} | "
            f"{_pct(item['holdout']['max_drawdown'])} |"
        )
    stress = payload["high_exposure_stress"]
    lines.extend(
        [
            "",
            "## High-Exposure Stress Test",
            "",
            "| Target exposure | Tick-fill return | Geometric/day | 15m-close max DD |",
            "|---:|---:|---:|---:|",
        ]
    )
    for item in stress["rows"]:
        lines.append(
            f"| {item['exposure']:.1f}x | {_pct(item['net_return'])} | "
            f"{_pct(item['geometric_daily_return'])} | {_pct(item['max_drawdown'])} |"
        )
    lines.extend(
        [
            "",
            (
                "This is an intentionally optimistic stress test: liquidation is not modeled "
                "and risk is marked only at 15m closes. It must not be interpreted as executable "
                "leverage guidance. Even the best tested geometric daily result remains below 5%."
            ),
        ]
    )
    target_compound = (1.05 ** full["active_days"]) - 1
    lines.extend(
        [
            "",
            "## 5% Daily Target Check",
            "",
            f"Over {full['active_days']} active UTC days, 5% daily compounding requires "
            f"`{_pct(target_compound)}` cumulative return. The selected candidate produced "
            f"{_pct(full['net_return'])}, with {_pct(full['target_day_rate'])} of active days "
            "at or above +5%.",
            "",
            (
                "The 5% daily objective was not achieved. This remains an exploratory bar-level "
                "candidate, not a production strategy. Next-persisted-Tick fills have been "
                "checked, but substantially more out-of-sample trades and full intrabar risk "
                "measurement are still required."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2%}"


def _number(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}"


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    run()
