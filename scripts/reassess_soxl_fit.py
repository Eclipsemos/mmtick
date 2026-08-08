#!/usr/bin/env python3
"""Produce frozen-parameter fit diagnostics for the current SOXLUSDT strategy."""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
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
from mastermind_tick.models import Bar, Side, StrategySignal, Tick


@dataclass(frozen=True)
class CostScenario:
    name: str
    fee_bps: Decimal
    slippage_bps: Decimal
    position_fraction: Decimal = Decimal("0.625")


@dataclass(frozen=True)
class EntryContext:
    signal_at_ms: int
    signal_reason: str
    efficiency: Decimal | None


class DiagnosticReplayStrategy(ReplayATRTickStrategy):
    """Capture signal and cross decisions without changing strategy behavior."""

    def __init__(
        self,
        period: int,
        multiplier: float,
        bar_minutes: int,
        trend_efficiency_period: int,
        minimum_trend_efficiency: float,
        reversal_confirmation_atr: float,
    ) -> None:
        super().__init__(
            period,
            multiplier,
            bar_minutes,
            trend_efficiency_period,
            minimum_trend_efficiency,
            reversal_confirmation_atr,
        )
        self.cross_decisions: list[dict[str, Any]] = []
        self.entry_contexts: list[EntryContext] = []
        self._emitting_reason: str | None = None

    def _emit_signal(
        self,
        tick: Tick,
        atr: Decimal,
        bar_start: int,
        side: Side,
        reason: str,
        *,
        reduce_only: bool,
        reversal_after: str | None = None,
    ) -> StrategySignal:
        self._emitting_reason = reason
        try:
            signal = super()._emit_signal(
                tick,
                atr,
                bar_start,
                side,
                reason,
                reduce_only=reduce_only,
                reversal_after=reversal_after,
            )
        finally:
            self._emitting_reason = None
        if side is Side.BUY and not reduce_only:
            self.entry_contexts.append(
                EntryContext(
                    signal_at_ms=tick.timestamp_ms,
                    signal_reason=reason,
                    efficiency=self.last_trend_efficiency,
                )
            )
        return signal

    def _record_cross(
        self,
        direction: str,
        timestamp_ms: int,
        result: str,
        reason: str | None,
    ) -> None:
        self.cross_decisions.append(
            {
                "direction": direction,
                "timestamp_ms": timestamp_ms,
                "result": result,
                "reason": reason,
                "signal_reason": self._emitting_reason,
                "trend_efficiency": self.last_trend_efficiency,
            }
        )
        super()._record_cross(direction, timestamp_ms, result, reason)


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


