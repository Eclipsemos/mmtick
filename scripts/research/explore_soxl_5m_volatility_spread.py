#!/usr/bin/env python3
"""Explore a five-minute SOXLUSDT volatility-spread strategy from persisted ticks."""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import explore_soxl_volatility_spread_v2 as phase_two

from mastermind_tick.models import FundingRate
from mastermind_tick.volatility_spread import (
    SpreadBar,
    SpreadExecution,
    SpreadFeatures,
    SpreadParameters,
    SpreadResult,
    build_spread_features,
    evaluate_spread,
)
from mastermind_tick.volatility_spread_forward import load_forward_market, load_frozen_candidate

BAR_MS = 5 * 60_000
TRAIN_END = date(2026, 6, 30)
VALIDATION_START = date(2026, 7, 1)
VALIDATION_END = date(2026, 7, 31)
CONFIRMATION_START = date(2026, 8, 1)
CONFIRMATION_END = date(2026, 8, 10)
REVEALED_START = date(2026, 8, 11)
REVEALED_END = date(2026, 8, 13)


def main() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("strategies/candidates/soxl_volatility_spread_true_range_v1.json"),
    )
    parser.add_argument("--finalists", type=int, default=20)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14-5m"),
    )
    args = parser.parse_args()
    if args.finalists < 1:
        raise ValueError("finalists must be positive")

    frozen = load_frozen_candidate(args.candidate)
    bars, funding_by_bar, executions = _load_5m_market(args.database, frozen.instrument_id)
    periods = _periods(frozen.continuous_replay_start_ms)
    grid = _candidate_grid()
    feature_cache: dict[tuple[Any, ...], SpreadFeatures] = {}
    evaluated = []
    for index, parameters in enumerate(grid, start=1):
        features = _features(bars, parameters, feature_cache)
        train = _evaluate(bars, funding_by_bar, executions, features, parameters, periods["train"])
        validation = _evaluate(
            bars, funding_by_bar, executions, features, parameters, periods["validation"]
        )
        train_summary = _summary(train)
        validation_summary = _summary(validation)
        evaluated.append(
            {
                "parameters": asdict(parameters),
                "selection_score": _selection_score(train, validation),
                "train": train_summary,
                "validation": validation_summary,
            }
        )
        if index % 250 == 0:
            print(f"5m search {index}/{len(grid)}", flush=True)
    eligible = [item for item in evaluated if _eligible(item["train"], item["validation"])]
    finalists = sorted(
        eligible or evaluated, key=lambda item: item["selection_score"], reverse=True
    )[: args.finalists]
    for item in finalists:
        parameters = SpreadParameters(**item["parameters"])
        confirmation = _evaluate(
            bars,
            funding_by_bar,
            executions,
            _features(bars, parameters, feature_cache),
            parameters,
            periods["confirmation"],
        )
        item["confirmation"] = _summary(confirmation)
    confirmation_passed = [
        item
        for item in finalists
        if bool(eligible)
        and item["confirmation"]["net_return"] > 0
        and item["confirmation"]["completed_trades"] >= 2
    ]
    selected = confirmation_passed[0] if confirmation_passed else finalists[0]
    parameters = SpreadParameters(**selected["parameters"])
    features = _features(bars, parameters, feature_cache)
    results = {
        name: _evaluate(bars, funding_by_bar, executions, features, parameters, period)
        for name, period in periods.items()
    }
    base_bars, base_funding, base_executions = load_forward_market(args.database, frozen)
    base_features = build_spread_features(
        base_bars,
        fast_window=frozen.parameters.fast_window,
        slow_window=frozen.parameters.slow_window,
        breakout_window=frozen.parameters.breakout_window,
        compression_ratio=frozen.parameters.compression_ratio,
        compression_lookback=frozen.parameters.compression_lookback,
        spread_measure=frozen.parameters.spread_measure,
    )
    baseline = {
        name: evaluate_spread(
            base_bars,
            base_features,
            frozen.parameters,
            start_ms=period[0],
            end_ms=period[1],
            funding_by_bar=base_funding,
            execution_by_bar=base_executions,
        )
        for name, period in periods.items()
    }
    tick_risk = phase_two._tick_path_risk(
        args.database,
        results["development"].trades,
        [event for events in funding_by_bar for event in events],
        expected_final_equity=results["development"].final_equity,
    )
    payload = {
        "schema_version": 1,
        "strategy": "SOXLUSDT five-minute volatility spread",
        "status": (
            "exploratory_post_reveal_no_clean_holdout"
            if eligible
            else "rejected_no_train_validation_candidate"
        ),
        "parameter_search_performed": True,
        "fresh_holdout_used_for_selection": False,
        "market": {
            "bar_interval_minutes": 5,
            "bar_source": "persisted_250ms_agg_trades",
            "fill_timing": "first_persisted_tick_in_next_5m_bar_after_closed_signal",
            "funding_included": True,
            "bars": len(bars),
            "no_trade_carry_bars": sum(execution is None for execution in executions),
        },
        "splits": {
            name: {"start": _timestamp(period[0]), "end": _timestamp(period[1])}
            for name, period in periods.items()
        },
        "selection": {
            "candidate_count": len(grid),
            "eligible_count": len(eligible),
            "finalists_checked": len(finalists),
            "confirmation_positive_finalists": len(confirmation_passed),
            "rule": "positive train and validation, then positive August 1-10 confirmation",
            "selected": selected,
            "finalists": finalists,
        },
        "five_minute": {
            "parameters": asdict(parameters),
            "results": {name: _summary(result) for name, result in results.items()},
            "daily": {name: list(result.daily_returns) for name, result in results.items()},
            "development_tick_path": tick_risk,
        },
        "frozen_15m_baseline": {
            "parameters": asdict(frozen.parameters),
            "results": {name: _summary(result) for name, result in baseline.items()},
        },
        "target": {"geometric_daily_return": 0.05, "achieved": False},
        "limitations": [
            "The five-minute parameter search has no clean holdout after selection.",
            "August 11-13 was already revealed and is diagnostic only.",
            "Liquidation and shared intraday portfolio margin are not modeled.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(_markdown(payload), encoding="utf-8")
    print(args.output_dir / "README.md")
    return payload


def _load_5m_market(
    database: Path, instrument_id: str
) -> tuple[list[SpreadBar], list[list[FundingRate]], list[SpreadExecution | None]]:
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        ticks = connection.execute(
            """
            SELECT timestamp_ms, open_price, high_price, low_price, price, quantity
            FROM agg_trades
            WHERE instrument_id = ?
            ORDER BY timestamp_ms
            """,
            (instrument_id,),
        )
        bars, executions = _resample_ticks(ticks)
        funding = [
            FundingRate(timestamp_ms=int(row[0]), rate=Decimal(row[1]), mark_price=Decimal(row[2]))
            for row in connection.execute(
                """
                SELECT timestamp_ms, rate, mark_price
                FROM funding_rates WHERE instrument_id = ? ORDER BY timestamp_ms
                """,
                (instrument_id,),
            )
        ]
    if not bars:
        raise RuntimeError("no five-minute bars were constructed")
    bar_ends = [bar.end_ms for bar in bars]
    funding_by_bar: list[list[FundingRate]] = [[] for _ in bars]
    for event in funding:
        index = bisect.bisect_left(bar_ends, event.timestamp_ms)
        if index < len(bars):
            funding_by_bar[index].append(event)
    return bars, funding_by_bar, executions


def _resample_ticks(rows) -> tuple[list[SpreadBar], list[SpreadExecution | None]]:
    bars: list[SpreadBar] = []
    executions: list[SpreadExecution | None] = []
    current_start: int | None = None
    open_price = high_price = low_price = close_price = volume = None
    execution: SpreadExecution | None = None
    for timestamp_ms, tick_open, tick_high, tick_low, tick_close, quantity in rows:
        bucket_start = int(timestamp_ms) // BAR_MS * BAR_MS
        if current_start is not None and bucket_start != current_start:
            assert open_price is not None and high_price is not None and low_price is not None
            assert close_price is not None and volume is not None
            bars.append(
                SpreadBar(
                    start_ms=current_start,
                    end_ms=current_start + BAR_MS - 1,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
                )
            )
            executions.append(execution)
            current_start = None
        if current_start is None:
            current_start = bucket_start
            open_price = Decimal(tick_open)
            high_price = Decimal(tick_high)
            low_price = Decimal(tick_low)
            close_price = Decimal(tick_close)
            volume = Decimal(quantity)
            execution = SpreadExecution(timestamp_ms=int(timestamp_ms), price=Decimal(tick_close))
            continue
        high_price = max(high_price, Decimal(tick_high))
        low_price = min(low_price, Decimal(tick_low))
        close_price = Decimal(tick_close)
        volume += Decimal(quantity)
    if current_start is not None:
        assert open_price is not None and high_price is not None and low_price is not None
        assert close_price is not None and volume is not None
        bars.append(
            SpreadBar(
                start_ms=current_start,
                end_ms=current_start + BAR_MS - 1,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
            )
        )
        executions.append(execution)
    filled_bars, filled_executions = _fill_no_trade_bars(bars, executions)
    _verify_continuity(filled_bars, filled_executions)
    return filled_bars, filled_executions


def _fill_no_trade_bars(
    bars: list[SpreadBar], executions: list[SpreadExecution | None]
) -> tuple[list[SpreadBar], list[SpreadExecution | None]]:
    if len(bars) != len(executions) or not bars:
        raise ValueError("source bars and executions must be non-empty and aligned")
    filled_bars = [bars[0]]
    filled_executions = [executions[0]]
    for bar, execution in zip(bars[1:], executions[1:], strict=True):
        previous = filled_bars[-1]
        next_start = previous.start_ms + BAR_MS
        while next_start < bar.start_ms:
            filled_bars.append(
                SpreadBar(
                    start_ms=next_start,
                    end_ms=next_start + BAR_MS - 1,
                    open=previous.close,
                    high=previous.close,
                    low=previous.close,
                    close=previous.close,
                    volume=Decimal("0"),
                )
            )
            filled_executions.append(None)
            previous = filled_bars[-1]
            next_start += BAR_MS
        filled_bars.append(bar)
        filled_executions.append(execution)
    return filled_bars, filled_executions


def _verify_continuity(bars: list[SpreadBar], executions: list[SpreadExecution | None]) -> None:
    if len(bars) != len(executions) or not bars:
        raise ValueError("five-minute bars and executions must be non-empty and aligned")
    for previous, current in zip(bars[:-1], bars[1:], strict=True):
        if current.start_ms != previous.start_ms + BAR_MS:
            raise ValueError(f"missing five-minute bar at {previous.start_ms + BAR_MS}")


def _candidate_grid() -> list[SpreadParameters]:
    return [
        SpreadParameters(
            variant=variant,
            direction="long_short",
            fast_window=fast,
            slow_window=slow,
            entry_ratio=entry,
            exit_ratio=exit_ratio,
            breakout_window=breakout,
            stop_atr=stop,
            max_hold_bars=max_hold,
            exposure=1.25,
            compression_ratio=0.85,
            compression_lookback=48,
            spread_measure="true_range",
        )
        for variant in ("expansion_breakout", "compression_release")
        for fast, slow in ((24, 96), (36, 192))
        for entry in (1.0, 1.1, 1.2)
        for exit_ratio in (0.8, 1.0)
        for breakout in (48, 72)
        for stop in (2.0, 2.5)
        for max_hold in (144, 288)
    ]


def _features(
    bars: list[SpreadBar],
    parameters: SpreadParameters,
    cache: dict[tuple[Any, ...], SpreadFeatures],
) -> SpreadFeatures:
    key = (
        parameters.fast_window,
        parameters.slow_window,
        parameters.breakout_window,
        parameters.compression_ratio,
        parameters.compression_lookback,
    )
    if key not in cache:
        cache[key] = build_spread_features(
            bars,
            fast_window=parameters.fast_window,
            slow_window=parameters.slow_window,
            breakout_window=parameters.breakout_window,
            compression_ratio=parameters.compression_ratio,
            compression_lookback=parameters.compression_lookback,
            spread_measure=parameters.spread_measure,
        )
    return cache[key]


def _evaluate(bars, funding, executions, features, parameters, period) -> SpreadResult:
    return evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=period[0],
        end_ms=period[1],
        funding_by_bar=funding,
        execution_by_bar=executions,
    )


def _eligible(train: dict[str, Any], validation: dict[str, Any]) -> bool:
    return (
        train["net_return"] > 0
        and validation["net_return"] > 0
        and train["completed_trades"] >= 8
        and validation["completed_trades"] >= 8
    )


def _selection_score(train: SpreadResult, validation: SpreadResult) -> float:
    return min(train.geometric_daily_return, validation.geometric_daily_return) - 0.25 * max(
        abs(train.max_drawdown), abs(validation.max_drawdown)
    )


def _summary(result: SpreadResult) -> dict[str, Any]:
    return {
        "net_return": result.net_return,
        "geometric_daily_return": result.geometric_daily_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "target_day_rate": result.target_day_rate,
    }


def _periods(replay_start_ms: int) -> dict[str, tuple[int, int]]:
    return {
        "train": (replay_start_ms, _day_end(TRAIN_END)),
        "validation": (_day_start(VALIDATION_START), _day_end(VALIDATION_END)),
        "confirmation": (_day_start(CONFIRMATION_START), _day_end(CONFIRMATION_END)),
        "development": (replay_start_ms, _day_end(CONFIRMATION_END)),
        "revealed_diagnostic": (_day_start(REVEALED_START), _day_end(REVEALED_END)),
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    five_minute = payload["five_minute"]["results"]
    baseline = payload["frozen_15m_baseline"]["results"]
    return "\n".join(
        [
            "# SOXLUSDT Five-Minute Volatility-Spread Exploration",
            "",
            f"Status: **{payload['status']}**  ",
            (
                f"Best {'eligible' if payload['selection']['eligible_count'] else 'rejected'} "
                f"parameters: `{_parameter_label(selected['parameters'])}`  "
            ),
            "5% daily target: **not achieved**",
            "",
            "Five-minute bars are constructed from persisted 250ms aggregate trades. Signals use "
            "closed bars and the order fills at the first persisted Tick in the subsequent 5m bar. "
            "No-trade intervals are flat carry bars and cannot fill an order.",
            "",
            (
                "| Path | Train geo/day | Validation geo/day | Confirmation geo/day | "
                "Development geo/day | Revealed 8/11-13 | 5m close DD |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|",
            _row("5m spread", five_minute),
            _row("Frozen 15m", baseline),
            "",
            f"The search tested {payload['selection']['candidate_count']} candidates; "
            f"{payload['selection']['eligible_count']} passed train/validation and "
            f"{payload['selection']['confirmation_positive_finalists']} of "
            f"{payload['selection']['finalists_checked']} finalists were positive in confirmation.",
            "",
            "This is not a production recommendation. August 11-13 was already revealed and is "
            "diagnostic only; the current 5m grid is rejected before forward monitoring.",
            "",
        ]
    )


def _row(name: str, results: dict[str, dict[str, Any]]) -> str:
    return (
        f"| {name} | {_percent(results['train']['geometric_daily_return'])} | "
        f"{_percent(results['validation']['geometric_daily_return'])} | "
        f"{_percent(results['confirmation']['geometric_daily_return'])} | "
        f"{_percent(results['development']['geometric_daily_return'])} | "
        f"{_percent(results['revealed_diagnostic']['net_return'])} | "
        f"{_percent(results['development']['max_drawdown'])} |"
    )


def _parameter_label(parameters: dict[str, Any]) -> str:
    return (
        f"{parameters['variant']}/{parameters['fast_window']}-{parameters['slow_window']}/"
        f"entry_{parameters['entry_ratio']:g}/exit_{parameters['exit_ratio']:g}/"
        f"breakout_{parameters['breakout_window']}/stop_{parameters['stop_atr']:g}/"
        f"hold_{parameters['max_hold_bars']}"
    )


def _percent(value: float) -> str:
    return f"{value:+.2%}"


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
