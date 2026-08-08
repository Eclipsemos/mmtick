#!/usr/bin/env python3
"""Walk-forward parameter search for the SOXLUSDT tick strategy."""

from __future__ import annotations

import argparse
import json
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from mastermind_tick.backtest import (
    ReplayATRTickStrategy,
    ReplayBroker,
    ReplayCandidate,
    ReplayParameters,
    ReplayResult,
    _candidate_result,
    _load_funding_rates,
    _load_warmup_bars,
)
from mastermind_tick.config import load_settings
from mastermind_tick.models import Bar, Tick


@dataclass(frozen=True)
class CandidateSpec:
    name: str
    instrument_id: str
    atr_period: int
    atr_multiplier: float
    bar_minutes: int = 15
    allow_short: bool | None = None
    trend_period: int = 8
    minimum_efficiency: float = 0.25
    reversal_confirmation_atr: float = 0.25
    profit_activation_atr: float | None = None
    profit_trailing_atr: float | None = None
    continuation_reentry_atr: float | None = None
    leverage: int | None = None
    position_fraction: float | None = None


@dataclass(frozen=True)
class EvaluationTask:
    config: str
    spec: CandidateSpec
    period_name: str
    start_ms: int
    end_ms: int


def evaluate(task: EvaluationTask) -> dict:
    settings = load_settings(task.config)
    instrument = next(item for item in settings.instruments if item.id == task.spec.instrument_id)
    if task.spec.allow_short is not None:
        instrument = replace(instrument, allow_short=task.spec.allow_short)
    if task.spec.leverage is not None:
        instrument = replace(instrument, leverage=task.spec.leverage)
    if task.spec.position_fraction is not None:
        instrument = replace(instrument, position_fraction=task.spec.position_fraction)
    database_uri = f"file:{settings.database_path}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        warmup = _load_timeframe_warmup_bars(
            connection,
            instrument.market_id,
            task.start_ms,
            settings.warmup_bars,
            task.spec.bar_minutes,
        )
        required_warmup = max(task.spec.atr_period, task.spec.trend_period + 1)
        if len(warmup) < required_warmup:
            raise ValueError(
                f"insufficient {task.spec.bar_minutes}m warmup for {task.spec.name}: "
                f"{len(warmup)} < {required_warmup}"
            )
        funding_rates = _load_funding_rates(
            connection,
            instrument.market_id,
            task.start_ms,
            task.end_ms,
        )
        strategy = ReplayATRTickStrategy(
            task.spec.atr_period,
            task.spec.atr_multiplier,
            task.spec.bar_minutes,
            task.spec.trend_period,
            task.spec.minimum_efficiency,
            task.spec.reversal_confirmation_atr,
        )
        strategy.bootstrap(warmup)
        parameters = ReplayParameters(
            task.spec.atr_period,
            task.spec.atr_multiplier,
            variant=task.spec.name,
            profit_activation_atr=task.spec.profit_activation_atr,
            profit_trailing_atr=task.spec.profit_trailing_atr,
            continuation_reentry_atr=task.spec.continuation_reentry_atr,
        )
        broker = ReplayBroker(
            instrument,
            Decimal(str(settings.initial_cash)),
            Decimal(str(instrument.position_fraction)),
            Decimal(str(instrument.fee_bps)),
            Decimal(str(instrument.slippage_bps)),
            Decimal(str(instrument.minimum_notional)),
        )
        candidate = ReplayCandidate(parameters, strategy, broker)
        tick_count = 0
        raw_trade_count = 0
        last_price: Decimal | None = None
        rows = connection.execute(
            """
            SELECT event_id, timestamp_ms, price, open_price, high_price, low_price,
                   quantity, source, first_trade_id, last_trade_id
            FROM agg_trades
            WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
            ORDER BY timestamp_ms, received_at_ms, event_id
            """,
            (instrument.market_id, task.start_ms, task.end_ms),
        )
        for row in rows:
            tick = _tick_from_row(row)
            candidate.process_tick(tick, funding_rates)
            tick_count += 1
            raw_trade_count += (
                int(row["last_trade_id"]) - int(row["first_trade_id"]) + 1
                if row["first_trade_id"] is not None and row["last_trade_id"] is not None
                else 1
            )
            last_price = tick.price
    if last_price is None:
        raise ValueError(f"no data for {task.period_name}: {task.spec.name}")
    result: ReplayResult = _candidate_result(
        candidate,
        instrument,
        task.start_ms,
        task.end_ms,
        tick_count,
        raw_trade_count,
        len(warmup),
        last_price,
    )
    return {
        "period": task.period_name,
        "spec": asdict(task.spec),
        "result": asdict(result),
    }


