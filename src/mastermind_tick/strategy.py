"""Tick-driven ATR trailing-stop strategy with regime and startup alignment guards."""

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


class ATRProfitProtection:
    """One-way ATR profit stop shared by replay and live Futures execution."""

    STATE_VERSION = 1

    def __init__(self, activation_atr: float, trailing_atr: float):
        if activation_atr <= 0 or trailing_atr <= 0:
            raise ValueError("profit protection ATR parameters must be positive")
        self.activation_atr = Decimal(str(activation_atr))
        self.trailing_atr = Decimal(str(trailing_atr))
        self.entry_price: Decimal | None = None
        self.entry_atr: Decimal | None = None
        self.favorable_extreme: Decimal | None = None
        self.stop: Decimal | None = None
        self.position_side: str | None = None
        self.active = False

    def reset(self) -> None:
        self.entry_price = None
        self.entry_atr = None
        self.favorable_extreme = None
        self.stop = None
        self.position_side = None
        self.active = False

    def open(
        self,
        *,
        entry_price: Decimal,
        entry_atr: Decimal,
        is_short: bool,
    ) -> None:
        self.entry_price = entry_price
        self.entry_atr = entry_atr
        self.favorable_extreme = entry_price
        self.stop = None
        self.position_side = "SHORT" if is_short else "LONG"
        self.active = False

    def sync_position(
        self,
        *,
        quantity: Decimal,
        entry_price: Decimal,
        current_atr: Decimal,
    ) -> None:
        if quantity == 0:
            self.reset()
            return
        side = "SHORT" if quantity < 0 else "LONG"
        if (
            self.position_side != side
            or self.entry_price != entry_price
            or self.entry_atr is None
        ):
            self.open(
                entry_price=entry_price,
                entry_atr=current_atr,
                is_short=quantity < 0,
            )

    def observe(
        self, price: Decimal, current_atr: Decimal, *, action_locked: bool
    ) -> Decimal | None:
        if (
            self.entry_price is None
            or self.entry_atr is None
            or self.position_side is None
        ):
            return None
        is_short = self.position_side == "SHORT"
        self.favorable_extreme = (
            min(self.favorable_extreme or self.entry_price, price)
            if is_short
            else max(self.favorable_extreme or self.entry_price, price)
        )
        if action_locked:
            return None

        favorable_move = (
            self.entry_price - self.favorable_extreme
            if is_short
            else self.favorable_extreme - self.entry_price
        )
        trailing_distance = current_atr * self.trailing_atr
        if not self.active:
            if favorable_move < self.entry_atr * self.activation_atr:
                return None
            self.active = True
            self.stop = price + trailing_distance if is_short else price - trailing_distance
            return None

        if self.stop is None:
            return None
        crossed = price >= self.stop if is_short else price <= self.stop
        if crossed:
            return self.stop
        candidate = price + trailing_distance if is_short else price - trailing_distance
        self.stop = min(self.stop, candidate) if is_short else max(self.stop, candidate)
        return None

    def runtime_state(self) -> dict[str, Any]:
        return {
            "version": self.STATE_VERSION,
            "activation_atr": str(self.activation_atr),
            "trailing_atr": str(self.trailing_atr),
            "entry_price": _string_or_none(self.entry_price),
            "entry_atr": _string_or_none(self.entry_atr),
            "favorable_extreme": _string_or_none(self.favorable_extreme),
            "stop": _string_or_none(self.stop),
            "position_side": self.position_side,
            "active": self.active,
        }

    def restore_runtime(self, value: dict[str, Any] | None) -> None:
        if (
            not value
            or value.get("version") != self.STATE_VERSION
            or Decimal(str(value.get("activation_atr", "0"))) != self.activation_atr
            or Decimal(str(value.get("trailing_atr", "0"))) != self.trailing_atr
        ):
            return
        side = value.get("position_side")
        entry_price = _decimal_or_none(value.get("entry_price"))
        entry_atr = _decimal_or_none(value.get("entry_atr"))
        extreme = _decimal_or_none(value.get("favorable_extreme"))
        if side not in {"LONG", "SHORT"} or entry_price is None or entry_atr is None:
            return
        self.position_side = side
        self.entry_price = entry_price
        self.entry_atr = entry_atr
        self.favorable_extreme = extreme or entry_price
        self.stop = _decimal_or_none(value.get("stop"))
        self.active = bool(value.get("active", False))


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
    action_this_bar: bool
    trend_efficiency: Decimal | None
    trend_filter_passed: bool
    reversal_direction: str | None
    reversal_anchor: Decimal | None
    reversal_eligible_bar_ms: int | None
    last_cross: str | None
    last_cross_at_ms: int | None
    last_cross_result: str | None
    last_cross_reason: str | None


