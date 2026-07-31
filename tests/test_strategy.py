from decimal import Decimal

import pytest

from mastermind_tick.models import Bar, Side, Tick
from mastermind_tick.strategy import ATRTickStrategy, wilder_atr

BAR_MS = 900_000


def bars(closes: list[float], spread: float = 0.5) -> list[Bar]:
    result = []
    for index, close in enumerate(closes):
        start = index * BAR_MS
        result.append(
            Bar(
                start_ms=start,
                end_ms=start + BAR_MS - 1,
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


def warmed_strategy() -> ATRTickStrategy:
    strategy = ATRTickStrategy(period=2, multiplier=0.75, bar_minutes=15)
    strategy.bootstrap(bars([10, 9, 8]))
    return strategy


def test_wilder_atr_matches_known_pine_values() -> None:
    values = bars([10, 9, 8, 9, 12])

    assert float(wilder_atr(values[:2], 2)) == pytest.approx(1.25)
    assert float(wilder_atr(values[:3], 2)) == pytest.approx(1.375)
    assert float(wilder_atr(values[:4], 2)) == pytest.approx(1.4375)
    assert float(wilder_atr(values, 2)) == pytest.approx(2.46875)


def test_tick_up_cross_emits_immediately_without_debounce() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS

    signal = strategy.on_tick(
        tick("buy", start, 10),
        has_position=False,
        has_pending_order=False,
    )

    assert signal is not None
    assert signal.side is Side.BUY
    assert signal.reason == "price_crossed_above_atr_stop"
    assert signal.signal_price == Decimal("10")
    assert signal.signal_at_ms == start
    assert signal.tick_id == "buy"
    assert strategy.bought_this_bar
    assert strategy.last_cross_result == "BUY_SIGNAL"


def test_down_cross_then_up_cross_is_locked_for_same_bar() -> None:
    strategy = ATRTickStrategy(period=2, multiplier=0.75, bar_minutes=15)
    strategy.bootstrap(bars([10, 9, 8, 9, 12]))
    start = 5 * BAR_MS

    sell = strategy.on_tick(
        tick("sell", start, 10), has_position=True, has_pending_order=False
    )
    assert sell is not None and sell.side is Side.SELL
    assert strategy.flattened_this_bar

    blocked = strategy.on_tick(
        tick("blocked-buy", start + 1_000, 13),
        has_position=False,
        has_pending_order=False,
    )
    assert blocked is None
    assert strategy.last_cross == "UP"
    assert strategy.last_cross_result == "BLOCKED"
    assert strategy.last_cross_reason == "REENTRY_LOCKED_THIS_BAR"


def test_buy_and_sell_are_each_limited_to_one_signal_per_bar() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS

    buy = strategy.on_tick(
        tick("buy", start, 10), has_position=False, has_pending_order=False
    )
    assert buy is not None and buy.side is Side.BUY

    sell = strategy.on_tick(
        tick("sell", start + 1_000, 7), has_position=True, has_pending_order=False
    )
    assert sell is not None and sell.side is Side.SELL

    second_buy = strategy.on_tick(
        tick("second-buy", start + 2_000, 11),
        has_position=False,
        has_pending_order=False,
    )
    assert second_buy is None
    assert strategy.last_cross_reason == "BUY_LOCKED_THIS_BAR"


def test_paused_strategy_updates_line_without_consuming_trade_lock() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS

    signal = strategy.on_tick(
        tick("paused-cross", start, 10),
        has_position=False,
        has_pending_order=False,
        emit_signals=False,
    )

    assert signal is None
    assert not strategy.bought_this_bar
    assert not strategy.flattened_this_bar
    assert strategy.last_cross_reason == "TRADING_PAUSED"


def test_realtime_stop_recalculates_from_previous_official_bar_on_every_tick() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS

    strategy.on_tick(
        tick("bar-high", start, 12),
        has_position=False,
        has_pending_order=False,
    )
    assert strategy.trailing_stop == Decimal("9.984375")

    strategy.on_tick(
        tick("pullback", start + 1_000, 10),
        has_position=True,
        has_pending_order=False,
    )

    assert strategy.last_atr == Decimal("2.6875")
    assert strategy.trailing_stop == Decimal("7.984375")
    assert strategy.previous_tick_above


def test_current_binance_bar_seeds_realtime_state_without_signal() -> None:
    strategy = warmed_strategy()
    current = Bar(
        start_ms=3 * BAR_MS,
        end_ms=4 * BAR_MS - 1,
        open=Decimal("8"),
        high=Decimal("12"),
        low=Decimal("7"),
        close=Decimal("10"),
    )

    strategy.seed_current_bar(current)

    assert strategy.last_atr == Decimal("3.1875")
    assert strategy.trailing_stop == Decimal("7.609375")
    assert strategy.previous_tick_above
    assert strategy.last_cross is None


def test_official_bar_replaces_local_ohlc_and_never_emits_close_signal() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS
    strategy.on_tick(
        tick("local-high", start, 20),
        has_position=False,
        has_pending_order=False,
    )
    official = Bar(
        start,
        start + BAR_MS - 1,
        Decimal("8"),
        Decimal("8.5"),
        Decimal("7.5"),
        Decimal("8"),
    )

    signal = strategy.on_bar_close(
        official,
        has_position=False,
        has_pending_order=False,
    )

    assert signal is None
    assert strategy.completed_bars[-1].as_dict() == official.as_dict()
    assert strategy.committed_atr == Decimal("1.1875")
    assert strategy.committed_stop == Decimal("8.890625")
    assert strategy.current_bar is None


def test_runtime_state_persists_tick_relation_and_locks() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS
    strategy.on_tick(
        tick("cross", start, 12),
        has_position=False,
        has_pending_order=False,
    )

    state = strategy.runtime_state()

    assert state["pine_state_version"] == 6
    assert state["previous_tick_above"] is True
    assert state["bought_this_bar"] is True
    assert state["committed_stop"] == "9.03125"


def test_close_confirmed_state_is_not_restored_into_tick_strategy() -> None:
    strategy = warmed_strategy()
    strategy.restore_runtime(
        {
            "pine_state_version": 5,
            "last_cross": "UP",
            "last_cross_at_ms": 123,
            "last_cross_result": "BUY_SIGNAL",
            "current_bar": Bar(
                3 * BAR_MS,
                4 * BAR_MS - 1,
                Decimal("8"),
                Decimal("12"),
                Decimal("7"),
                Decimal("10"),
            ).as_dict(),
        }
    )

    assert strategy.last_cross is None
    assert strategy.current_bar is None