def _tick_from_row(row: sqlite3.Row) -> Tick:
    return Tick(
        event_id=row["event_id"],
        timestamp_ms=int(row["timestamp_ms"]),
        price=Decimal(row["price"]),
        quantity=Decimal(row["quantity"]),
        source=row["source"],
        first_trade_id=row["first_trade_id"],
        last_trade_id=row["last_trade_id"],
        open_price=Decimal(row["open_price"]) if row["open_price"] else None,
        high_price=Decimal(row["high_price"]) if row["high_price"] else None,
        low_price=Decimal(row["low_price"]) if row["low_price"] else None,
    )


def _load_timeframe_warmup_bars(
    connection: sqlite3.Connection,
    instrument_id: str,
    start_ms: int,
    limit: int,
    bar_minutes: int,
) -> list[Bar]:
    if bar_minutes == 15:
        return _load_warmup_bars(connection, instrument_id, start_ms, limit)
    if bar_minutes % 15 == 0:
        return _resample_official_bars(
            connection,
            instrument_id,
            start_ms,
            limit,
            bar_minutes,
        )
    return _aggregate_tick_bars(
        connection,
        instrument_id,
        start_ms,
        limit,
        bar_minutes,
    )


def _resample_official_bars(
    connection: sqlite3.Connection,
    instrument_id: str,
    start_ms: int,
    limit: int,
    bar_minutes: int,
) -> list[Bar]:
    bar_ms = bar_minutes * 60_000
    source_bars = bar_minutes // 15
    rows = connection.execute(
        """
        SELECT start_ms, open, high, low, close, volume, trade_count
        FROM ohlcv_bars
        WHERE instrument_id = ? AND interval_minutes = 15
          AND is_closed = 1 AND end_ms < ?
        ORDER BY start_ms DESC
        LIMIT ?
        """,
        (instrument_id, start_ms, (limit + 2) * source_bars),
    ).fetchall()
    bars: dict[int, Bar] = {}
    for row in reversed(rows):
        bucket = int(row["start_ms"]) // bar_ms * bar_ms
        value = bars.get(bucket)
        if value is None:
            bars[bucket] = Bar(
                start_ms=bucket,
                end_ms=bucket + bar_ms - 1,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                trade_count=int(row["trade_count"]),
            )
            continue
        value.high = max(value.high, Decimal(row["high"]))
        value.low = min(value.low, Decimal(row["low"]))
        value.close = Decimal(row["close"])
        value.volume += Decimal(row["volume"])
        value.trade_count += int(row["trade_count"])
    completed = [bar for bar in bars.values() if bar.end_ms < start_ms]
    return completed[-limit:]


