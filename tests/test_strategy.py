from decimal import Decimal

import pytest

from mastermind_tick.models import Bar, Side, Tick
from mastermind_tick.strategy import ATRTickStrategy, wilder_atr


def bars(closes: list[float], spread: float = 0.5) -> list[Bar]:
    result = []
    for index, close in enumerate(closes):
        start = index * 900_000
        result.append(
            Bar(
                start_ms=start,
                end_ms=start + 899_999,
                open=Decimal(str(close)),
                high=Decimal(str(close + spread)),
                low=Decimal(str(close - spread)),
                close=Decimal(str(close)),
            )
        )
    return result


def tick(event_id: str, timestamp_ms: int, price: float) -> Tick:
    return Tick(
        event_id=event_id,
        timestamp_ms=timestamp_ms,
        price=Decimal(str(price)),
        quantity=Decimal("1"),
        source="test",
    )


def test_wilder_atr_matches_known_pine_values() -> None:
    values = bars([10, 9, 8, 9, 12])

    assert float(wilder_atr(values[:2], 2)) == pytest.approx(1.25)
    assert float(wilder_atr(values[:3], 2)) == pytest.approx(1.375)
    assert float(wilder_atr(values[:4], 2)) == pytest.approx(1.4375)
    assert float(wilder_atr(values, 2)) == pytest.approx(2.46875)


def test_cross_down_then_cross_up_is_locked_for_same_bar() -> None:
    strategy = ATRTickStrategy(period=2, multiplier=0.75, bar_minutes=15)
    strategy.bootstrap(bars([10, 9, 8, 9, 12]))
    next_bar = 5 * 900_000

    sell = strategy.on_tick(
        tick("sell", next_bar, 10), has_position=True, has_pending_order=False
    )
    assert sell is not None
    assert sell.side is Side.SELL
    assert strategy.flattened_this_bar

    blocked = strategy.on_tick(
        tick("blocked-buy", next_bar + 1_000, 13),
        has_position=False,
        has_pending_order=False,
    )
    assert blocked is None
    assert strategy.flattened_this_bar


def test_buy_and_sell_are_each_limited_to_one_signal_per_bar() -> None:
    strategy = ATRTickStrategy(period=2, multiplier=0.75, bar_minutes=15)
    strategy.bootstrap(bars([10, 9, 8]))
    next_bar = 3 * 900_000

    buy = strategy.on_tick(
        tick("buy", next_bar, 10), has_position=False, has_pending_order=False
    )
    assert buy is not None
    assert buy.side is Side.BUY

    sell = strategy.on_tick(
        tick("sell", next_bar + 1_000, 7), has_position=True, has_pending_order=False
    )
    assert sell is not None
    assert sell.side is Side.SELL

    assert (
        strategy.on_tick(
            tick("second-buy", next_bar + 2_000, 11),
            has_position=False,
            has_pending_order=False,
        )
        is None
    )


def test_paused_strategy_updates_line_without_consuming_trade_lock() -> None:
    strategy = ATRTickStrategy(period=2, multiplier=0.75, bar_minutes=15)
    strategy.bootstrap(bars([10, 9, 8]))
    next_bar = 3 * 900_000

    signal = strategy.on_tick(
        tick("paused-cross", next_bar, 10),
        has_position=False,
        has_pending_order=False,
        emit_signals=False,
    )

    assert signal is None
    assert not strategy.bought_this_bar
    assert not strategy.flattened_this_bar


def test_tick_for_last_completed_bar_is_not_aggregated_twice() -> None:
    history = bars([10, 9, 8, 9, 12])
    strategy = ATRTickStrategy(period=2, multiplier=0.75, bar_minutes=15)
    strategy.bootstrap(history)
    previous_stop = strategy.trailing_stop

    result = strategy.on_tick(
        tick("stale-close", history[-1].start_ms, 12),
        has_position=False,
        has_pending_order=False,
    )

    assert result is None
    assert strategy.current_bar is None
    assert strategy.trailing_stop == previous_stop


def test_stale_runtime_bar_does_not_override_fresher_warmup() -> None:
    history = bars([10, 9, 8, 9, 12])
    strategy = ATRTickStrategy(period=2, multiplier=0.75, bar_minutes=15)
    strategy.bootstrap(history)
    previous_stop = strategy.trailing_stop

    strategy.restore_runtime(
        {
            "previous_price": "1",
            "trailing_stop": "2",
            "last_atr": "3",
            "bought_this_bar": True,
            "flattened_this_bar": True,
            "current_bar": history[-1].as_dict(),
        }
    )

    assert strategy.trailing_stop == previous_stop
    assert strategy.current_bar is None
    assert not strategy.bought_this_bar