def _load_bars(
    connection: sqlite3.Connection,
    instrument_id: str,
    end_ms: int,
) -> list[Bar]:
    rows = connection.execute(
        """
        SELECT start_ms, end_ms, open, high, low, close, volume, trade_count
        FROM ohlcv_bars
        WHERE instrument_id = ? AND interval_minutes = 15
          AND is_closed = 1 AND start_ms <= ?
        ORDER BY start_ms
        """,
        (instrument_id, end_ms),
    )
    return [
        Bar(
            start_ms=int(row["start_ms"]),
            end_ms=int(row["end_ms"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
            trade_count=int(row["trade_count"]),
        )
        for row in rows
    ]


def _build_candidate(
    settings: Settings,
    instrument: InstrumentSettings,
    scenario: CostScenario,
    warmup_bars: list[Bar],
) -> ReplayCandidate:
    scenario_instrument = replace(
        instrument,
        fee_bps=float(scenario.fee_bps),
        slippage_bps=float(scenario.slippage_bps),
        position_fraction=float(scenario.position_fraction),
    )
    strategy = DiagnosticReplayStrategy(
        settings.strategy.atr_period,
        settings.strategy.atr_multiplier,
        settings.strategy.bar_minutes,
        settings.strategy.trend_efficiency_period,
        settings.strategy.minimum_trend_efficiency,
        settings.strategy.reversal_confirmation_atr,
    )
    strategy.bootstrap(warmup_bars)
    return ReplayCandidate(
        parameters=ReplayParameters(
            settings.strategy.atr_period,
            settings.strategy.atr_multiplier,
            variant=scenario.name,
        ),
        strategy=strategy,
        broker=ReplayBroker(
            scenario_instrument,
            Decimal(str(settings.initial_cash)),
            scenario.position_fraction,
            scenario.fee_bps,
            scenario.slippage_bps,
            Decimal(str(instrument.minimum_notional)),
        ),
    )


def _percentile(values: list[Decimal], fraction: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int((Decimal(len(ordered) - 1) * fraction).to_integral_value())
    return ordered[index]


def _hours(milliseconds: int) -> Decimal:
    return Decimal(milliseconds) / Decimal(3_600_000)


def _trade_rows(candidate: ReplayCandidate) -> list[dict[str, Any]]:
    strategy = candidate.strategy
    if not isinstance(strategy, DiagnosticReplayStrategy):
        raise TypeError("diagnostic strategy required")

    equity_before = candidate.broker.initial_cash
    rows: list[dict[str, Any]] = []
    context_index = 0
    for trade in candidate.broker.trades:
        context: EntryContext | None = None
        while (
            context_index < len(strategy.entry_contexts)
            and strategy.entry_contexts[context_index].signal_at_ms <= trade.entry_at_ms
        ):
            context = strategy.entry_contexts[context_index]
            context_index += 1
        if context is None:
            raise ValueError(f"no entry context for completed trade at {trade.entry_at_ms}")
        account_return = trade.net_pnl / equity_before
        rows.append(
            {
                "trade": trade,
                "context": context,
                "equity_before": equity_before,
                "account_return": account_return,
            }
        )
        equity_before += trade.net_pnl
    return rows


def _aggregate_trade_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trades": 0,
            "wins": 0,
            "win_rate": None,
            "profit_factor": None,
            "net_pnl": 0.0,
            "average_account_return": None,
            "median_account_return": None,
            "average_holding_hours": None,
        }
    wins = [row for row in rows if row["trade"].net_pnl > 0]
    gross_profit = sum((row["trade"].net_pnl for row in wins), Decimal("0"))
    gross_loss = -sum(
        (row["trade"].net_pnl for row in rows if row["trade"].net_pnl < 0),
        Decimal("0"),
    )
    account_returns = [row["account_return"] for row in rows]
    holding_hours = [_hours(row["trade"].exit_at_ms - row["trade"].entry_at_ms) for row in rows]
    return {
        "trades": len(rows),
        "wins": len(wins),
        "win_rate": len(wins) / len(rows),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else None,
        "net_pnl": float(sum((row["trade"].net_pnl for row in rows), Decimal("0"))),
        "average_account_return": float(sum(account_returns, Decimal("0")) / len(rows)),
        "median_account_return": float(median(account_returns)),
        "average_holding_hours": float(sum(holding_hours, Decimal("0")) / len(rows)),
    }


def _trend_regime(value: Decimal) -> str:
    if value < Decimal("-0.05"):
        return "down_below_-5pct"
    if value > Decimal("0.05"):
        return "up_above_+5pct"
    return "sideways_-5pct_to_+5pct"


def _volatility_regime(value: Decimal) -> str:
    if value < Decimal("0.10"):
        return "low_below_10pct"
    if value < Decimal("0.20"):
        return "medium_10pct_to_20pct"
    return "high_20pct_or_more"


def _efficiency_regime(value: Decimal | None) -> str:
    if value is None:
        return "unavailable"
    if value < Decimal("0.40"):
        return "0.25_to_0.40"
    if value < Decimal("0.60"):
        return "0.40_to_0.60"
    return "0.60_to_1.00"


def _trade_regimes(
    rows: list[dict[str, Any]],
    bars: list[Bar],
) -> dict[str, Any]:
    end_times = [bar.end_ms for bar in bars]
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        "trailing_24h_trend": defaultdict(list),
        "trailing_24h_range": defaultdict(list),
        "entry_efficiency": defaultdict(list),
    }
    for row in rows:
        trade = row["trade"]
        grouped["entry_efficiency"][_efficiency_regime(row["context"].efficiency)].append(row)
        completed_count = bisect.bisect_left(end_times, trade.entry_at_ms)
        if completed_count < 96:
            continue
        window = bars[completed_count - 96 : completed_count]
        trailing_return = window[-1].close / window[0].open - Decimal("1")
        trailing_range = max(bar.high for bar in window) / min(bar.low for bar in window) - Decimal(
            "1"
        )
        grouped["trailing_24h_trend"][_trend_regime(trailing_return)].append(row)
        grouped["trailing_24h_range"][_volatility_regime(trailing_range)].append(row)
    return {
        group_name: {
            bucket: _aggregate_trade_group(bucket_rows)
            for bucket, bucket_rows in sorted(buckets.items())
        }
        for group_name, buckets in grouped.items()
    }


def _holding_and_concentration(
    candidate: ReplayCandidate,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    trades = candidate.broker.trades
    open_trade = candidate.broker.open_trade
    holding_hours = [_hours(trade.exit_at_ms - trade.entry_at_ms) for trade in trades]
    winners = sorted(
        (trade.net_pnl for trade in trades if trade.net_pnl > 0),
        reverse=True,
    )
    gross_profit = sum(winners, Decimal("0"))
    net_profit = sum((trade.net_pnl for trade in trades), Decimal("0"))
    closed_holding_ms = sum((trade.exit_at_ms - trade.entry_at_ms for trade in trades), 0)
    open_holding_ms = end_ms - open_trade.entry_at_ms if open_trade is not None else 0
    total_holding_ms = closed_holding_ms + open_holding_ms

    def contribution(count: int, denominator: Decimal) -> float | None:
        return float(sum(winners[:count], Decimal("0")) / denominator) if denominator > 0 else None

    if not trades:
        return {
            "exposure_fraction": total_holding_ms / (end_ms - start_ms),
            "total_holding_hours": float(_hours(total_holding_ms)),
            "holding_hours": {
                "mean": None,
                "median": None,
                "p25": None,
                "p75": None,
                "maximum": None,
            },
            "top_winner_share_of_gross_profit": {
                "top_1": None,
                "top_3": None,
                "top_5": None,
                "top_10": None,
            },
            "top_winner_share_of_net_profit": {
                "top_1": None,
                "top_3": None,
                "top_5": None,
                "top_10": None,
            },
        }

    return {
        "exposure_fraction": total_holding_ms / (end_ms - start_ms),
        "total_holding_hours": float(_hours(total_holding_ms)),
        "holding_hours": {
            "mean": float(sum(holding_hours, Decimal("0")) / len(holding_hours)),
            "median": float(median(holding_hours)),
            "p25": float(_percentile(holding_hours, Decimal("0.25")) or 0),
            "p75": float(_percentile(holding_hours, Decimal("0.75")) or 0),
            "maximum": float(max(holding_hours)),
        },
        "top_winner_share_of_gross_profit": {
            "top_1": contribution(1, gross_profit),
            "top_3": contribution(3, gross_profit),
            "top_5": contribution(5, gross_profit),
            "top_10": contribution(10, gross_profit),
        },
        "top_winner_share_of_net_profit": {
            "top_1": contribution(1, net_profit),
            "top_3": contribution(3, net_profit),
            "top_5": contribution(5, net_profit),
            "top_10": contribution(10, net_profit),
        },
    }


def _filter_diagnostics(strategy: DiagnosticReplayStrategy) -> dict[str, Any]:
    upward = [item for item in strategy.cross_decisions if item["direction"] == "UP"]
    accepted_crosses = [
        item
        for item in upward
        if item["result"] == "BUY_SIGNAL"
        and item["signal_reason"] == "price_crossed_above_atr_stop"
    ]
    blocked_low_efficiency = [
        item
        for item in upward
        if item["result"] == "BLOCKED" and item["reason"] == "LOW_TREND_EFFICIENCY"
    ]
    decisions = len(accepted_crosses) + len(blocked_low_efficiency)
    reasons = Counter(str(item["reason"] or item["result"]) for item in upward)
    startup_entries = sum(item["signal_reason"] == "startup_trend_alignment" for item in upward)
    return {
        "upward_cross_records": len(upward),
        "accepted_crossover_entries": len(accepted_crosses),
        "blocked_low_efficiency": len(blocked_low_efficiency),
        "filter_decision_pass_rate": len(accepted_crosses) / decisions if decisions else None,
        "startup_alignment_entries": startup_entries,
        "all_upward_outcomes": dict(sorted(reasons.items())),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--instrument", default="soxl_perp")
    parser.add_argument("--start-ms", type=int)
    parser.add_argument("--end-ms", type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    settings = load_settings(args.config)
    instrument = next(item for item in settings.instruments if item.id == args.instrument)
    database_uri = f"file:{settings.database_path}?mode=ro"
    scenarios = (
        CostScenario("no_cost", Decimal("0"), Decimal("0")),
        CostScenario("base_5_fee_2_slippage", Decimal("5"), Decimal("2")),
        CostScenario("double_10_fee_4_slippage", Decimal("10"), Decimal("4")),
        CostScenario("severe_15_fee_8_slippage", Decimal("15"), Decimal("8")),
        CostScenario("extreme_20_fee_10_slippage", Decimal("20"), Decimal("10")),
        CostScenario(
            "recommended_budget_1.40x",
            Decimal("5"),
            Decimal("2"),
            Decimal("0.70"),
        ),
    )

    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        market_id = instrument.market_id
        available = connection.execute(
            """
            SELECT MIN(timestamp_ms) AS first_ms, MAX(timestamp_ms) AS last_ms
            FROM agg_trades WHERE instrument_id = ?
            """,
            (market_id,),
        ).fetchone()
        if available is None or available["first_ms"] is None:
            raise ValueError(f"no aggTrade data for {instrument.id}")
        default_start = _default_replay_start(
            connection,
            market_id,
            settings.strategy.bar_minutes,
            settings.warmup_bars,
        )
        start_ms = max(int(available["first_ms"]), args.start_ms or default_start)
        end_ms = min(int(available["last_ms"]), args.end_ms or int(available["last_ms"]))
        if start_ms >= end_ms:
            raise ValueError("invalid replay range")
        warmup_bars = _load_warmup_bars(
            connection,
            market_id,
            start_ms,
            settings.warmup_bars,
        )
        funding_rates = _load_funding_rates(connection, market_id, start_ms, end_ms)
        bars = _load_bars(connection, market_id, end_ms)
        candidates = {
            scenario.name: _build_candidate(settings, instrument, scenario, warmup_bars)
            for scenario in scenarios
        }

        tick_count = 0
        raw_trade_count = 0
        first_price: Decimal | None = None
        last_price: Decimal | None = None
        benchmark_peak: Decimal | None = None
        benchmark_max_drawdown = Decimal("0")
        rows = connection.execute(
            """
            SELECT event_id, timestamp_ms, price, open_price, high_price, low_price,
                   quantity, source, first_trade_id, last_trade_id
            FROM agg_trades
            WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
            ORDER BY timestamp_ms, received_at_ms, event_id
            """,
            (market_id, start_ms, end_ms),
        )
        for row in rows:
            tick = _tick_from_row(row)
            for candidate in candidates.values():
                candidate.process_tick(tick, funding_rates)
            if first_price is None:
                first_price = tick.price
                benchmark_peak = tick.price
            benchmark_peak = max(benchmark_peak or tick.price, tick.price)
            benchmark_max_drawdown = min(
                benchmark_max_drawdown,
                tick.price / benchmark_peak - Decimal("1"),
            )
            last_price = tick.price
            tick_count += 1
            raw_trade_count += (
                int(row["last_trade_id"]) - int(row["first_trade_id"]) + 1
                if row["first_trade_id"] is not None and row["last_trade_id"] is not None
                else 1
            )

    if first_price is None or last_price is None:
        raise ValueError("no ticks in replay range")
    scenario_results = {}
    for scenario in scenarios:
        candidate = candidates[scenario.name]
        scenario_instrument = replace(
            instrument,
            position_fraction=float(scenario.position_fraction),
            fee_bps=float(scenario.fee_bps),
            slippage_bps=float(scenario.slippage_bps),
        )
        scenario_results[scenario.name] = asdict(
            _candidate_result(
                candidate,
                scenario_instrument,
                start_ms,
                end_ms,
                tick_count,
                raw_trade_count,
                len(warmup_bars),
                last_price,
            )
        )

    base = candidates["base_5_fee_2_slippage"]
    base_strategy = base.strategy
    if not isinstance(base_strategy, DiagnosticReplayStrategy):
        raise TypeError("diagnostic strategy required")
    trade_rows = _trade_rows(base)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "range": {
            "start_ms": start_ms,
            "start": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
            "end_ms": end_ms,
            "end": datetime.fromtimestamp(end_ms / 1000, UTC).isoformat(),
            "tick_count": tick_count,
            "raw_trade_count": raw_trade_count,
            "warmup_bars": len(warmup_bars),
            "funding_events": len(funding_rates),
        },
        "frozen_strategy": {
            "direction": "long_only",
            "bar_minutes": settings.strategy.bar_minutes,
            "atr_period": settings.strategy.atr_period,
            "atr_multiplier": settings.strategy.atr_multiplier,
            "trend_efficiency_period": settings.strategy.trend_efficiency_period,
            "minimum_trend_efficiency": settings.strategy.minimum_trend_efficiency,
            "profit_protection": "disabled",
            "continuation_reentry": "disabled",
            "leverage": instrument.leverage,
            "position_fraction": instrument.position_fraction,
            "target_exposure": float(
                Decimal(instrument.leverage) * Decimal(str(instrument.position_fraction))
            ),
        },
        "buy_and_hold_1x": {
            "start_price": float(first_price),
            "end_price": float(last_price),
            "return": float(last_price / first_price - Decimal("1")),
            "max_drawdown": float(benchmark_max_drawdown),
        },
        "scenarios": scenario_results,
        "holding_and_concentration": _holding_and_concentration(base, start_ms, end_ms),
        "trend_filter": _filter_diagnostics(base_strategy),
        "trade_regimes": _trade_regimes(trade_rows, bars),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n"
    )
    print(output)


if __name__ == "__main__":
    main()