def _aggregate_tick_bars(
    connection: sqlite3.Connection,
    instrument_id: str,
    start_ms: int,
    limit: int,
    bar_minutes: int,
) -> list[Bar]:
    bar_ms = bar_minutes * 60_000
    lower_bound = start_ms - (limit + 2) * bar_ms
    rows = connection.execute(
        """
        SELECT event_id, timestamp_ms, price, open_price, high_price, low_price,
               quantity, source, first_trade_id, last_trade_id
        FROM agg_trades
        WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
        ORDER BY timestamp_ms, received_at_ms, event_id
        """,
        (instrument_id, lower_bound, start_ms - 1),
    )
    bars: dict[int, Bar] = {}
    for row in rows:
        tick = _tick_from_row(row)
        bucket = tick.timestamp_ms // bar_ms * bar_ms
        value = bars.get(bucket)
        if value is None:
            bars[bucket] = Bar(
                start_ms=bucket,
                end_ms=bucket + bar_ms - 1,
                open=tick.open_price or tick.price,
                high=tick.high_price or tick.price,
                low=tick.low_price or tick.price,
                close=tick.price,
                volume=tick.quantity,
                trade_count=(
                    tick.last_trade_id - tick.first_trade_id + 1
                    if tick.first_trade_id is not None and tick.last_trade_id is not None
                    else 1
                ),
            )
            continue
        value.update(tick)
    completed = [bar for bar in bars.values() if bar.end_ms < start_ms]
    return completed[-limit:]


def baseline_grid() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            name=f"{direction}_atr_{period}_{multiplier:g}",
            instrument_id=("soxl_perp" if direction == "both" else "soxl_perp_long"),
            atr_period=period,
            atr_multiplier=multiplier,
        )
        for direction in ("both", "long")
        for period in (14, 21, 28)
        for multiplier in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)
    ]


def timeframe_coarse_grid() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            name=(
                f"{direction}_{bar_minutes}m_atr_{period}_{multiplier:g}_"
                f"eff_{minimum_efficiency:g}"
            ),
            instrument_id="soxl_perp",
            atr_period=period,
            atr_multiplier=multiplier,
            bar_minutes=bar_minutes,
            allow_short=allow_short,
            trend_period=8,
            minimum_efficiency=minimum_efficiency,
        )
        for direction, allow_short in (("both", True), ("long", False))
        for bar_minutes in (5, 15, 30, 60)
        for period in (14, 21, 32)
        for multiplier in (2.0, 3.0, 4.0)
        for minimum_efficiency in (0.0, 0.25)
    ]


def timeframe_trend_grid() -> list[CandidateSpec]:
    bases = (
        ("long_15m_32_3", 15, 32, 3.0, False),
        ("both_15m_14_3", 15, 14, 3.0, True),
        ("both_15m_21_3", 15, 21, 3.0, True),
        ("both_30m_14_4", 30, 14, 4.0, True),
        ("both_30m_32_4", 30, 32, 4.0, True),
        ("long_30m_14_4", 30, 14, 4.0, False),
        ("both_60m_32_2", 60, 32, 2.0, True),
    )
    trend_periods = {
        15: (4, 8, 16, 32),
        30: (2, 4, 8, 16),
        60: (2, 4, 8, 12),
    }
    return [
        CandidateSpec(
            name=(
                f"{name}_eff_{trend_period}_{minimum_efficiency:g}"
                if minimum_efficiency > 0
                else f"{name}_no_efficiency_filter"
            ),
            instrument_id="soxl_perp",
            atr_period=atr_period,
            atr_multiplier=atr_multiplier,
            bar_minutes=bar_minutes,
            allow_short=allow_short,
            trend_period=trend_period,
            minimum_efficiency=minimum_efficiency,
        )
        for name, bar_minutes, atr_period, atr_multiplier, allow_short in bases
        for trend_period, minimum_efficiency in (
            [(8, 0.0)]
            + [
                (period, threshold)
                for period in trend_periods[bar_minutes]
                for threshold in (0.15, 0.25, 0.35, 0.45, 0.55)
            ]
        )
    ]


