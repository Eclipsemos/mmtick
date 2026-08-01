"""Tick-driven ATR trailing-stop strategy from commit 25784e3."""

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
    bought_this_bar: bool
    flattened_this_bar: bool
    last_cross: str | None
    last_cross_at_ms: int | None
    last_cross_result: str | None
    last_cross_reason: str | None


class ATRTickStrategy:
    """Apply the supplied 15-minute ATR rules on every received market tick."""

    ALGORITHM_VERSION = "25784e3"

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
        self.bought_this_bar = False
        self.flattened_this_bar = False
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
        for bar in sorted(bars, key=lambda value: value.start_ms):
            candidate = [*self.completed_bars, bar]
            atr = wilder_atr(candidate, self.period)
            if atr is not None:
                self._move_stop(bar.close, atr)
                self.previous_price = bar.close
                self.last_atr = atr
            self.completed_bars.append(bar)
        self.completed_bars = self.completed_bars[-500:]

    def restore_runtime(self, value: dict[str, Any] | None) -> None:
        if not value or value.get("algorithm_version") != self.ALGORITHM_VERSION:
            return
        self.last_cross = value.get("last_cross")
        self.last_cross_at_ms = value.get("last_cross_at_ms")
        self.last_cross_result = value.get("last_cross_result")
        self.last_cross_reason = value.get("last_cross_reason")
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
        self.previous_price = _decimal_or_none(value.get("previous_price"))
        self.trailing_stop = _decimal_or_none(value.get("trailing_stop"))
        self.last_atr = _decimal_or_none(value.get("last_atr"))
        self.bought_this_bar = bool(value.get("bought_this_bar", False))
        self.flattened_this_bar = bool(value.get("flattened_this_bar", False))
        self.current_bar = current

    def runtime_state(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.ALGORITHM_VERSION,
            "previous_price": _string_or_none(self.previous_price),
            "trailing_stop": _string_or_none(self.trailing_stop),
            "last_atr": _string_or_none(self.last_atr),
            "bought_this_bar": self.bought_this_bar,
            "flattened_this_bar": self.flattened_this_bar,
            "last_cross": self.last_cross,
            "last_cross_at_ms": self.last_cross_at_ms,
            "last_cross_result": self.last_cross_result,
            "last_cross_reason": self.last_cross_reason,
            "current_bar": self.current_bar.as_dict() if self.current_bar else None,
        }

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
        if self.current_bar is None or bar_start > self.current_bar.start_ms:
            if self.current_bar is not None:
                self.completed_bars.append(self.current_bar)
                self.completed_bars = self.completed_bars[-500:]
            self.current_bar = Bar(
                start_ms=bar_start,
                end_ms=bar_start + self.bar_ms - 1,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=tick.quantity,
            )
            self.bought_this_bar = False
            self.flattened_this_bar = False
        else:
            self.current_bar.update(tick)

        atr = wilder_atr([*self.completed_bars, self.current_bar], self.period)
        if atr is None:
            self.previous_price = tick.price
            return None

        previous_stop = self.trailing_stop
        previous_price = self.previous_price
        cross_up = (
            previous_stop is not None
            and previous_price is not None
            and previous_price <= previous_stop
            and tick.price > previous_stop
        )
        cross_down = (
            previous_stop is not None
            and previous_price is not None
            and previous_price >= previous_stop
            and tick.price < previous_stop
        )
        self._move_stop(tick.price, atr)
        self.previous_price = tick.price
        self.last_atr = atr

        if cross_up:
            blocked_reason = _buy_block_reason(
                emit_signals=emit_signals,
                has_position=has_position,
                has_pending_order=has_pending_order,
                bought_this_bar=self.bought_this_bar,
                flattened_this_bar=self.flattened_this_bar,
            )
            if blocked_reason is None:
                self.bought_this_bar = True
                self._record_cross("UP", tick.timestamp_ms, "BUY_SIGNAL", None)
                return StrategySignal(
                    side=Side.BUY,
                    reason="price_crossed_above_atr_stop",
                    signal_price=tick.price,
                    trailing_stop=self.trailing_stop,
                    atr=atr,
                    bar_start_ms=bar_start,
                    tick_id=tick.event_id,
                )
            self._record_cross("UP", tick.timestamp_ms, "BLOCKED", blocked_reason)

        if cross_down:
            blocked_reason = _sell_block_reason(
                emit_signals=emit_signals,
                has_position=has_position,
                has_pending_order=has_pending_order,
                flattened_this_bar=self.flattened_this_bar,
            )
            if blocked_reason is None:
                self.flattened_this_bar = True
                self._record_cross("DOWN", tick.timestamp_ms, "SELL_SIGNAL", None)
                return StrategySignal(
                    side=Side.SELL,
                    reason="price_crossed_below_atr_stop",
                    signal_price=tick.price,
                    trailing_stop=self.trailing_stop,
                    atr=atr,
                    bar_start_ms=bar_start,
                    tick_id=tick.event_id,
                )
            self._record_cross("DOWN", tick.timestamp_ms, "BLOCKED", blocked_reason)
        return None

    def view(self) -> StrategyView:
        relation = "warming"
        price = self.previous_price
        if price is not None and self.trailing_stop is not None:
            relation = "above" if price > self.trailing_stop else "below"
        return StrategyView(
            ready=self.last_atr is not None and self.trailing_stop is not None,
            atr=self.last_atr,
            trailing_stop=self.trailing_stop,
            price=price,
            relation=relation,
            bar_start_ms=self.current_bar.start_ms if self.current_bar else None,
            bought_this_bar=self.bought_this_bar,
            flattened_this_bar=self.flattened_this_bar,
            last_cross=self.last_cross,
            last_cross_at_ms=self.last_cross_at_ms,
            last_cross_result=self.last_cross_result,
            last_cross_reason=self.last_cross_reason,
        )

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

    def _move_stop(self, price: Decimal, atr: Decimal) -> None:
        distance = atr * self.multiplier
        previous_stop = self.trailing_stop
        previous_price = self.previous_price
        if previous_stop is None or previous_price is None:
            self.trailing_stop = price + distance
        elif price > previous_stop and previous_price > previous_stop:
            self.trailing_stop = max(previous_stop, price - distance)
        elif price < previous_stop and previous_price < previous_stop:
            self.trailing_stop = min(previous_stop, price + distance)
        elif price > previous_stop:
            self.trailing_stop = price - distance
        else:
            self.trailing_stop = price + distance


def _decimal_or_none(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _string_or_none(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _buy_block_reason(
    *,
    emit_signals: bool,
    has_position: bool,
    has_pending_order: bool,
    bought_this_bar: bool,
    flattened_this_bar: bool,
) -> str | None:
    if not emit_signals:
        return "TRADING_PAUSED"
    if has_position:
        return "ALREADY_LONG"
    if has_pending_order:
        return "ORDER_PENDING"
    if bought_this_bar:
        return "BUY_LOCKED_THIS_BAR"
    if flattened_this_bar:
        return "REENTRY_LOCKED_THIS_BAR"
    return None


def _sell_block_reason(
    *,
    emit_signals: bool,
    has_position: bool,
    has_pending_order: bool,
    flattened_this_bar: bool,
) -> str | None:
    if not emit_signals:
        return "TRADING_PAUSED"
    if not has_position:
        return "NO_POSITION"
    if has_pending_order:
        return "ORDER_PENDING"
    if flattened_this_bar:
        return "EXIT_LOCKED_THIS_BAR"
    return None