class ATRTickStrategy:
    """Apply the supplied 15-minute ATR rules on every received market tick."""

    ALGORITHM_VERSION = "atr_tick_v3_startup_alignment"

    def __init__(
        self,
        period: int = 7,
        multiplier: float = 1.0,
        bar_minutes: int = 15,
        trend_efficiency_period: int = 8,
        minimum_trend_efficiency: float = 0.25,
        reversal_confirmation_atr: float = 0.25,
    ):
        if (
            period < 1
            or multiplier <= 0
            or bar_minutes <= 0
            or trend_efficiency_period < 2
            or not 0 <= minimum_trend_efficiency <= 1
            or reversal_confirmation_atr < 0
        ):
            raise ValueError("invalid ATR strategy parameters")
        self.period = period
        self.multiplier = Decimal(str(multiplier))
        self.bar_ms = bar_minutes * 60_000
        self.trend_efficiency_period = trend_efficiency_period
        self.minimum_trend_efficiency = Decimal(str(minimum_trend_efficiency))
        self.reversal_confirmation_atr = Decimal(str(reversal_confirmation_atr))
        self.completed_bars: list[Bar] = []
        self.current_bar: Bar | None = None
        self.previous_price: Decimal | None = None
        self.trailing_stop: Decimal | None = None
        self.last_atr: Decimal | None = None
        self.bought_this_bar = False
        self.flattened_this_bar = False
        self.action_this_bar = False
        self.last_action_bar_start_ms: int | None = None
        self.last_trend_efficiency: Decimal | None = None
        self.reversal_direction: str | None = None
        self.reversal_anchor: Decimal | None = None
        self.reversal_eligible_bar_ms: int | None = None
        self.startup_alignment_checked = False
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
        self.last_trend_efficiency = None
        self.action_this_bar = False
        self.last_action_bar_start_ms = None
        self.reversal_direction = None
        self.reversal_anchor = None
        self.reversal_eligible_bar_ms = None
        self.startup_alignment_checked = False
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
        if (
            not value
            or value.get("algorithm_version") != self.ALGORITHM_VERSION
            or value.get("period") != self.period
            or Decimal(str(value.get("multiplier", "0"))) != self.multiplier
            or value.get("bar_ms") != self.bar_ms
            or value.get("trend_efficiency_period") != self.trend_efficiency_period
            or Decimal(str(value.get("minimum_trend_efficiency", "-1")))
            != self.minimum_trend_efficiency
            or Decimal(str(value.get("reversal_confirmation_atr", "-1")))
            != self.reversal_confirmation_atr
        ):
            return
        previous_price = _decimal_or_none(value.get("previous_price"))
        trailing_stop = _decimal_or_none(value.get("trailing_stop"))
        last_atr = _decimal_or_none(value.get("last_atr"))
        if previous_price is None or trailing_stop is None or last_atr is None:
            return
        self.last_cross = value.get("last_cross")
        self.last_cross_at_ms = value.get("last_cross_at_ms")
        self.last_cross_result = value.get("last_cross_result")
        self.last_cross_reason = value.get("last_cross_reason")
        self.previous_price = previous_price
        self.trailing_stop = trailing_stop
        self.last_atr = last_atr
        self.last_trend_efficiency = _decimal_or_none(value.get("last_trend_efficiency"))
        self.last_action_bar_start_ms = value.get("last_action_bar_start_ms")
        self.reversal_direction = value.get("reversal_direction")
        self.reversal_anchor = _decimal_or_none(value.get("reversal_anchor"))
        self.reversal_eligible_bar_ms = value.get("reversal_eligible_bar_ms")
        self.startup_alignment_checked = bool(
            value.get("startup_alignment_checked", False)
        )
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
        self.bought_this_bar = bool(value.get("bought_this_bar", False))
        self.flattened_this_bar = bool(value.get("flattened_this_bar", False))
        self.action_this_bar = bool(value.get("action_this_bar", False))
        self.current_bar = current

    def runtime_state(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.ALGORITHM_VERSION,
            "period": self.period,
            "multiplier": str(self.multiplier),
            "bar_ms": self.bar_ms,
            "trend_efficiency_period": self.trend_efficiency_period,
            "minimum_trend_efficiency": str(self.minimum_trend_efficiency),
            "reversal_confirmation_atr": str(self.reversal_confirmation_atr),
            "previous_price": _string_or_none(self.previous_price),
            "trailing_stop": _string_or_none(self.trailing_stop),
            "last_atr": _string_or_none(self.last_atr),
            "bought_this_bar": self.bought_this_bar,
            "flattened_this_bar": self.flattened_this_bar,
            "action_this_bar": self.action_this_bar,
            "last_action_bar_start_ms": self.last_action_bar_start_ms,
            "last_trend_efficiency": _string_or_none(self.last_trend_efficiency),
            "reversal_direction": self.reversal_direction,
            "reversal_anchor": _string_or_none(self.reversal_anchor),
            "reversal_eligible_bar_ms": self.reversal_eligible_bar_ms,
            "startup_alignment_checked": self.startup_alignment_checked,
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
        allow_short: bool = False,
        is_short: bool = False,
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
            self.action_this_bar = self.last_action_bar_start_ms == bar_start
        else:
            self.current_bar.update(tick)

        atr = self._current_atr()
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
        self.last_trend_efficiency = self._current_trend_efficiency()

        if not has_position and self.reversal_direction is not None:
            opposite_cross = (
                self.reversal_direction == "LONG" and cross_down
            ) or (
                self.reversal_direction == "SHORT" and cross_up
            )
            if opposite_cross:
                self._clear_reversal()
                self._record_cross(
                    "DOWN" if cross_down else "UP",
                    tick.timestamp_ms,
                    "BLOCKED",
                    "REVERSAL_CANCELED_OPPOSITE_CROSS",
                )
                return None
            reversal_signal = self._confirmed_reversal_signal(
                tick,
                atr,
                bar_start,
                emit_signals=emit_signals,
                has_pending_order=has_pending_order,
            )
            if reversal_signal is not None:
                return reversal_signal
            if self.reversal_direction is not None:
                if (
                    self.reversal_eligible_bar_ms is not None
                    and bar_start > self.reversal_eligible_bar_ms
                ):
                    self._clear_reversal()
                else:
                    return None

        if cross_up:
            blocked_reason = _common_block_reason(
                emit_signals, has_pending_order, self.action_this_bar
            )
            reduce_only = allow_short and has_position and is_short
            if blocked_reason is None and has_position and not reduce_only:
                blocked_reason = "ALREADY_LONG"
            if blocked_reason is None and not has_position:
                blocked_reason = self._entry_filter_reason()
            if blocked_reason is None:
                return self._emit_signal(
                    tick,
                    atr,
                    bar_start,
                    Side.BUY,
                    "price_crossed_above_atr_stop",
                    reduce_only=reduce_only,
                    reversal_after="LONG" if reduce_only else None,
                )
            self._record_cross("UP", tick.timestamp_ms, "BLOCKED", blocked_reason)

        if cross_down:
            blocked_reason = _common_block_reason(
                emit_signals, has_pending_order, self.action_this_bar
            )
            reduce_only = has_position and not is_short
            if blocked_reason is None and has_position and is_short:
                blocked_reason = "ALREADY_SHORT"
            if blocked_reason is None and not has_position and not allow_short:
                blocked_reason = "NO_POSITION"
            if blocked_reason is None and not has_position:
                blocked_reason = self._entry_filter_reason()
            if blocked_reason is None:
                return self._emit_signal(
                    tick,
                    atr,
                    bar_start,
                    Side.SELL,
                    "price_crossed_below_atr_stop",
                    reduce_only=reduce_only,
                    reversal_after="SHORT" if allow_short and reduce_only else None,
                )
            self._record_cross("DOWN", tick.timestamp_ms, "BLOCKED", blocked_reason)

        startup_signal = self._startup_alignment_signal(
            tick,
            atr,
            bar_start,
            has_position=has_position,
            has_pending_order=has_pending_order,
            allow_short=allow_short,
            emit_signals=emit_signals,
        )
        if startup_signal is not None:
            return startup_signal
        return None

    def _startup_alignment_signal(
        self,
        tick: Tick,
        atr: Decimal,
        bar_start: int,
        *,
        has_position: bool,
        has_pending_order: bool,
        allow_short: bool,
        emit_signals: bool,
    ) -> StrategySignal | None:
        """Align a fresh account once when it starts inside an established trend."""
        if self.startup_alignment_checked or not emit_signals:
            return None
        self.startup_alignment_checked = True
        if has_position or has_pending_order:
            return None
        if self._entry_filter_reason() is not None or self.trailing_stop is None:
            return None
        if tick.price > self.trailing_stop:
            side = Side.BUY
        elif allow_short and tick.price < self.trailing_stop:
            side = Side.SELL
        else:
            return None
        return self._emit_signal(
            tick,
            atr,
            bar_start,
            side,
            "startup_trend_alignment",
            reduce_only=False,
        )

    def on_fill(self, timestamp_ms: int, *, filled: bool) -> None:
        """Apply the one-action lock to the actual fill bar and arm delayed reversal."""
        if not filled:
            self._clear_reversal()
            return
        fill_bar_start = timestamp_ms // self.bar_ms * self.bar_ms
        self.last_action_bar_start_ms = fill_bar_start
        if self.current_bar is not None and self.current_bar.start_ms == fill_bar_start:
            self.action_this_bar = True
        if self.reversal_direction is not None and self.reversal_eligible_bar_ms is None:
            self.reversal_eligible_bar_ms = fill_bar_start + self.bar_ms

    def on_manual_flatten(self, timestamp_ms: int) -> None:
        """Prevent an operator-initiated close from arming an automatic reversal."""
        fill_bar_start = timestamp_ms // self.bar_ms * self.bar_ms
        self.startup_alignment_checked = True
        self.action_this_bar = True
        self.flattened_this_bar = True
        self.bought_this_bar = False
        self.last_action_bar_start_ms = fill_bar_start
        self._clear_reversal()

    def _confirmed_reversal_signal(
        self,
        tick: Tick,
        atr: Decimal,
        bar_start: int,
        *,
        emit_signals: bool,
        has_pending_order: bool,
    ) -> StrategySignal | None:
        if self.reversal_eligible_bar_ms != bar_start or self.reversal_anchor is None:
            return None
        blocked_reason = _common_block_reason(
            emit_signals, has_pending_order, self.action_this_bar
        )
        if blocked_reason is None:
            blocked_reason = self._entry_filter_reason()
        threshold = atr * self.reversal_confirmation_atr
        confirmed = (
            self.reversal_direction == "LONG"
            and tick.price >= self.reversal_anchor + threshold
        ) or (
            self.reversal_direction == "SHORT"
            and tick.price <= self.reversal_anchor - threshold
        )
        if blocked_reason is not None or not confirmed:
            return None
        side = Side.BUY if self.reversal_direction == "LONG" else Side.SELL
        direction = self.reversal_direction.lower()
        self._clear_reversal()
        return self._emit_signal(
            tick,
            atr,
            bar_start,
            side,
            f"confirmed_{direction}_reversal",
            reduce_only=False,
        )

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
        self.startup_alignment_checked = True
        self.action_this_bar = True
        self.last_action_bar_start_ms = bar_start
        self.bought_this_bar = side is Side.BUY
        self.flattened_this_bar = side is Side.SELL
        if reversal_after is not None:
            self.reversal_direction = reversal_after
            self.reversal_anchor = tick.price
            self.reversal_eligible_bar_ms = None
        self._record_cross(
            "UP" if side is Side.BUY else "DOWN",
            tick.timestamp_ms,
            "BUY_SIGNAL" if side is Side.BUY else "SELL_SIGNAL",
            None,
        )
        return StrategySignal(
            side=side,
            reason=reason,
            signal_price=tick.price,
            trailing_stop=self.trailing_stop,
            atr=atr,
            bar_start_ms=bar_start,
            tick_id=tick.event_id,
            reduce_only=reduce_only,
        )

    def _entry_filter_reason(self) -> str | None:
        if self.last_trend_efficiency is None:
            return "TREND_FILTER_WARMING"
        if self.last_trend_efficiency < self.minimum_trend_efficiency:
            return "LOW_TREND_EFFICIENCY"
        return None

    def _clear_reversal(self) -> None:
        self.reversal_direction = None
        self.reversal_anchor = None
        self.reversal_eligible_bar_ms = None

    def _current_atr(self) -> Decimal | None:
        if self.current_bar is None:
            return None
        return wilder_atr([*self.completed_bars, self.current_bar], self.period)

    def _current_trend_efficiency(self) -> Decimal | None:
        if self.current_bar is None:
            return None
        closes = [
            bar.close
            for bar in [*self.completed_bars, self.current_bar][
                -(self.trend_efficiency_period + 1) :
            ]
        ]
        if len(closes) < self.trend_efficiency_period + 1:
            return None
        path = sum(
            (
                abs(current - previous)
                for previous, current in zip(closes, closes[1:], strict=False)
            ),
            Decimal("0"),
        )
        if path == 0:
            return Decimal("0")
        return abs(closes[-1] - closes[0]) / path

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
            action_this_bar=self.action_this_bar,
            trend_efficiency=self.last_trend_efficiency,
            trend_filter_passed=(
                self.last_trend_efficiency is not None
                and self.last_trend_efficiency >= self.minimum_trend_efficiency
            ),
            reversal_direction=self.reversal_direction,
            reversal_anchor=self.reversal_anchor,
            reversal_eligible_bar_ms=self.reversal_eligible_bar_ms,
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


def atr_profit_protection_signal(
    protection: ATRProfitProtection | None,
    strategy: ATRTickStrategy,
    tick: Tick,
    *,
    position_quantity: Decimal,
    entry_price: Decimal,
    has_pending_order: bool,
    emit_signals: bool,
) -> StrategySignal | None:
    """Update shared profit protection and emit a reduce-only exit when crossed."""
    atr = getattr(strategy, "last_atr", None)
    if protection is None or atr is None:
        return None
    protection.sync_position(
        quantity=position_quantity,
        entry_price=entry_price,
        current_atr=atr,
    )
    crossed_stop = protection.observe(
        tick.price,
        atr,
        action_locked=strategy.action_this_bar,
    )
    if (
        crossed_stop is None
        or not emit_signals
        or has_pending_order
        or position_quantity == 0
    ):
        return None
    return StrategySignal(
        side=Side.BUY if position_quantity < 0 else Side.SELL,
        reason="atr_profit_protection",
        signal_price=tick.price,
        trailing_stop=crossed_stop,
        atr=atr,
        bar_start_ms=tick.timestamp_ms // strategy.bar_ms * strategy.bar_ms,
        tick_id=tick.event_id,
        reduce_only=True,
    )


def _decimal_or_none(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _string_or_none(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _common_block_reason(
    emit_signals: bool,
    has_pending_order: bool,
    action_this_bar: bool,
) -> str | None:
    if not emit_signals:
        return "TRADING_PAUSED"
    if has_pending_order:
        return "ORDER_PENDING"
    if action_this_bar:
        return "ACTION_LOCKED_THIS_BAR"
    return None