def timeframe_trend_refined_grid() -> list[CandidateSpec]:
    sixty_minute = [
        CandidateSpec(
            name=f"both_60m_32_2_eff_{period}_{threshold:g}",
            instrument_id="soxl_perp",
            atr_period=32,
            atr_multiplier=2.0,
            bar_minutes=60,
            allow_short=True,
            trend_period=period,
            minimum_efficiency=threshold,
        )
        for period in (8, 10, 12, 14, 16)
        for threshold in (0.1, 0.125, 0.15, 0.175, 0.2, 0.25)
    ]
    thirty_minute = [
        CandidateSpec(
            name=f"both_30m_14_4_eff_{period}_{threshold:g}",
            instrument_id="soxl_perp",
            atr_period=14,
            atr_multiplier=4.0,
            bar_minutes=30,
            allow_short=True,
            trend_period=period,
            minimum_efficiency=threshold,
        )
        for period in (6, 8, 10, 12)
        for threshold in (0.45, 0.5, 0.55, 0.6, 0.65)
    ]
    return [*sixty_minute, *thirty_minute]


def timeframe_atr_refined_grid() -> list[CandidateSpec]:
    thirty_minute = [
        CandidateSpec(
            name=f"both_30m_atr_{period}_{multiplier:g}_eff_8_0.55",
            instrument_id="soxl_perp",
            atr_period=period,
            atr_multiplier=multiplier,
            bar_minutes=30,
            allow_short=True,
            trend_period=8,
            minimum_efficiency=0.55,
        )
        for period in (10, 12, 14, 16, 18)
        for multiplier in (3.5, 4.0, 4.5)
    ]
    sixty_minute = [
        CandidateSpec(
            name=f"both_60m_atr_{period}_{multiplier:g}_eff_12_0.15",
            instrument_id="soxl_perp",
            atr_period=period,
            atr_multiplier=multiplier,
            bar_minutes=60,
            allow_short=True,
            trend_period=12,
            minimum_efficiency=0.15,
        )
        for period in (28, 32, 36)
        for multiplier in (1.75, 2.0, 2.25)
    ]
    return [*thirty_minute, *sixty_minute]


def thirty_minute_trend_final_grid() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            name=f"both_30m_atr_14_3.5_eff_{period}_{threshold:g}",
            instrument_id="soxl_perp",
            atr_period=14,
            atr_multiplier=3.5,
            bar_minutes=30,
            allow_short=True,
            trend_period=period,
            minimum_efficiency=threshold,
        )
        for period in (6, 8, 10, 12)
        for threshold in (0.45, 0.5, 0.55, 0.6, 0.65)
    ]


def thirty_minute_final_controls_grid() -> list[CandidateSpec]:
    direction_and_confirmation = [
        CandidateSpec(
            name=(
                f"{direction}_30m_atr_14_3.5_eff_8_{threshold:g}_"
                f"reversal_{confirmation:g}"
            ),
            instrument_id="soxl_perp",
            atr_period=14,
            atr_multiplier=3.5,
            bar_minutes=30,
            allow_short=allow_short,
            trend_period=8,
            minimum_efficiency=threshold,
            reversal_confirmation_atr=confirmation,
        )
        for direction, allow_short in (("both", True), ("long", False))
        for threshold in (0.55, 0.65)
        for confirmation in (0.0, 0.25, 0.5, 0.75)
    ]
    profit_protection = [
        CandidateSpec(
            name=f"both_30m_atr_14_3.5_eff_8_0.55_profit_{activation:g}_{trailing:g}",
            instrument_id="soxl_perp",
            atr_period=14,
            atr_multiplier=3.5,
            bar_minutes=30,
            allow_short=True,
            trend_period=8,
            minimum_efficiency=0.55,
            reversal_confirmation_atr=0.25,
            profit_activation_atr=activation,
            profit_trailing_atr=trailing,
        )
        for activation, trailing in ((2.0, 0.5), (3.0, 1.0), (4.0, 1.5))
    ]
    return [*direction_and_confirmation, *profit_protection]


