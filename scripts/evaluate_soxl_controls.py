#!/usr/bin/env python3
"""Compare frozen SOXL control extensions and record continuous monthly equity."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.backtest import (
    ReplayATRTickStrategy,
    ReplayBroker,
    ReplayCandidate,
    ReplayParameters,
    _candidate_result,
    _default_replay_start,
    _load_funding_rates,
    _load_warmup_bars,
)
from mastermind_tick.config import InstrumentSettings, Settings, load_settings
from mastermind_tick.models import Tick


@dataclass(frozen=True)
class ControlSpec:
    name: str
    profit_activation_atr: float | None = None
    profit_trailing_atr: float | None = None
    continuation_reentry_atr: float | None = None


CONTROL_SPECS = (
    ControlSpec("baseline"),
    ControlSpec("profit_2_0.5", 2.0, 0.5),
    ControlSpec("profit_4_1.5", 4.0, 1.5),
    ControlSpec("reentry_0.5", continuation_reentry_atr=0.5),
    ControlSpec("reentry_1.4", continuation_reentry_atr=1.4),
    ControlSpec("reentry_2.0", continuation_reentry_atr=2.0),
    ControlSpec("profit_2_0.5_reentry_1.4", 2.0, 0.5, 1.4),
    ControlSpec("profit_4_1.5_reentry_1.4", 4.0, 1.5, 1.4),
)


def _tick_from_row(row: sqlite3.Row) -> Tick:
    return Tick(
        event_id=row["event_id"],
        timestamp_ms=int(row["timestamp_ms"]),
        price=Decimal(row["price"]),
        quantity=Decimal(row["quantity"]),
        source=row["source"],
        first_trade_id=row["first_trade_id"],
        last_trade_id=row["last_trade_id"],
        open_price=Decimal(row["open_price"]) if row["open_price"] is not None else None,
        high_price=Decimal(row["high_price"]) if row["high_price"] is not None else None,
        low_price=Decimal(row["low_price"]) if row["low_price"] is not None else None,
    )


def _candidate(
    settings: Settings,
    instrument: InstrumentSettings,
    spec: ControlSpec,
    warmup_bars: list[Any],
) -> ReplayCandidate:
    strategy = ReplayATRTickStrategy(
        settings.strategy.atr_period,
        settings.strategy.atr_multiplier,
        settings.strategy.bar_minutes,
        settings.strategy.trend_efficiency_period,
        settings.strategy.minimum_trend_efficiency,
        settings.strategy.reversal_confirmation_atr,
    )
    strategy.bootstrap(warmup_bars)
    position_fraction = Decimal(
        str(instrument.position_fraction or settings.strategy.position_fraction)
    )
    fee_bps = Decimal(str(instrument.fee_bps or settings.execution.fee_bps))
    slippage_bps = Decimal(str(instrument.slippage_bps or settings.execution.slippage_bps))
    minimum_notional = Decimal(
        str(instrument.minimum_notional or settings.execution.minimum_notional)
    )
    return ReplayCandidate(
        parameters=ReplayParameters(
            settings.strategy.atr_period,
            settings.strategy.atr_multiplier,
            variant=spec.name,
            profit_activation_atr=spec.profit_activation_atr,
            profit_trailing_atr=spec.profit_trailing_atr,
            continuation_reentry_atr=spec.continuation_reentry_atr,
        ),
        strategy=strategy,
        broker=ReplayBroker(
            instrument,
            Decimal(str(settings.initial_cash)),
            position_fraction,
            fee_bps,
            slippage_bps,
            minimum_notional,
        ),
    )


def _month(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).strftime("%Y-%m")


def _position_label(candidate: ReplayCandidate) -> str:
    if candidate.broker.quantity > 0:
        return "LONG"
    if candidate.broker.quantity < 0:
        return "SHORT"
    return "FLAT"


def _trade_stats(trades: list[Any]) -> dict[str, Any]:
    wins = [trade for trade in trades if trade.net_pnl > 0]
    gross_profit = sum((trade.net_pnl for trade in wins), Decimal("0"))
    gross_loss = -sum(
        (trade.net_pnl for trade in trades if trade.net_pnl < 0),
        Decimal("0"),
    )
    return {
        "completed_trades": len(trades),
        "winning_trades": len(wins),
        "win_rate": len(wins) / len(trades) if trades else None,
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else None,
        "net_pnl": float(sum((trade.net_pnl for trade in trades), Decimal("0"))),
    }


def _month_row(
    candidate: ReplayCandidate,
    state: dict[str, Any],
    month: str,
    end_timestamp_ms: int,
) -> dict[str, Any]:
    end_equity = state["last_equity"]
    start_equity = state["start_equity"]
    trades = candidate.broker.trades[state["trade_start_index"] :]
    row = {
        "month": month,
        "start_ms": state["start_ms"],
        "end_ms": end_timestamp_ms,
        "start_equity": float(start_equity),
        "end_equity": float(end_equity),
        "net_return": float(end_equity / start_equity - Decimal("1")) if start_equity else None,
        "max_drawdown": float(state["max_drawdown"]),
        "ending_position": _position_label(candidate),
        "signals": candidate.signals - state["signals_start"],
        "profit_exit_signals": candidate.profit_exit_signals - state["profit_exits_start"],
        "continuation_reentry_signals": (
            candidate.continuation_reentry_signals - state["reentries_start"]
        ),
    }
    row.update(_trade_stats(trades))
    return row


def _new_month_state(
    candidate: ReplayCandidate, equity: Decimal, timestamp_ms: int
) -> dict[str, Any]:
    return {
        "start_ms": timestamp_ms,
        "start_equity": equity,
        "last_equity": equity,
        "peak_equity": equity,
        "max_drawdown": Decimal("0"),
        "trade_start_index": len(candidate.broker.trades),
        "signals_start": candidate.signals,
        "profit_exits_start": candidate.profit_exit_signals,
        "reentries_start": candidate.continuation_reentry_signals,
    }


def _run_range(
    settings: Settings,
    instrument: InstrumentSettings,
    specs: tuple[ControlSpec, ...],
    start_ms: int,
    end_ms: int,
    *,
    record_months: bool,
) -> dict[str, Any]:
    database_uri = f"file:{settings.database_path}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        warmup_bars = _load_warmup_bars(
            connection,
            instrument.market_id,
            start_ms,
            settings.warmup_bars,
        )
        if len(warmup_bars) < max(
            settings.strategy.atr_period, settings.strategy.trend_efficiency_period + 1
        ):
            raise ValueError("insufficient warmup bars")
        funding_rates = _load_funding_rates(connection, instrument.market_id, start_ms, end_ms)
        candidates = {
            spec.name: _candidate(settings, instrument, spec, warmup_bars) for spec in specs
        }
        month_states: dict[str, dict[str, Any]] = {}
        monthly: dict[str, list[dict[str, Any]]] = {spec.name: [] for spec in specs}
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
            (instrument.market_id, start_ms, end_ms),
        )
        for row in rows:
            tick = _tick_from_row(row)
            tick_month = _month(tick.timestamp_ms)
            for spec in specs:
                candidate = candidates[spec.name]
                if record_months:
                    state = month_states.get(spec.name)
                    if state is None:
                        state = _new_month_state(
                            candidate,
                            candidate.broker.initial_cash,
                            tick.timestamp_ms,
                        )
                        month_states[spec.name] = state
                    elif state["month"] != tick_month:
                        monthly[spec.name].append(
                            _month_row(
                                candidate,
                                state,
                                state["month"],
                                state["last_ms"],
                            )
                        )
                        state = _new_month_state(
                            candidate,
                            state["last_equity"],
                            tick.timestamp_ms,
                        )
                        month_states[spec.name] = state
                    state["month"] = tick_month

                candidate.process_tick(tick, funding_rates)
                if record_months:
                    equity = candidate.broker.equity(tick.price)
                    state["last_equity"] = equity
                    state["last_ms"] = tick.timestamp_ms
                    state["peak_equity"] = max(state["peak_equity"], equity)
                    state["max_drawdown"] = min(
                        state["max_drawdown"],
                        equity / state["peak_equity"] - Decimal("1"),
                    )
            last_price = tick.price
            tick_count += 1
            raw_trade_count += (
                int(row["last_trade_id"]) - int(row["first_trade_id"]) + 1
                if row["first_trade_id"] is not None and row["last_trade_id"] is not None
                else 1
            )

    if last_price is None:
        raise ValueError("no ticks in replay range")
    result_payload: dict[str, Any] = {
        "range": {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
            "tick_count": tick_count,
            "raw_trade_count": raw_trade_count,
            "warmup_bars": len(warmup_bars),
            "funding_events": len(funding_rates),
        },
        "results": {},
    }
    for spec in specs:
        candidate = candidates[spec.name]
        result_payload["results"][spec.name] = asdict(
            _candidate_result(
                candidate,
                instrument,
                start_ms,
                end_ms,
                tick_count,
                raw_trade_count,
                len(warmup_bars),
                last_price,
            )
        )
        if record_months:
            state = month_states.get(spec.name)
            if state is not None:
                monthly[spec.name].append(
                    _month_row(candidate, state, state["month"], state["last_ms"])
                )
    if record_months:
        result_payload["monthly_continuous"] = monthly
    return result_payload


def _available_range(settings: Settings, instrument: InstrumentSettings) -> tuple[int, int, int]:
    database_uri = f"file:{settings.database_path}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        first = _default_replay_start(
            connection,
            instrument.market_id,
            settings.strategy.bar_minutes,
            settings.warmup_bars,
        )
        row = connection.execute(
            "SELECT MAX(timestamp_ms) AS last_ms FROM agg_trades WHERE instrument_id = ?",
            (instrument.market_id,),
        ).fetchone()
        if row["last_ms"] is None:
            raise ValueError("no market data")
        return first, int(row["last_ms"]), int(row["last_ms"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--instrument", default="soxl_perp")
    parser.add_argument("--output", required=True)
    parser.add_argument("--tail-start-ms", type=int, default=1786108395532)
    parser.add_argument(
        "--periods",
        default="full,august_independent,holdout_independent,new_tail_independent",
        help="Comma-separated period names to run",
    )
    parser.add_argument(
        "--controls",
        default=",".join(spec.name for spec in CONTROL_SPECS),
        help="Comma-separated control names to run",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    instrument = next(item for item in settings.instruments if item.id == args.instrument)
    start_ms, end_ms, _ = _available_range(settings, instrument)
    selected_controls = tuple(name.strip() for name in args.controls.split(",") if name.strip())
    spec_by_name = {spec.name: spec for spec in CONTROL_SPECS}
    unknown_controls = set(selected_controls) - set(spec_by_name)
    if not selected_controls or unknown_controls:
        raise ValueError(f"unknown or empty controls: {', '.join(sorted(unknown_controls))}")
    specs = tuple(spec_by_name[name] for name in selected_controls)
    all_periods = {
        "full": (start_ms, end_ms),
        "train_independent": (
            start_ms,
            int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000) - 1,
        ),
        "validation_independent": (
            int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2026, 7, 31, tzinfo=UTC).timestamp() * 1000) - 1,
        ),
        "august_independent": (
            int(datetime(2026, 8, 1, tzinfo=UTC).timestamp() * 1000),
            end_ms,
        ),
        "holdout_independent": (
            int(datetime(2026, 7, 31, tzinfo=UTC).timestamp() * 1000),
            min(end_ms, args.tail_start_ms - 1),
        ),
        "new_tail_independent": (min(end_ms, args.tail_start_ms), end_ms),
    }
    selected_periods = tuple(name.strip() for name in args.periods.split(",") if name.strip())
    unknown_periods = set(selected_periods) - set(all_periods)
    if not selected_periods or unknown_periods:
        raise ValueError(f"unknown or empty periods: {', '.join(sorted(unknown_periods))}")
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "controls": [asdict(spec) for spec in specs],
        "periods": {},
    }
    for name in selected_periods:
        period_start, period_end = all_periods[name]
        if period_start >= period_end:
            continue
        print(f"Replaying {name}...", flush=True)
        payload["periods"][name] = _run_range(
            settings,
            instrument,
            specs,
            period_start,
            period_end,
            record_months=name == "full",
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
