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


def form_up_cross_bar(strategy: ATRTickStrategy) -> Bar:
    start = 3 * BAR_MS
    assert strategy.on_tick(
        tick("open", start, 10), has_position=False, has_pending_order=False
    ) is None
    assert strategy.on_tick(
        tick("intrabar-down", start + 300_000, 7),
        has_position=False,
        has_pending_order=False,
    ) is None
    assert strategy.on_tick(
        tick("close", start + BAR_MS - 2, 10),
        has_position=False,
        has_pending_order=False,
    ) is None
    assert strategy.current_bar is not None
    return Bar.from_dict(strategy.current_bar.as_dict())


def test_wilder_atr_matches_known_pine_values() -> None:
    values = bars([10, 9, 8, 9, 12])

    assert float(wilder_atr(values[:2], 2)) == pytest.approx(1.25)
    assert float(wilder_atr(values[:3], 2)) == pytest.approx(1.375)
    assert float(wilder_atr(values[:4], 2)) == pytest.approx(1.4375)
    assert float(wilder_atr(values, 2)) == pytest.approx(2.46875)


def test_intrabar_crosses_never_emit_a_signal() -> None:
    strategy = warmed_strategy()
    official_bar = form_up_cross_bar(strategy)

    assert strategy.current_bar is not None
    assert strategy.current_bar.start_ms == official_bar.start_ms
    assert strategy.last_cross is None


def test_official_close_confirms_up_cross_without_waiting_for_a_trade_tick() -> None:
    strategy = warmed_strategy()
    official_bar = form_up_cross_bar(strategy)

    signal = strategy.on_bar_close(
        official_bar,
        has_position=False,
        has_pending_order=False,
    )

    assert signal is not None
    assert signal.side is Side.BUY
    assert signal.reason == "close_crossed_above_atr_stop"
    assert signal.signal_price == Decimal("10")
    assert signal.trailing_stop == Decimal("8.359375")
    assert signal.atr == Decimal("2.1875")
    assert signal.bar_start_ms == official_bar.start_ms
    assert signal.signal_at_ms == official_bar.end_ms
    assert signal.tick_id == f"bar-close:{official_bar.end_ms}"
    assert strategy.last_cross == "UP"
    assert strategy.last_cross_result == "BUY_SIGNAL"


def test_realtime_atr_and_stop_continue_to_update_on_every_tick() -> None:
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
        has_position=False,
        has_pending_order=False,
    )

    assert strategy.last_atr == Decimal("2.6875")
    assert strategy.trailing_stop == Decimal("7.984375")
    assert strategy.last_cross is None


def test_close_confirmed_down_cross_sells_on_following_bar() -> None:
    strategy = warmed_strategy()
    up_bar = form_up_cross_bar(strategy)
    buy = strategy.on_bar_close(
        up_bar,
        has_position=False,
        has_pending_order=False,
    )
    assert buy is not None and buy.side is Side.BUY

    assert strategy.on_tick(
        tick("down-open", up_bar.start_ms + BAR_MS, 11),
        has_position=True,
        has_pending_order=False,
    ) is None
    assert strategy.on_tick(
        tick("down-close", up_bar.start_ms + BAR_MS + 600_000, 7),
        has_position=True,
        has_pending_order=False,
    ) is None
    assert strategy.current_bar is not None
    official_down_bar = Bar.from_dict(strategy.current_bar.as_dict())
    sell = strategy.on_bar_close(
        official_down_bar,
        has_position=True,
        has_pending_order=False,
    )

    assert sell is not None
    assert sell.side is Side.SELL
    assert sell.signal_price == Decimal("7")
    assert sell.trailing_stop == Decimal("9.3203125")
    assert sell.signal_at_ms == official_down_bar.end_ms
    assert strategy.last_cross == "DOWN"
    assert strategy.last_cross_result == "SELL_SIGNAL"


def test_position_rules_block_close_confirmed_crosses() -> None:
    strategy = warmed_strategy()
    official_bar = form_up_cross_bar(strategy)

    signal = strategy.on_bar_close(
        official_bar,
        has_position=True,
        has_pending_order=False,
    )

    assert signal is None
    assert strategy.last_cross == "UP"
    assert strategy.last_cross_result == "BLOCKED"
    assert strategy.last_cross_reason == "ALREADY_LONG"


def test_paused_strategy_records_but_does_not_emit_confirmed_cross() -> None:
    strategy = warmed_strategy()
    official_bar = form_up_cross_bar(strategy)

    signal = strategy.on_bar_close(
        official_bar,
        has_position=False,
        has_pending_order=False,
        emit_signals=False,
    )

    assert signal is None
    assert strategy.last_cross_result == "BLOCKED"
    assert strategy.last_cross_reason == "TRADING_PAUSED"


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
    assert strategy.last_cross is None


def test_open_bar_survives_restart_and_confirms_only_at_next_open() -> None:
    original = warmed_strategy()
    official_bar = form_up_cross_bar(original)
    state = original.runtime_state()

    restored = warmed_strategy()
    restored.restore_runtime(state)
    assert restored.runtime_state()["pine_state_version"] == 5
    assert restored.last_cross is None

    signal = restored.on_bar_close(
        official_bar,
        has_position=False,
        has_pending_order=False,
    )

    assert signal is not None
    assert signal.side is Side.BUY
    assert signal.signal_at_ms == official_bar.end_ms


def test_official_bar_replaces_local_tick_ohlc_for_atr_and_signal() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS
    strategy.on_tick(
        tick("local-high", start, 20),
        has_position=False,
        has_pending_order=False,
    )
    official_bar = Bar(
        start,
        start + BAR_MS - 1,
        Decimal("10"),
        Decimal("10"),
        Decimal("7"),
        Decimal("10"),
    )

    signal = strategy.on_bar_close(
        official_bar,
        has_position=False,
        has_pending_order=False,
    )

    assert signal is not None
    assert signal.atr == Decimal("2.1875")
    assert signal.trailing_stop == Decimal("8.359375")


def test_legacy_tick_cross_metadata_is_not_restored() -> None:
    strategy = warmed_strategy()
    strategy.restore_runtime(
        {
            "pine_state_version": 3,
            "last_cross": "UP",
            "last_cross_at_ms": 123,
            "last_cross_result": "BUY_SIGNAL",
        }
    )

    assert strategy.last_cross is None