def multitimeframe_finalists_grid() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            name="balanced_30m_both",
            instrument_id="soxl_perp",
            atr_period=14,
            atr_multiplier=3.5,
            bar_minutes=30,
            allow_short=True,
            trend_period=8,
            minimum_efficiency=0.55,
        ),
        CandidateSpec(
            name="defensive_30m_both",
            instrument_id="soxl_perp",
            atr_period=14,
            atr_multiplier=3.5,
            bar_minutes=30,
            allow_short=True,
            trend_period=8,
            minimum_efficiency=0.65,
        ),
        CandidateSpec(
            name="defensive_30m_long",
            instrument_id="soxl_perp",
            atr_period=14,
            atr_multiplier=3.5,
            bar_minutes=30,
            allow_short=False,
            trend_period=8,
            minimum_efficiency=0.65,
        ),
        CandidateSpec(
            name="high_return_60m_both",
            instrument_id="soxl_perp",
            atr_period=32,
            atr_multiplier=2.0,
            bar_minutes=60,
            allow_short=True,
            trend_period=12,
            minimum_efficiency=0.15,
        ),
        CandidateSpec(
            name="current_15m_long",
            instrument_id="soxl_perp",
            atr_period=32,
            atr_multiplier=3.0,
            bar_minutes=15,
            allow_short=False,
            trend_period=8,
            minimum_efficiency=0.25,
        ),
    ]


def multitimeframe_top_three_grid() -> list[CandidateSpec]:
    selected_names = {
        "current_15m_long",
        "high_return_60m_both",
        "balanced_30m_both",
    }
    return [
        spec for spec in multitimeframe_finalists_grid() if spec.name in selected_names
    ]


def fifteen_minute_direction_grid() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            name=f"15m_{direction}_atr_32_3_eff_8_0.25",
            instrument_id="soxl_perp",
            atr_period=32,
            atr_multiplier=3.0,
            bar_minutes=15,
            allow_short=allow_short,
            trend_period=8,
            minimum_efficiency=0.25,
        )
        for direction, allow_short in (("both", True), ("long", False))
    ]


def fifteen_minute_both_atr_finalists_grid() -> list[CandidateSpec]:
    both_directions = [
        CandidateSpec(
            name=f"15m_both_atr_{period}_3_eff_8_0.25",
            instrument_id="soxl_perp",
            atr_period=period,
            atr_multiplier=3.0,
            bar_minutes=15,
            allow_short=True,
            trend_period=8,
            minimum_efficiency=0.25,
        )
        for period in (14, 21, 32)
    ]
    long_baseline = CandidateSpec(
        name="15m_long_atr_32_3_eff_8_0.25",
        instrument_id="soxl_perp",
        atr_period=32,
        atr_multiplier=3.0,
        bar_minutes=15,
        allow_short=False,
        trend_period=8,
        minimum_efficiency=0.25,
    )
    return [*both_directions, long_baseline]


def long_risk_budget_grid() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            name=f"long_15m_leverage_{leverage}_fraction_{position_fraction:g}",
            instrument_id="soxl_perp",
            atr_period=32,
            atr_multiplier=3.0,
            bar_minutes=15,
            allow_short=False,
            trend_period=8,
            minimum_efficiency=0.25,
            leverage=leverage,
            position_fraction=position_fraction,
        )
        for leverage in (1, 2, 3, 4)
        for position_fraction in (0.25, 0.5, 0.625, 0.75)
    ]


def long_risk_budget_refined_grid() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            name=f"long_15m_leverage_2_fraction_{position_fraction:g}",
            instrument_id="soxl_perp",
            atr_period=32,
            atr_multiplier=3.0,
            bar_minutes=15,
            allow_short=False,
            trend_period=8,
            minimum_efficiency=0.25,
            leverage=2,
            position_fraction=position_fraction,
        )
        for position_fraction in (0.625, 0.65, 0.675, 0.7, 0.725, 0.75)
    ]


def long_risk_budget_finalists_grid() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            name=f"long_15m_leverage_2_fraction_{position_fraction:g}",
            instrument_id="soxl_perp",
            atr_period=32,
            atr_multiplier=3.0,
            bar_minutes=15,
            allow_short=False,
            trend_period=8,
            minimum_efficiency=0.25,
            leverage=2,
            position_fraction=position_fraction,
        )
        for position_fraction in (0.5, 0.625, 0.7)
    ]


