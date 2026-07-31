"""Pine-compatible ATR trailing stop with close-confirmed signals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mastermind_tick.models import Bar, Side, StrategySignal, Tick


def true_range(bar: Bar, previous_close: Decimal | None) -> Decimal:
    values = [bar.high - bar.low]
    if previous_close is not None:
        values.extend([abs(bar.high - previous_close), abs(bar.low - previous_close)])
    return max(values)


def wilder_atr(bars: list[Bar], period: int) -> Decimal | None:
    """Return the last Pine-style ATR, seeded by a full-window TR average."""
    if len(bars) < period:
        return None
    ranges: list[Decimal] = []
    previous_close: Decimal | None = None
    for bar in bars:
        ranges.append(true_range(bar, previous_close))
        previous_close = bar.close
    atr = sum(ranges[:period], Decimal("0")) / Decimal(period)
    for value in ranges[period:]:
        atr = (atr * Decimal(period - 1) + value) / Decimal(period)
    return atr


@dataclass
class StrategyView:
    ready: bool
    atr: Decimal | None
    trailing_stop: Decimal | None
    price: Decimal | None
    relation: str
    bar_start_ms: int | None
    last_cross: str | None
    last_cross_at_ms: int | None
    last_cross_result: str | None
    last_cross_reason: str | None


class ATRTickStrategy:
    """Update ATR on ticks and trade only after a 15-minute bar closes."""

    def __init__(
        self,
        period: int = 7,
        multiplier: float = 1.0,
        bar_minutes: int = 15,
    ):
        if period < 1 or multiplier <= 0 or bar_minutes <= 0:
            raise ValueError("invalid ATR strategy parameters")
        self.period = period
        self.multiplier = Decimal(str(multiplier))
        self.bar_ms = bar_minutes * 60_000
        self.completed_bars: list[Bar] = []
        self.current_bar: Bar | None = None
        self.previous_price: Decimal | None = None
        self.trailing_stop: Decimal | None = None
        self.last_atr: Decimal | None = None
        self.committed_price: Decimal | None = None
        self.committed_stop: Decimal | None = None
        self.committed_atr: Decimal | None = None
        self.last_cross: str | None = None
        self.last_cross_at_ms: int | None = None
        self.last_cross_result: str | None = None
        self.last_cross_reason: str | None = None

    def bootstrap(self, bars: list[Bar]) -> None:
        """Warm the indicator without creating historical paper orders."""
        self.completed_bars = []
        self.current_bar = None
        self.previous_price = None
        self.trailing_stop = None
        self.last_atr = None
        self.committed_price = None
        self.committed_stop = None
        self.committed_atr = None
        for bar in sorted(bars, key=lambda value: value.start_ms):
            candidate = [*self.completed_bars, bar]
            atr = wilder_atr(candidate, self.period)
            if atr is not None:
                self.committed_stop = pine_trailing_stop(
                    source=bar.close,
                    atr=atr,
                    multiplier=self.multiplier,
                    previous_stop=self.committed_stop,
                    previous_source=self.committed_price,
                )
                self.committed_atr = atr
            self.committed_price = bar.close
            self.completed_bars.append(bar)
        self.completed_bars = self.completed_bars[-500:]
        self.previous_price = self.committed_price
        self.trailing_stop = self.committed_stop
        self.last_atr = self.committed_atr

    def restore_runtime(self, value: dict[str, Any] | None) -> None:
        if not value:
            return
        state_version = int(value.get("pine_state_version", 1))
        if state_version >= 5:
            self.last_cross = value.get("last_cross")
            self.last_cross_at_ms = value.get("last_cross_at_ms")
            self.last_cross_result = value.get("last_cross_result")
            self.last_cross_reason = value.get("last_cross_reason")
        if self.committed_price is None and state_version >= 2:
            self.committed_price = _decimal_or_none(value.get("committed_price"))
            self.committed_stop = _decimal_or_none(value.get("committed_stop"))
            self.committed_atr = _decimal_or_none(value.get("committed_atr"))
        current_value = value.get("current_bar")
        current = Bar.from_dict(current_value) if current_value else None
        latest_completed_start = (
            self.completed_bars[-1].start_ms if self.completed_bars else None
        )
        if (
            current is None
            or latest_completed_start is not None
            and current.start_ms <= latest_completed_start
        ):
            return
        self.current_bar = current
        self._refresh_realtime()

    def runtime_state(self) -> dict[str, Any]:
        return {
            "pine_state_version": 5,
            "previous_price": _string_or_none(self.previous_price),
            "trailing_stop": _string_or_none(self.trailing_stop),
            "last_atr": _string_or_none(self.last_atr),
            "committed_price": _string_or_none(self.committed_price),
            "committed_stop": _string_or_none(self.committed_stop),
            "committed_atr": _string_or_none(self.committed_atr),
            "last_cross": self.last_cross,
            "last_cross_at_ms": self.last_cross_at_ms,
            "last_cross_result": self.last_cross_result,
            "last_cross_reason": self.last_cross_reason,
            "current_bar": self.current_bar.as_dict() if self.current_bar else None,
        }

    def seed_current_bar(self, bar: Bar) -> None:
        """Seed the open Binance bar without emitting a retrospective signal."""
        latest_completed_start = (
            self.completed_bars[-1].start_ms if self.completed_bars else None
        )
        if latest_completed_start is not None and bar.start_ms <= latest_completed_start:
            return
        self.current_bar = Bar.from_dict(bar.as_dict())
        self._refresh_realtime()

    def on_tick(
        self,
        tick: Tick,
        *,
        has_position: bool,
        has_pending_order: bool,
        emit_signals: bool = True,
    ) -> StrategySignal | None:
        bar_start = tick.timestamp_ms // self.bar_ms * self.bar_ms
        if (
            self.current_bar is None
            and self.completed_bars
            and bar_start <= self.completed_bars[-1].start_ms
        ):
            return None
        if self.current_bar is not None and bar_start < self.current_bar.start_ms:
            return None

        if self.current_bar is None:
            self.current_bar = self._new_bar(bar_start, tick)
        elif bar_start > self.current_bar.start_ms:
            self.current_bar = self._new_bar(bar_start, tick)
        else:
            self.current_bar.update(tick)

        self._refresh_realtime()
        return None

    def on_bar_close(
        self,
        bar: Bar,
        *,
        has_position: bool,
        has_pending_order: bool,
        emit_signals: bool = True,
    ) -> StrategySignal | None:
        """Confirm a signal from an authoritative, closed Binance kline."""
        if self.is_bar_committed(bar.start_ms):
            return None
        signal = self._confirm_and_commit_bar(
            bar,
            has_position=has_position,
            has_pending_order=has_pending_order,
            emit_signals=emit_signals,
        )
        if self.current_bar is not None and self.current_bar.start_ms == bar.start_ms:
            self.current_bar = None
        self._refresh_realtime()
        return signal

    def is_bar_committed(self, bar_start_ms: int) -> bool:
        return bool(
            self.completed_bars
            and bar_start_ms <= self.completed_bars[-1].start_ms
        )

    @property
    def next_uncommitted_bar_start_ms(self) -> int | None:
        if not self.completed_bars:
            return None
        return self.completed_bars[-1].start_ms + self.bar_ms

    def view(self) -> StrategyView:
        relation = "warming"
        price = self.previous_price
        if price is not None and self.trailing_stop is not None:
            relation = "above" if price >= self.trailing_stop else "below"
        return StrategyView(
            ready=self.last_atr is not None and self.trailing_stop is not None,
            atr=self.last_atr,
            trailing_stop=self.trailing_stop,
            price=price,
            relation=relation,
            bar_start_ms=self.current_bar.start_ms if self.current_bar else None,
            last_cross=self.last_cross,
            last_cross_at_ms=self.last_cross_at_ms,
            last_cross_result=self.last_cross_result,
            last_cross_reason=self.last_cross_reason,
        )

    def _new_bar(self, bar_start: int, tick: Tick) -> Bar:
        trade_count = (
            tick.last_trade_id - tick.first_trade_id + 1
            if tick.first_trade_id is not None and tick.last_trade_id is not None
            else 1
        )
        return Bar(
            start_ms=bar_start,
            end_ms=bar_start + self.bar_ms - 1,
            open=tick.open_price or tick.price,
            high=tick.high_price or tick.price,
            low=tick.low_price or tick.price,
            close=tick.price,
            volume=tick.quantity,
            trade_count=trade_count,
        )

    def _confirm_and_commit_bar(
        self,
        closed_bar: Bar,
        *,
        has_position: bool,
        has_pending_order: bool,
        emit_signals: bool,
    ) -> StrategySignal | None:
        previous_close = self.committed_price
        previous_stop = self.committed_stop
        confirmed_atr = self._atr_for_bar(closed_bar)
        confirmed_stop = (
            pine_trailing_stop(
                source=closed_bar.close,
                atr=confirmed_atr,
                multiplier=self.multiplier,
                previous_stop=previous_stop,
                previous_source=previous_close,
            )
            if confirmed_atr is not None
            else None
        )

        signal: StrategySignal | None = None
        if confirmed_stop is not None and previous_close is not None and previous_stop is not None:
            cross_up = closed_bar.close > confirmed_stop and previous_close <= previous_stop
            cross_down = closed_bar.close < confirmed_stop and previous_close >= previous_stop
            if cross_up:
                blocked_reason = _buy_block_reason(
                    emit_signals=emit_signals,
                    has_position=has_position,
                    has_pending_order=has_pending_order,
                )
                result = "BUY_SIGNAL" if blocked_reason is None else "BLOCKED"
                self._record_cross("UP", closed_bar.end_ms, result, blocked_reason)
                if blocked_reason is None:
                    signal = StrategySignal(
                        side=Side.BUY,
                        reason="close_crossed_above_atr_stop",
                        signal_price=closed_bar.close,
                        trailing_stop=confirmed_stop,
                        atr=confirmed_atr,
                        bar_start_ms=closed_bar.start_ms,
                        tick_id=f"bar-close:{closed_bar.end_ms}",
                        signal_at_ms=closed_bar.end_ms,
                    )
            elif cross_down:
                blocked_reason = _sell_block_reason(
                    emit_signals=emit_signals,
                    has_position=has_position,
                    has_pending_order=has_pending_order,
                )
                result = "SELL_SIGNAL" if blocked_reason is None else "BLOCKED"
                self._record_cross("DOWN", closed_bar.end_ms, result, blocked_reason)
                if blocked_reason is None:
                    signal = StrategySignal(
                        side=Side.SELL,
                        reason="close_crossed_below_atr_stop",
                        signal_price=closed_bar.close,
                        trailing_stop=confirmed_stop,
                        atr=confirmed_atr,
                        bar_start_ms=closed_bar.start_ms,
                        tick_id=f"bar-close:{closed_bar.end_ms}",
                        signal_at_ms=closed_bar.end_ms,
                    )

        self.committed_price = closed_bar.close
        self.committed_stop = confirmed_stop
        self.committed_atr = confirmed_atr
        self.completed_bars.append(closed_bar)
        self.completed_bars = self.completed_bars[-500:]
        return signal

    def _record_cross(
        self,
        direction: str,
        timestamp_ms: int,
        result: str,
        reason: str | None,
    ) -> None:
        self.last_cross = direction
        self.last_cross_at_ms = timestamp_ms
        self.last_cross_result = result
        self.last_cross_reason = reason

    def _refresh_realtime(self) -> None:
        if self.current_bar is None:
            self.previous_price = self.committed_price
            self.trailing_stop = self.committed_stop
            self.last_atr = self.committed_atr
            return
        self.previous_price = self.current_bar.close
        self.last_atr = self._current_atr()
        self.trailing_stop = (
            pine_trailing_stop(
                source=self.current_bar.close,
                atr=self.last_atr,
                multiplier=self.multiplier,
                previous_stop=self.committed_stop,
                previous_source=self.committed_price,
            )
            if self.last_atr is not None
            else None
        )

    def _current_atr(self) -> Decimal | None:
        if self.current_bar is None:
            return self.committed_atr
        if self.committed_atr is not None:
            current_range = true_range(self.current_bar, self.committed_price)
            return (
                self.committed_atr * Decimal(self.period - 1) + current_range
            ) / Decimal(self.period)
        return wilder_atr([*self.completed_bars, self.current_bar], self.period)

    def _atr_for_bar(self, bar: Bar) -> Decimal | None:
        if self.committed_atr is not None:
            current_range = true_range(bar, self.committed_price)
            return (
                self.committed_atr * Decimal(self.period - 1) + current_range
            ) / Decimal(self.period)
        return wilder_atr([*self.completed_bars, bar], self.period)


def _decimal_or_none(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _string_or_none(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def pine_trailing_stop(
    *,
    source: Decimal,
    atr: Decimal,
    multiplier: Decimal,
    previous_stop: Decimal | None,
    previous_source: Decimal | None,
) -> Decimal:
    """Evaluate the Pine tsl expression from the previous committed bar."""
    distance = multiplier * atr
    if previous_stop is None or previous_source is None:
        return source + distance
    if source > previous_stop and previous_source > previous_stop:
        return max(previous_stop, source - distance)
    if source < previous_stop and previous_source < previous_stop:
        return min(previous_stop, source + distance)
    if source > previous_stop:
        return source - distance
    return source + distance


def _buy_block_reason(
    *,
    emit_signals: bool,
    has_position: bool,
    has_pending_order: bool,
) -> str | None:
    if not emit_signals:
        return "TRADING_PAUSED"
    if has_position:
        return "ALREADY_LONG"
    if has_pending_order:
        return "ORDER_PENDING"
    return None


def _sell_block_reason(
    *,
    emit_signals: bool,
    has_position: bool,
    has_pending_order: bool,
) -> str | None:
    if not emit_signals:
        return "TRADING_PAUSED"
    if not has_position:
        return "NO_POSITION"
    if has_pending_order:
        return "ORDER_PENDING"
    return None
