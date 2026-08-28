"""Tick-level ATR parameter replay against the persisted market warehouse."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.config import InstrumentSettings, Settings, load_settings
from mastermind_tick.models import Bar, FundingRate, Side, StrategySignal, Tick
from mastermind_tick.strategy import (
    ATRProfitProtection,
    ATRTickStrategy,
    true_range,
    wilder_atr,
)

DEFAULT_PERIODS = (5, 7, 10, 14, 21, 28, 35, 42, 56)
DEFAULT_MULTIPLIERS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)


@dataclass(frozen=True)
class ReplayParameters:
    atr_period: int
    atr_multiplier: float
    variant: str = "baseline"
    fixed_take_profit_atr: float | None = None
    profit_activation_atr: float | None = None
    profit_trailing_atr: float | None = None
    continuation_reentry_atr: float | None = None


@dataclass
class ReplayTrade:
    direction: str
    entry_at_ms: int
    exit_at_ms: int
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal


@dataclass
class OpenReplayTrade:
    direction: str
    entry_at_ms: int
    entry_price: Decimal
    quantity: Decimal
    entry_fee: Decimal
    funding: Decimal = Decimal("0")


@dataclass
class ReplayResult:
    instrument_id: str
    symbol: str
    paper_model: str
    atr_period: int
    atr_multiplier: float
    variant: str
    fixed_take_profit_atr: float | None
    profit_activation_atr: float | None
    profit_trailing_atr: float | None
    continuation_reentry_atr: float | None
    start_ms: int
    end_ms: int
    tick_count: int
    raw_trade_count: int
    warmup_bars: int
    initial_equity: float
    final_equity: float
    net_profit: float
    net_return: float
    completed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float | None
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    max_drawdown: float
    total_fees: float
    total_funding: float
    signals: int
    profit_exit_signals: int
    continuation_reentry_signals: int
    ending_position: str
    daily_equity: list[dict[str, Any]] = field(default_factory=list)


class ReplayATRTickStrategy(ATRTickStrategy):
    """Equivalent ATR calculation that avoids replaying all prior bars per Tick."""

    def __init__(
        self,
        period: int,
        multiplier: float,
        bar_minutes: int,
        trend_efficiency_period: int = 8,
        minimum_trend_efficiency: float = 0.25,
        reversal_confirmation_atr: float = 0.25,
    ):
        super().__init__(
            period,
            multiplier,
            bar_minutes,
            trend_efficiency_period,
            minimum_trend_efficiency,
            reversal_confirmation_atr,
        )
        self._closed_signature: tuple[int, int | None] | None = None
        self._closed_atr: Decimal | None = None

    def _current_atr(self) -> Decimal | None:
        if self.current_bar is None:
            return None
        latest_start = self.completed_bars[-1].start_ms if self.completed_bars else None
        signature = (len(self.completed_bars), latest_start)
        if signature != self._closed_signature:
            self._closed_signature = signature
            self._closed_atr = wilder_atr(self.completed_bars, self.period)
        if self._closed_atr is None or len(self.completed_bars) < self.period:
            return wilder_atr([*self.completed_bars, self.current_bar], self.period)
        current_range = true_range(self.current_bar, self.completed_bars[-1].close)
        return (self._closed_atr * Decimal(self.period - 1) + current_range) / Decimal(self.period)


class ReplayBroker:
    def __init__(
        self,
        instrument: InstrumentSettings,
        initial_cash: Decimal,
        position_fraction: Decimal,
        fee_bps: Decimal,
        slippage_bps: Decimal,
        minimum_notional: Decimal,
    ):
        self.instrument = instrument
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.position_fraction = position_fraction
        self.fee_rate = fee_bps / Decimal("10000")
        self.slippage_rate = slippage_bps / Decimal("10000")
        self.minimum_notional = minimum_notional
        self.step = Decimal(str(instrument.quantity_step))
        self.leverage = Decimal(instrument.leverage)
        self.quantity = Decimal("0")
        self.average_price = Decimal("0")
        self.total_fees = Decimal("0")
        self.total_funding = Decimal("0")
        self.trades: list[ReplayTrade] = []
        self.open_trade: OpenReplayTrade | None = None
        self.peak_equity = initial_cash
        self.max_drawdown = Decimal("0")

    @property
    def has_position(self) -> bool:
        return self.quantity != 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    def fill(
        self,
        side: Side,
        market_price: Decimal,
        timestamp_ms: int,
        *,
        reduce_only: bool = False,
    ) -> bool:
        fill_price = market_price * (
            Decimal("1") + self.slippage_rate
            if side is Side.BUY
            else Decimal("1") - self.slippage_rate
        )
        if self.instrument.paper_model == "futures":
            return self._fill_futures(side, fill_price, timestamp_ms, reduce_only=reduce_only)
        return self._fill_spot(side, fill_price, timestamp_ms)

    def _fill_spot(self, side: Side, fill_price: Decimal, timestamp_ms: int) -> bool:
        if side is Side.BUY:
            if self.quantity > 0:
                return False
            budget = self.cash * self.position_fraction
            quantity = _floor_step(
                budget / (fill_price * (Decimal("1") + self.fee_rate)), self.step
            )
            notional = fill_price * quantity
            fee = notional * self.fee_rate
            if quantity <= 0 or notional < self.minimum_notional or notional + fee > self.cash:
                return False
            self.cash -= notional + fee
            self.quantity = quantity
            self.average_price = fill_price
            self.total_fees += fee
            self.open_trade = OpenReplayTrade(
                direction="LONG",
                entry_at_ms=timestamp_ms,
                entry_price=fill_price,
                quantity=quantity,
                entry_fee=fee,
            )
            return True

        if self.quantity <= 0:
            return False
        quantity = self.quantity
        notional = fill_price * quantity
        fee = notional * self.fee_rate
        self.cash += notional - fee
        self.total_fees += fee
        self._complete_trade(fill_price, fee, timestamp_ms)
        self.quantity = Decimal("0")
        self.average_price = Decimal("0")
        return True

    def _fill_futures(
        self,
        side: Side,
        fill_price: Decimal,
        timestamp_ms: int,
        *,
        reduce_only: bool,
    ) -> bool:
        desired_sign = Decimal("1") if side is Side.BUY else Decimal("-1")
        if reduce_only:
            if not self.quantity or self.quantity * desired_sign >= 0:
                return False
            close_quantity = abs(self.quantity)
            close_fee = fill_price * close_quantity * self.fee_rate
            close_realized = self.quantity * (fill_price - self.average_price) - close_fee
            self.cash += close_realized
            self.total_fees += close_fee
            self._complete_trade(fill_price, close_fee, timestamp_ms)
            self.quantity = Decimal("0")
            self.average_price = Decimal("0")
            return True

        if self.quantity:
            return False

        budget = self.cash * self.position_fraction
        required_per_unit = fill_price / self.leverage + fill_price * self.fee_rate
        quantity = _floor_step(budget / required_per_unit, self.step)
        notional = fill_price * quantity
        fee = notional * self.fee_rate
        required_balance = notional / self.leverage + fee
        if quantity <= 0 or notional < self.minimum_notional or required_balance > self.cash:
            return False
        self.quantity = desired_sign * quantity
        self.average_price = fill_price
        self.cash -= fee
        self.total_fees += fee
        self.open_trade = OpenReplayTrade(
            direction="LONG" if desired_sign > 0 else "SHORT",
            entry_at_ms=timestamp_ms,
            entry_price=fill_price,
            quantity=quantity,
            entry_fee=fee,
        )
        return True

    def _complete_trade(self, exit_price: Decimal, exit_fee: Decimal, timestamp_ms: int) -> None:
        trade = self.open_trade
        if trade is None:
            return
        direction_sign = Decimal("1") if trade.direction == "LONG" else Decimal("-1")
        gross_pnl = direction_sign * trade.quantity * (exit_price - trade.entry_price)
        fees = trade.entry_fee + exit_fee
        self.trades.append(
            ReplayTrade(
                direction=trade.direction,
                entry_at_ms=trade.entry_at_ms,
                exit_at_ms=timestamp_ms,
                entry_price=trade.entry_price,
                exit_price=exit_price,
                quantity=trade.quantity,
                fees=fees,
                funding=trade.funding,
                net_pnl=gross_pnl - fees + trade.funding,
            )
        )
        self.open_trade = None

    def apply_funding(self, funding: FundingRate) -> Decimal:
        if self.instrument.paper_model != "futures" or not self.quantity:
            return Decimal("0")
        amount = -(self.quantity * funding.mark_price * funding.rate)
        self.cash += amount
        self.total_funding += amount
        if self.open_trade is not None:
            self.open_trade.funding += amount
        return amount

    def equity(self, market_price: Decimal) -> Decimal:
        if self.instrument.paper_model == "futures":
            return self.cash + self.quantity * (market_price - self.average_price)
        return self.cash + self.quantity * market_price

    def mark(self, market_price: Decimal) -> Decimal:
        equity = self.equity(market_price)
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            self.max_drawdown = min(self.max_drawdown, equity / self.peak_equity - Decimal("1"))
        return equity


@dataclass
class ReplayCandidate:
    parameters: ReplayParameters
    strategy: ReplayATRTickStrategy
    broker: ReplayBroker
    pending_signal: StrategySignal | None = None
    signals: int = 0
    profit_exit_signals: int = 0
    continuation_reentry_signals: int = 0
    funding_index: int = 0
    entry_atr: Decimal | None = None
    favorable_extreme: Decimal | None = None
    profit_protection: ATRProfitProtection | None = None
    continuation_direction: str | None = None
    continuation_anchor: Decimal | None = None
    continuation_eligible_bar_ms: int | None = None
    direction: str = "long_short"
    daily_equity: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if (
            self.parameters.continuation_reentry_atr is not None
            and self.parameters.continuation_reentry_atr < 0
        ):
            raise ValueError("continuation re-entry ATR must be non-negative")
        activation = self.parameters.profit_activation_atr
        trailing = self.parameters.profit_trailing_atr
        if activation is not None and trailing is not None:
            self.profit_protection = ATRProfitProtection(activation, trailing)
            if self.broker.has_position and self.entry_atr is not None:
                self.profit_protection.open(
                    entry_price=self.broker.average_price,
                    entry_atr=self.entry_atr,
                    is_short=self.broker.is_short,
                )

    @property
    def profit_stop(self) -> Decimal | None:
        return self.profit_protection.stop if self.profit_protection else None

    @property
    def profit_protection_active(self) -> bool:
        return bool(self.profit_protection and self.profit_protection.active)

    def process_tick(self, tick: Tick, funding_rates: list[FundingRate]) -> None:
        while (
            self.funding_index < len(funding_rates)
            and funding_rates[self.funding_index].timestamp_ms <= tick.timestamp_ms
        ):
            self.broker.apply_funding(funding_rates[self.funding_index])
            self.funding_index += 1

        if self.pending_signal is not None and tick.event_id != self.pending_signal.tick_id:
            pending_signal = self.pending_signal
            position_before = self.broker.quantity
            filled = self.broker.fill(
                pending_signal.side,
                tick.price,
                tick.timestamp_ms,
                reduce_only=pending_signal.reduce_only,
            )
            self.strategy.on_fill(tick.timestamp_ms, filled=filled)
            self.pending_signal = None
            if filled:
                if position_before == 0 and self.broker.has_position:
                    self._clear_continuation_state()
                    self.entry_atr = pending_signal.atr
                    self.favorable_extreme = self.broker.average_price
                    if self.profit_protection is not None:
                        self.profit_protection.open(
                            entry_price=self.broker.average_price,
                            entry_atr=pending_signal.atr,
                            is_short=self.broker.is_short,
                        )
                elif position_before != 0 and not self.broker.has_position:
                    if self.parameters.continuation_reentry_atr is not None:
                        trade = self.broker.trades[-1]
                        self.continuation_direction = trade.direction
                        self.continuation_anchor = trade.exit_price
                        fill_bar_start = (
                            tick.timestamp_ms // self.strategy.bar_ms * self.strategy.bar_ms
                        )
                        self.continuation_eligible_bar_ms = fill_bar_start + self.strategy.bar_ms
                    self._clear_profit_state()

        signal = self.strategy.on_tick(
            tick,
            has_position=self.broker.has_position,
            has_pending_order=self.pending_signal is not None,
            allow_short=self.direction in {"short_only", "long_short"},
            allow_long=self.direction in {"long_only", "long_short"},
            is_short=self.broker.is_short,
        )
        if signal is None:
            signal = self._continuation_reentry_signal(tick)
        if signal is None:
            signal = self._profit_exit_signal(tick)
        if signal is not None:
            self.pending_signal = signal
            self.signals += 1
        self.broker.mark(tick.price)

    def _continuation_reentry_signal(self, tick: Tick) -> StrategySignal | None:
        threshold = self.parameters.continuation_reentry_atr
        eligible_bar = self.continuation_eligible_bar_ms
        if (
            threshold is None
            or self.broker.has_position
            or self.continuation_direction is None
            or self.continuation_anchor is None
            or eligible_bar is None
        ):
            return None
        bar_start = tick.timestamp_ms // self.strategy.bar_ms * self.strategy.bar_ms
        if bar_start > eligible_bar:
            self._clear_continuation_state()
            return None
        signal = self.strategy.continuation_reentry_signal(
            tick,
            direction=self.continuation_direction,
            exit_anchor=self.continuation_anchor,
            eligible_bar_ms=eligible_bar,
            threshold_atr=Decimal(str(threshold)),
            has_pending_order=self.pending_signal is not None,
        )
        if signal is not None:
            self.continuation_reentry_signals += 1
        return signal

    def _profit_exit_signal(self, tick: Tick) -> StrategySignal | None:
        atr = self.strategy.last_atr
        if (
            not self.broker.has_position
            or self.entry_atr is None
            or atr is None
            or self.strategy.trailing_stop is None
        ):
            return None

        is_short = self.broker.is_short
        entry_price = self.broker.average_price
        self.favorable_extreme = (
            min(self.favorable_extreme or entry_price, tick.price)
            if is_short
            else max(self.favorable_extreme or entry_price, tick.price)
        )
        if self.strategy.action_this_bar:
            return None

        fixed_atr = self.parameters.fixed_take_profit_atr
        if fixed_atr is not None:
            distance = self.entry_atr * Decimal(str(fixed_atr))
            reached = (
                tick.price <= entry_price - distance
                if is_short
                else tick.price >= entry_price + distance
            )
            if reached:
                return self._build_profit_signal(
                    tick,
                    atr,
                    "fixed_atr_take_profit",
                    entry_price - distance if is_short else entry_price + distance,
                )

        if self.profit_protection is None:
            return None
        crossed_stop = self.profit_protection.observe(
            tick.price,
            atr,
            action_locked=self.strategy.action_this_bar,
        )
        if crossed_stop is None:
            return None
        return self._build_profit_signal(
            tick,
            atr,
            "atr_profit_protection",
            crossed_stop,
        )

    def _build_profit_signal(
        self,
        tick: Tick,
        atr: Decimal,
        reason: str,
        exit_stop: Decimal,
    ) -> StrategySignal:
        self.profit_exit_signals += 1
        return StrategySignal(
            side=Side.BUY if self.broker.is_short else Side.SELL,
            reason=reason,
            signal_price=tick.price,
            trailing_stop=exit_stop,
            atr=atr,
            bar_start_ms=tick.timestamp_ms // self.strategy.bar_ms * self.strategy.bar_ms,
            tick_id=tick.event_id,
            reduce_only=self.broker.instrument.paper_model == "futures",
        )

    def _clear_profit_state(self) -> None:
        self.entry_atr = None
        self.favorable_extreme = None
        if self.profit_protection is not None:
            self.profit_protection.reset()

    def _clear_continuation_state(self) -> None:
        self.continuation_direction = None
        self.continuation_anchor = None
        self.continuation_eligible_bar_ms = None

    def snapshot_day(self, timestamp_ms: int, market_price: Decimal) -> None:
        self.daily_equity.append(
            {
                "date": datetime.fromtimestamp(timestamp_ms / 1000, UTC).date().isoformat(),
                "timestamp_ms": timestamp_ms,
                "equity": float(self.broker.equity(market_price)),
            }
        )


def run_parameter_grid(
    settings: Settings,
    instrument: InstrumentSettings,
    parameters: Iterable[ReplayParameters],
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    direction: str | None = None,
    progress_callback: Callable[[float], None] | None = None,
    warmup_callback: Callable[[int], None] | None = None,
) -> tuple[dict[str, Any], list[ReplayResult]]:
    if direction is None:
        direction = "long_short" if instrument.short_enabled else "long_only"
    if direction not in {"long_only", "short_only", "long_short"}:
        raise ValueError(f"invalid replay direction: {direction}")
    database_uri = f"file:{settings.database_path}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        market_id = instrument.market_id
        available = connection.execute(
            """
            SELECT
                (
                    SELECT timestamp_ms FROM agg_trades
                    WHERE instrument_id = ?
                    ORDER BY timestamp_ms ASC LIMIT 1
                ) AS first_ms,
                (
                    SELECT timestamp_ms FROM agg_trades
                    WHERE instrument_id = ?
                    ORDER BY timestamp_ms DESC LIMIT 1
                ) AS last_ms
            """,
            (market_id, market_id),
        ).fetchone()
        if available is None or available["first_ms"] is None:
            raise ValueError(f"no aggTrade data for {instrument.id}")
        requested_start = start_ms
        if requested_start is None:
            requested_start = _default_replay_start(
                connection,
                market_id,
                settings.strategy.bar_minutes,
                settings.warmup_bars,
            )
        replay_start = _replay_start_with_warmup(
            connection,
            market_id,
            settings.strategy.bar_minutes,
            requested_start,
            int(available["first_ms"]),
            settings.warmup_bars,
        )
        replay_end = min(int(available["last_ms"]), end_ms or int(available["last_ms"]))
        if replay_start >= replay_end:
            raise ValueError(f"invalid replay range for {instrument.id}")

        warmup_bars = _load_warmup_bars(connection, market_id, replay_start, settings.warmup_bars)
        if not warmup_bars:
            raise ValueError(f"no pre-replay OHLCV warmup for {instrument.id}")
        if warmup_callback is not None:
            warmup_callback(len(warmup_bars))
        funding_rates = _load_funding_rates(connection, market_id, replay_start, replay_end)

        position_fraction = Decimal(
            str(
                instrument.position_fraction
                if instrument.position_fraction is not None
                else settings.strategy.position_fraction
            )
        )
        fee_bps = Decimal(
            str(
                instrument.fee_bps if instrument.fee_bps is not None else settings.execution.fee_bps
            )
        )
        slippage_bps = Decimal(
            str(
                instrument.slippage_bps
                if instrument.slippage_bps is not None
                else settings.execution.slippage_bps
            )
        )
        minimum_notional = Decimal(
            str(
                instrument.minimum_notional
                if instrument.minimum_notional is not None
                else settings.execution.minimum_notional
            )
        )
        candidates = []
        for item in parameters:
            strategy = ReplayATRTickStrategy(
                item.atr_period,
                item.atr_multiplier,
                settings.strategy.bar_minutes,
                settings.strategy.trend_efficiency_period,
                settings.strategy.minimum_trend_efficiency,
                settings.strategy.reversal_confirmation_atr,
            )
            strategy.bootstrap(warmup_bars)
            candidates.append(
                ReplayCandidate(
                    parameters=item,
                    strategy=strategy,
                    broker=ReplayBroker(
                        instrument,
                        Decimal(str(settings.initial_cash)),
                        position_fraction,
                        fee_bps,
                        slippage_bps,
                        minimum_notional,
                    ),
                    direction=direction,
                )
            )

        tick_count = 0
        raw_trade_count = 0
        last_price: Decimal | None = None
        last_timestamp_ms: int | None = None
        last_day: int | None = None
        rows = connection.execute(
            """
            SELECT event_id, timestamp_ms, price, open_price, high_price, low_price,
                   quantity, source,
                   first_trade_id, last_trade_id
            FROM agg_trades
            WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
            ORDER BY timestamp_ms, received_at_ms, event_id
            """,
            (market_id, replay_start, replay_end),
        )
        for row in rows:
            timestamp_ms = int(row["timestamp_ms"])
            day = timestamp_ms // 86_400_000
            if last_day is not None and day != last_day and last_price is not None:
                for candidate in candidates:
                    candidate.snapshot_day(last_timestamp_ms or timestamp_ms, last_price)
            tick = Tick(
                event_id=row["event_id"],
                timestamp_ms=timestamp_ms,
                price=Decimal(row["price"]),
                quantity=Decimal(row["quantity"]),
                source=row["source"],
                first_trade_id=row["first_trade_id"],
                last_trade_id=row["last_trade_id"],
                open_price=(Decimal(row["open_price"]) if row["open_price"] is not None else None),
                high_price=(Decimal(row["high_price"]) if row["high_price"] is not None else None),
                low_price=(Decimal(row["low_price"]) if row["low_price"] is not None else None),
            )
            for candidate in candidates:
                candidate.process_tick(tick, funding_rates)
            tick_count += 1
            raw_trade_count += (
                int(row["last_trade_id"]) - int(row["first_trade_id"]) + 1
                if row["first_trade_id"] is not None and row["last_trade_id"] is not None
                else 1
            )
            last_price = tick.price
            last_timestamp_ms = tick.timestamp_ms
            last_day = day
            if progress_callback is not None and tick_count % 100_000 == 0:
                progress_callback((tick.timestamp_ms - replay_start) / (replay_end - replay_start))

    if last_price is None:
        raise ValueError(f"no aggTrade data in selected range for {instrument.id}")
    for candidate in candidates:
        candidate.snapshot_day(last_timestamp_ms or replay_end, last_price)
    if progress_callback is not None:
        progress_callback(1.0)

    results = [
        _candidate_result(
            candidate,
            instrument,
            replay_start,
            replay_end,
            tick_count,
            raw_trade_count,
            len(warmup_bars),
            last_price,
        )
        for candidate in candidates
    ]
    metadata = {
        "instrument_id": instrument.id,
        "market_data_id": market_id,
        "allow_short": instrument.short_enabled,
        "direction": direction,
        "symbol": instrument.symbol,
        "paper_model": instrument.paper_model,
        "start_ms": replay_start,
        "end_ms": replay_end,
        "requested_start_ms": requested_start,
        "requested_end_ms": end_ms,
        "start_adjusted_for_warmup": replay_start > requested_start,
        "tick_count": tick_count,
        "raw_trade_count": raw_trade_count,
        "warmup_bars": len(warmup_bars),
        "warmup_interval_minutes": settings.strategy.bar_minutes,
        "fee_bps": float(fee_bps),
        "slippage_bps": float(slippage_bps),
        "leverage": instrument.leverage,
        "position_fraction": float(position_fraction),
        "target_exposure": float(position_fraction * Decimal(instrument.leverage)),
        "funding_events": len(funding_rates),
    }
    return metadata, results


def _candidate_result(
    candidate: ReplayCandidate,
    instrument: InstrumentSettings,
    start_ms: int,
    end_ms: int,
    tick_count: int,
    raw_trade_count: int,
    warmup_bars: int,
    last_price: Decimal,
) -> ReplayResult:
    broker = candidate.broker
    final_equity = broker.equity(last_price)
    net_profit = final_equity - broker.initial_cash
    wins = sum(trade.net_pnl > 0 for trade in broker.trades)
    losses = len(broker.trades) - wins
    gross_profit = sum(
        (trade.net_pnl for trade in broker.trades if trade.net_pnl > 0),
        Decimal("0"),
    )
    gross_loss = -sum(
        (trade.net_pnl for trade in broker.trades if trade.net_pnl < 0),
        Decimal("0"),
    )
    ending_position = "SHORT" if broker.quantity < 0 else "LONG" if broker.quantity > 0 else "FLAT"
    return ReplayResult(
        instrument_id=instrument.id,
        symbol=instrument.symbol,
        paper_model=instrument.paper_model,
        atr_period=candidate.parameters.atr_period,
        atr_multiplier=candidate.parameters.atr_multiplier,
        variant=candidate.parameters.variant,
        fixed_take_profit_atr=candidate.parameters.fixed_take_profit_atr,
        profit_activation_atr=candidate.parameters.profit_activation_atr,
        profit_trailing_atr=candidate.parameters.profit_trailing_atr,
        continuation_reentry_atr=candidate.parameters.continuation_reentry_atr,
        start_ms=start_ms,
        end_ms=end_ms,
        tick_count=tick_count,
        raw_trade_count=raw_trade_count,
        warmup_bars=warmup_bars,
        initial_equity=float(broker.initial_cash),
        final_equity=float(final_equity),
        net_profit=float(net_profit),
        net_return=float(net_profit / broker.initial_cash),
        completed_trades=len(broker.trades),
        winning_trades=wins,
        losing_trades=losses,
        win_rate=wins / len(broker.trades) if broker.trades else None,
        gross_profit=float(gross_profit),
        gross_loss=float(gross_loss),
        profit_factor=float(gross_profit / gross_loss) if gross_loss else None,
        max_drawdown=float(broker.max_drawdown),
        total_fees=float(broker.total_fees),
        total_funding=float(broker.total_funding),
        signals=candidate.signals,
        profit_exit_signals=candidate.profit_exit_signals,
        continuation_reentry_signals=candidate.continuation_reentry_signals,
        ending_position=ending_position,
        daily_equity=candidate.daily_equity,
    )


def _load_warmup_bars(
    connection: sqlite3.Connection,
    instrument_id: str,
    start_ms: int,
    limit: int = 200,
) -> list[Bar]:
    rows = connection.execute(
        """
        SELECT * FROM (
            SELECT start_ms, end_ms, open, high, low, close, volume, trade_count
            FROM ohlcv_bars
            WHERE instrument_id = ? AND interval_minutes = 15
              AND is_closed = 1 AND end_ms < ?
            ORDER BY start_ms DESC LIMIT ?
        ) ORDER BY start_ms
        """,
        (instrument_id, start_ms, limit),
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


def _default_replay_start(
    connection: sqlite3.Connection,
    instrument_id: str,
    interval_minutes: int,
    warmup_bars: int,
) -> int:
    row = connection.execute(
        """
        SELECT start_ms FROM ohlcv_bars
        WHERE instrument_id = ? AND interval_minutes = ? AND is_closed = 1
        ORDER BY start_ms
        LIMIT 1 OFFSET ?
        """,
        (instrument_id, interval_minutes, warmup_bars),
    ).fetchone()
    if row is None:
        raise ValueError(f"insufficient OHLCV warmup for {instrument_id}")
    return int(row["start_ms"] if isinstance(row, sqlite3.Row) else row[0])


def _replay_start_with_warmup(
    connection: sqlite3.Connection,
    instrument_id: str,
    interval_minutes: int,
    requested_start_ms: int,
    first_tick_ms: int,
    warmup_bars: int,
) -> int:
    earliest_ready_ms = _default_replay_start(
        connection,
        instrument_id,
        interval_minutes,
        warmup_bars,
    )
    return max(first_tick_ms, requested_start_ms, earliest_ready_ms)


def _load_funding_rates(
    connection: sqlite3.Connection,
    account_id: str,
    start_ms: int,
    end_ms: int,
) -> list[FundingRate]:
    rows = connection.execute(
        """
        SELECT timestamp_ms, rate, mark_price FROM funding_rates
        WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
        ORDER BY timestamp_ms
        """,
        (account_id, start_ms, end_ms),
    )
    return [
        FundingRate(
            timestamp_ms=int(row["timestamp_ms"]),
            rate=Decimal(row["rate"]),
            mark_price=Decimal(row["mark_price"]),
        )
        for row in rows
    ]


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def select_recommendation(results: list[ReplayResult]) -> ReplayResult:
    if not results:
        raise ValueError("cannot select from empty replay results")
    return max(results, key=lambda item: (item.net_return, item.max_drawdown))


def build_report(payload: dict[str, Any]) -> str:
    lines = [
        "# ATR Tick Replay Grid",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        (
            "Selection rule: highest net return after fees, slippage and funding; "
            "max drawdown breaks ties."
        ),
        "",
    ]
    for run in payload["runs"]:
        metadata = run["metadata"]
        recommendation = run["recommendation"]
        lines.extend(
            [
                f"## {metadata['symbol']} ({metadata['paper_model']})",
                "",
                (
                    f"Range: {_iso(metadata['start_ms'])} to {_iso(metadata['end_ms'])}; "
                    f"{metadata['tick_count']:,} stored ticks / "
                    f"{metadata['raw_trade_count']:,} underlying trades; "
                    f"{metadata['warmup_bars']} warmup bars."
                ),
                "",
                (
                    f"Execution: {metadata['leverage']}x venue leverage, "
                    f"{metadata['position_fraction']:.2%} position budget, "
                    f"{metadata['target_exposure']:.2f}x target exposure."
                ),
                "",
                (
                    f"Recommended in this sample: ATR({recommendation['atr_period']}) x "
                    f"{recommendation['atr_multiplier']:.2f}."
                ),
                "",
                (
                    "| ATR | Mult | Net return | Net PnL | Trades | Win rate | "
                    "Max DD | Fees | Funding |"
                ),
                "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        ordered = sorted(
            run["results"],
            key=lambda item: (item["net_return"], item["max_drawdown"]),
            reverse=True,
        )
        for item in ordered:
            win_rate = "--" if item["win_rate"] is None else f"{item['win_rate']:.2%}"
            lines.append(
                f"| {item['atr_period']} | {item['atr_multiplier']:.2f} | "
                f"{item['net_return']:.2%} | {item['net_profit']:,.2f} | "
                f"{item['completed_trades']} | {win_rate} | {item['max_drawdown']:.2%} | "
                f"{item['total_fees']:,.2f} | {item['total_funding']:,.2f} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Interpretation",
            "",
            "The result is sample-optimal, not validated long-term. The stored sample spans "
            "only a few days. Futures rows are persisted 250 ms trade buckets; historical "
            "intrabucket high/low paths are unavailable, "
            "so replay uses each bucket close as its Tick price.",
            "",
        ]
    )
    return "\n".join(lines)


def _iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def _csv_numbers(value: str, converter: type[int] | type[float]) -> tuple[Any, ...]:
    return tuple(converter(item.strip()) for item in value.split(",") if item.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay persisted aggTrade data over an ATR grid")
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--instrument", action="append", dest="instruments")
    parser.add_argument("--periods", default=",".join(str(value) for value in DEFAULT_PERIODS))
    parser.add_argument(
        "--multipliers", default=",".join(str(value) for value in DEFAULT_MULTIPLIERS)
    )
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--start-ms", type=int)
    parser.add_argument("--end-ms", type=int)
    parser.add_argument(
        "--minimum-return",
        type=float,
        help="Fail when any selected instrument's recommendation is below this return.",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    selected_ids = set(args.instruments or [item.id for item in settings.instruments])
    instruments = [item for item in settings.instruments if item.id in selected_ids]
    missing = selected_ids - {item.id for item in instruments}
    if missing:
        raise ValueError(f"unknown instruments: {', '.join(sorted(missing))}")
    periods = _csv_numbers(args.periods, int)
    multipliers = _csv_numbers(args.multipliers, float)
    parameters = [
        ReplayParameters(period, multiplier) for period in periods for multiplier in multipliers
    ]

    runs = []
    for instrument in instruments:
        print(f"Replaying {instrument.id}: {len(parameters)} ATR combinations...", flush=True)
        metadata, results = run_parameter_grid(
            settings,
            instrument,
            parameters,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
        )
        recommendation = select_recommendation(results)
        runs.append(
            {
                "metadata": metadata,
                "recommendation": asdict(recommendation),
                "results": [asdict(item) for item in results],
            }
        )
        print(
            f"  best ATR({recommendation.atr_period}) x {recommendation.atr_multiplier:.2f}: "
            f"{recommendation.net_return:.2%}, {recommendation.completed_trades} trades",
            flush=True,
        )

    generated_at = datetime.now(UTC).isoformat()
    payload = {"generated_at": generated_at, "runs": runs}
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"atr_tick_grid_{stamp}.json"
    markdown_path = output_dir / f"atr_tick_grid_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    markdown_path.write_text(build_report(payload))
    print(json_path)
    print(markdown_path)
    if args.minimum_return is not None:
        failures = [
            run for run in runs if run["recommendation"]["net_return"] < args.minimum_return
        ]
        if failures:
            details = ", ".join(
                f"{run['metadata']['instrument_id']}={run['recommendation']['net_return']:.2%}"
                for run in failures
            )
            raise SystemExit(f"minimum return {args.minimum_return:.2%} not met: {details}")


if __name__ == "__main__":
    main()