def refined_atr_grid() -> list[CandidateSpec]:
    both = [
        CandidateSpec(
            name=f"both_atr_{period}_{multiplier:g}",
            instrument_id="soxl_perp",
            atr_period=period,
            atr_multiplier=multiplier,
        )
        for period in (12, 14, 16, 18, 21, 24, 28)
        for multiplier in (2.75, 3.0, 3.25)
    ]
    long_only = [
        CandidateSpec(
            name=f"long_atr_{period}_{multiplier:g}",
            instrument_id="soxl_perp_long",
            atr_period=period,
            atr_multiplier=multiplier,
        )
        for period in (21, 24, 28, 32, 35)
        for multiplier in (2.5, 2.75, 3.0, 3.25, 3.5)
    ]
    return [*both, *long_only]


def trend_filter_grid() -> list[CandidateSpec]:
    filter_settings = [(8, 0.0)] + [
        (period, threshold)
        for period in (4, 8, 12, 16, 24, 32)
        for threshold in (0.15, 0.25, 0.35, 0.45, 0.55)
    ]
    bases = (
        ("both", "soxl_perp", 16, 3.0),
        ("long", "soxl_perp_long", 32, 3.0),
    )
    return [
        CandidateSpec(
            name=f"{direction}_atr_{atr_period}_3_eff_{trend_period}_{threshold:g}",
            instrument_id=instrument_id,
            atr_period=atr_period,
            atr_multiplier=atr_multiplier,
            trend_period=trend_period,
            minimum_efficiency=threshold,
        )
        for direction, instrument_id, atr_period, atr_multiplier in bases
        for trend_period, threshold in filter_settings
    ]


def profit_exit_grid() -> list[CandidateSpec]:
    bases = (
        ("both_fast", "soxl_perp", 16, 8, 0.25),
        ("both_selective", "soxl_perp", 16, 8, 0.35),
        ("both_defensive", "soxl_perp", 16, 12, 0.45),
        ("long_fast", "soxl_perp_long", 32, 8, 0.25),
        ("long_defensive", "soxl_perp_long", 32, 12, 0.45),
    )
    exits: tuple[tuple[float | None, float | None], ...] = (
        (None, None),
        *(
            (activation, trailing)
            for activation in (2.0, 3.0, 4.0)
            for trailing in (0.5, 1.0, 1.5, 2.0)
        ),
    )
    return [
        CandidateSpec(
            name=(
                f"{name}_no_profit_exit"
                if activation is None
                else f"{name}_profit_{activation:g}_{trailing:g}"
            ),
            instrument_id=instrument_id,
            atr_period=atr_period,
            atr_multiplier=3.0,
            trend_period=trend_period,
            minimum_efficiency=threshold,
            profit_activation_atr=activation,
            profit_trailing_atr=trailing,
        )
        for name, instrument_id, atr_period, trend_period, threshold in bases
        for activation, trailing in exits
    ]


def reversal_confirmation_grid() -> list[CandidateSpec]:
    bases = (
        ("both_fast", 8, 0.25),
        ("both_selective", 8, 0.35),
        ("both_defensive", 12, 0.45),
    )
    return [
        CandidateSpec(
            name=f"{name}_reversal_{confirmation:g}",
            instrument_id="soxl_perp",
            atr_period=16,
            atr_multiplier=3.0,
            trend_period=trend_period,
            minimum_efficiency=threshold,
            reversal_confirmation_atr=confirmation,
        )
        for name, trend_period, threshold in bases
        for confirmation in (0.0, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5)
    ]


def continuation_reentry_grid() -> list[CandidateSpec]:
    bases = (
        ("both_fast", "soxl_perp", 16, 8, 0.25, 0.5, None, None),
        ("both_defensive", "soxl_perp", 16, 12, 0.45, 1.0, None, None),
        ("long_fast", "soxl_perp_long", 32, 8, 0.25, 0.25, None, None),
        ("long_defensive", "soxl_perp_long", 32, 12, 0.45, 0.25, None, None),
        ("long_defensive_profit", "soxl_perp_long", 32, 12, 0.45, 0.25, 4.0, 1.5),
    )
    return [
        CandidateSpec(
            name=(
                f"{name}_no_reentry"
                if reentry is None
                else f"{name}_reentry_{reentry:g}"
            ),
            instrument_id=instrument_id,
            atr_period=atr_period,
            atr_multiplier=3.0,
            trend_period=trend_period,
            minimum_efficiency=threshold,
            reversal_confirmation_atr=confirmation,
            profit_activation_atr=activation,
            profit_trailing_atr=trailing,
            continuation_reentry_atr=reentry,
        )
        for (
            name,
            instrument_id,
            atr_period,
            trend_period,
            threshold,
            confirmation,
            activation,
            trailing,
        ) in bases
        for reentry in (None, 0.5, 1.0, 1.5, 2.0, 3.0)
    ]


def finalist_grid() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            name="both_fast_finalist",
            instrument_id="soxl_perp",
            atr_period=16,
            atr_multiplier=3.0,
            trend_period=8,
            minimum_efficiency=0.25,
            reversal_confirmation_atr=0.5,
        ),
        CandidateSpec(
            name="both_defensive_finalist",
            instrument_id="soxl_perp",
            atr_period=16,
            atr_multiplier=3.0,
            trend_period=12,
            minimum_efficiency=0.45,
            reversal_confirmation_atr=1.0,
        ),
        CandidateSpec(
            name="long_fast_finalist",
            instrument_id="soxl_perp_long",
            atr_period=32,
            atr_multiplier=3.0,
            trend_period=8,
            minimum_efficiency=0.25,
        ),
        CandidateSpec(
            name="long_defensive_finalist",
            instrument_id="soxl_perp_long",
            atr_period=32,
            atr_multiplier=3.0,
            trend_period=12,
            minimum_efficiency=0.45,
        ),
        CandidateSpec(
            name="long_defensive_profit_finalist",
            instrument_id="soxl_perp_long",
            atr_period=32,
            atr_multiplier=3.0,
            trend_period=12,
            minimum_efficiency=0.45,
            profit_activation_atr=4.0,
            profit_trailing_atr=1.5,
        ),
    ]


def selected_grid() -> list[CandidateSpec]:
    return [
        CandidateSpec(
            name="selected_long_atr_32_3",
            instrument_id="soxl_perp_long",
            atr_period=32,
            atr_multiplier=3.0,
            trend_period=8,
            minimum_efficiency=0.25,
        )
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--grid",
        choices=(
            "baseline",
            "timeframe-coarse",
            "timeframe-trend",
            "timeframe-trend-refined",
            "timeframe-atr-refined",
            "thirty-minute-trend-final",
            "thirty-minute-final-controls",
            "multitimeframe-finalists",
            "multitimeframe-top-three",
            "fifteen-minute-direction",
            "fifteen-minute-both-atr-finalists",
            "long-risk-budget",
            "long-risk-budget-refined",
            "long-risk-budget-finalists",
            "refined-atr",
            "trend-filter",
            "profit-exit",
            "reversal-confirmation",
            "continuation-reentry",
            "finalists",
            "selected",
        ),
        default="baseline",
    )
    parser.add_argument(
        "--splits",
        default="train,validation,holdout",
        help="Comma-separated time splits to evaluate",
    )
    parser.add_argument(
        "--common-start-bar-minutes",
        type=int,
        help="Use a common replay start after warmup at this bar duration",
    )
    args = parser.parse_args()
    settings = load_settings(args.config)
    with sqlite3.connect(settings.database_path) as connection:
        first_bar = int(
            connection.execute(
                """
                SELECT MIN(start_ms) FROM ohlcv_bars
                WHERE instrument_id = 'soxl_perp' AND interval_minutes = 15
                """
            ).fetchone()[0]
        )
        last_tick = int(
            connection.execute(
                "SELECT MAX(timestamp_ms) FROM agg_trades WHERE instrument_id = 'soxl_perp'"
            ).fetchone()[0]
        )
    common_start_bar_minutes = (
        args.common_start_bar_minutes or settings.strategy.bar_minutes
    )
    first_replay = first_bar + settings.warmup_bars * common_start_bar_minutes * 60_000
    periods = {
        "full": (first_replay, last_tick),
        "may": (
            first_replay,
            int(datetime(2026, 6, 1, tzinfo=UTC).timestamp() * 1000) - 1,
        ),
        "june": (
            int(datetime(2026, 6, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000) - 1,
        ),
        "july": (
            int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2026, 7, 31, tzinfo=UTC).timestamp() * 1000) - 1,
        ),
        "july_calendar": (
            int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2026, 8, 1, tzinfo=UTC).timestamp() * 1000) - 1,
        ),
        "august": (
            int(datetime(2026, 8, 1, tzinfo=UTC).timestamp() * 1000),
            last_tick,
        ),
        "train": (
            first_replay,
            int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000) - 1,
        ),
        "validation": (
            int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2026, 7, 31, tzinfo=UTC).timestamp() * 1000) - 1,
        ),
        "holdout": (
            int(datetime(2026, 7, 31, tzinfo=UTC).timestamp() * 1000),
            last_tick,
        ),
    }
    requested_splits = tuple(value.strip() for value in args.splits.split(",") if value.strip())
    unknown_splits = set(requested_splits) - set(periods)
    if not requested_splits or unknown_splits:
        unknown = ", ".join(sorted(unknown_splits)) or "none selected"
        raise ValueError(f"invalid splits: {unknown}")
    grid_builders = {
        "baseline": baseline_grid,
        "timeframe-coarse": timeframe_coarse_grid,
        "timeframe-trend": timeframe_trend_grid,
        "timeframe-trend-refined": timeframe_trend_refined_grid,
        "timeframe-atr-refined": timeframe_atr_refined_grid,
        "thirty-minute-trend-final": thirty_minute_trend_final_grid,
        "thirty-minute-final-controls": thirty_minute_final_controls_grid,
        "multitimeframe-finalists": multitimeframe_finalists_grid,
        "multitimeframe-top-three": multitimeframe_top_three_grid,
        "fifteen-minute-direction": fifteen_minute_direction_grid,
        "fifteen-minute-both-atr-finalists": fifteen_minute_both_atr_finalists_grid,
        "long-risk-budget": long_risk_budget_grid,
        "long-risk-budget-refined": long_risk_budget_refined_grid,
        "long-risk-budget-finalists": long_risk_budget_finalists_grid,
        "refined-atr": refined_atr_grid,
        "trend-filter": trend_filter_grid,
        "profit-exit": profit_exit_grid,
        "reversal-confirmation": reversal_confirmation_grid,
        "continuation-reentry": continuation_reentry_grid,
        "finalists": finalist_grid,
        "selected": selected_grid,
    }
    specs = grid_builders[args.grid]()
    tasks = [
        EvaluationTask(args.config, spec, period_name, start_ms, end_ms)
        for period_name in requested_splits
        for start_ms, end_ms in (periods[period_name],)
        for spec in specs
    ]
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(evaluate, task): task for task in tasks}
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            value = future.result()
            results.append(value)
            result = value["result"]
            print(
                f"[{index}/{len(tasks)}] {task.period_name} {task.spec.name}: "
                f"{result['net_return']:.2%}, DD {result['max_drawdown']:.2%}",
                flush=True,
            )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "grid": args.grid,
        "common_start_bar_minutes": common_start_bar_minutes,
        "periods": periods,
        "evaluated_splits": requested_splits,
        "results": results,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
