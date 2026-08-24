from datetime import UTC, datetime
from decimal import Decimal

import pytest

from mastermind_tick.models import Bar, Side, Tick
from mastermind_tick.strategy import (
    ATRProfitProtection,
    ATRTickStrategy,
    SessionRecoveryReentry,
    wilder_atr,
)

BAR_MS = 900_000


def test_atr_profit_protection_activates_ratchets_and_restores() -> None:
    protection = ATRProfitProtection(2, 0.5)
    protection.open(
        entry_price=Decimal("100"), entry_atr=Decimal("2"), is_short=False
    )

    assert protection.observe(Decimal("103.99"), Decimal("2"), action_locked=False) is None
    assert not protection.active
    assert protection.observe(Decimal("104"), Decimal("2"), action_locked=False) is None
    assert protection.active
    assert protection.stop == Decimal("103.0")
    assert protection.observe(Decimal("105"), Decimal("2"), action_locked=False) is None
    assert protection.stop == Decimal("104.0")

    restored = ATRProfitProtection(2, 0.5)
    restored.restore_runtime(protection.runtime_state())

    assert restored.observe(Decimal("104"), Decimal("2"), action_locked=False) == Decimal(
        "104.0"
    )


def test_atr_profit_protection_is_symmetric_for_short() -> None:
    protection = ATRProfitProtection(2, 0.5)
    protection.open(
        entry_price=Decimal("100"), entry_atr=Decimal("2"), is_short=True
    )

    assert protection.observe(Decimal("96"), Decimal("2"), action_locked=False) is None
    assert protection.stop == Decimal("97.0")
    assert protection.observe(Decimal("95"), Decimal("2"), action_locked=False) is None
    assert protection.stop == Decimal("96.0")
    assert protection.observe(Decimal("96"), Decimal("2"), action_locked=False) == Decimal(
        "96.0"
    )


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


def test_session_reentry_waits_for_next_bar_and_restores_state() -> None:
    rule = SessionRecoveryReentry(0.5, 2, "0816_2130")
    exit_ms = int(datetime(2026, 8, 11, 8, 5, tzinfo=UTC).timestamp() * 1000)
    exit_bar_ms = exit_ms // BAR_MS * BAR_MS

    rule.capture_exit(exit_ms, Decimal("99"))
    rule.on_fill(
        filled=True,
        reduce_only=True,
        fill_price=Decimal("98"),
        timestamp_ms=exit_ms,
        bar_ms=BAR_MS,
    )
    restored = SessionRecoveryReentry(0.5, 2, "0816_2130")
    restored.restore_runtime(rule.runtime_state())

    assert restored.signal(
        tick("same-bar", exit_bar_ms + BAR_MS - 1, 100),
        atr=Decimal("2"),
        trend_efficiency=Decimal("0.3"),
        minimum_trend_efficiency=Decimal("0.25"),
        action_locked=False,
        has_position=False,
        has_pending_order=False,
        bar_ms=BAR_MS,
    ) is None
    signal = restored.signal(
        tick("recovered", exit_bar_ms + BAR_MS, 100),
        atr=Decimal("2"),
        trend_efficiency=Decimal("0.3"),
        minimum_trend_efficiency=Decimal("0.25"),
        action_locked=False,
        has_position=False,
        has_pending_order=False,
        bar_ms=BAR_MS,
    )

    assert signal is not None
    assert signal.reason == "session_recovery_reentry"
    assert signal.trailing_stop == Decimal("99")
    assert restored.signal_count == 1


def warmed_strategy() -> ATRTickStrategy:
    strategy = ATRTickStrategy(
        period=2,
        multiplier=0.75,
        bar_minutes=15,
        trend_efficiency_period=2,
        minimum_trend_efficiency=0,
    )
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
    assert signal.signal_at_ms is None
    assert signal.tick_id == "buy"
    assert strategy.bought_this_bar
    assert strategy.last_cross_result == "BUY_SIGNAL"


def test_startup_alignment_opens_long_inside_an_established_uptrend() -> None:
    strategy = warmed_strategy()
    strategy.previous_price = Decimal("12")
    strategy.trailing_stop = Decimal("10")

    signal = strategy.on_tick(
        tick("startup-long", 3 * BAR_MS, 12),
        has_position=False,
        has_pending_order=False,
    )

    assert signal is not None
    assert signal.side is Side.BUY
    assert signal.reason == "startup_trend_alignment"
    assert signal.reduce_only is False
    assert strategy.startup_alignment_checked is True


def test_startup_alignment_opens_short_for_futures_downtrend() -> None:
    strategy = warmed_strategy()
    strategy.previous_price = Decimal("8")
    strategy.trailing_stop = Decimal("10")

    signal = strategy.on_tick(
        tick("startup-short", 3 * BAR_MS, 8),
        has_position=False,
        has_pending_order=False,
        allow_short=True,
    )

    assert signal is not None
    assert signal.side is Side.SELL
    assert signal.reason == "startup_trend_alignment"
    assert signal.reduce_only is False


def test_startup_alignment_never_opens_a_spot_short() -> None:
    strategy = warmed_strategy()
    strategy.previous_price = Decimal("8")
    strategy.trailing_stop = Decimal("10")

    signal = strategy.on_tick(
        tick("spot-below", 3 * BAR_MS, 8),
        has_position=False,
        has_pending_order=False,
    )

    assert signal is None
    assert strategy.startup_alignment_checked is True


def test_long_only_down_cross_closes_long_without_arming_short_reversal() -> None:
    strategy = warmed_strategy()
    strategy.previous_price = Decimal("12")
    strategy.trailing_stop = Decimal("10")
    strategy.startup_alignment_checked = True

    signal = strategy.on_tick(
        tick("long-only-close", 3 * BAR_MS, 9),
        has_position=True,
        has_pending_order=False,
        allow_short=False,
        is_short=False,
    )

    assert signal is not None
    assert signal.side is Side.SELL
    assert signal.reduce_only
    assert strategy.reversal_direction is None


def test_long_only_down_cross_does_not_open_short_while_flat() -> None:
    strategy = warmed_strategy()
    strategy.previous_price = Decimal("12")
    strategy.trailing_stop = Decimal("10")
    strategy.startup_alignment_checked = True

    signal = strategy.on_tick(
        tick("long-only-flat", 3 * BAR_MS, 9),
        has_position=False,
        has_pending_order=False,
        allow_short=False,
        is_short=False,
    )

    assert signal is None


def test_paused_startup_alignment_waits_until_trading_resumes() -> None:
    strategy = warmed_strategy()
    strategy.previous_price = Decimal("12")
    strategy.trailing_stop = Decimal("10")
    start = 3 * BAR_MS

    assert strategy.on_tick(
        tick("paused-startup", start, 12),
        has_position=False,
        has_pending_order=False,
        emit_signals=False,
    ) is None
    assert strategy.startup_alignment_checked is False
    signal = strategy.on_tick(
        tick("resumed-startup", start + 1, 12),
        has_position=False,
        has_pending_order=False,
    )

    assert signal is not None
    assert signal.reason == "startup_trend_alignment"


def test_down_cross_then_up_cross_is_locked_for_same_bar() -> None:
    strategy = ATRTickStrategy(
        period=2,
        multiplier=0.75,
        bar_minutes=15,
        trend_efficiency_period=2,
        minimum_trend_efficiency=0,
    )
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
    assert strategy.last_cross_reason == "ACTION_LOCKED_THIS_BAR"


def test_futures_down_cross_opens_short_from_flat() -> None:
    strategy = ATRTickStrategy(
        period=2,
        multiplier=0.75,
        bar_minutes=15,
        trend_efficiency_period=2,
        minimum_trend_efficiency=0,
    )
    strategy.bootstrap(bars([10, 9, 8, 9, 12]))
    start = 5 * BAR_MS

    signal = strategy.on_tick(
        tick("short", start, 10),
        has_position=False,
        has_pending_order=False,
        allow_short=True,
    )

    assert signal is not None
    assert signal.side is Side.SELL
    assert strategy.flattened_this_bar


def test_futures_up_cross_reverses_short_to_long() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS

    signal = strategy.on_tick(
        tick("reverse-long", start, 10),
        has_position=True,
        has_pending_order=False,
        allow_short=True,
        is_short=True,
    )

    assert signal is not None
    assert signal.side is Side.BUY
    assert signal.reduce_only is True


def test_futures_reversal_closes_then_opens_short_after_next_bar_confirmation() -> None:
    strategy = ATRTickStrategy(
        period=2,
        multiplier=0.75,
        bar_minutes=15,
        trend_efficiency_period=2,
        minimum_trend_efficiency=0,
        reversal_confirmation_atr=0.25,
    )
    strategy.bootstrap(bars([10, 9, 8, 9, 12]))
    close_bar = 5 * BAR_MS

    close = strategy.on_tick(
        tick("close-long", close_bar, 10),
        has_position=True,
        has_pending_order=False,
        allow_short=True,
    )
    assert close is not None
    assert close.side is Side.SELL
    assert close.reduce_only is True
    assert strategy.reversal_direction == "SHORT"

    strategy.on_fill(close_bar + 1, filled=True)
    assert strategy.reversal_eligible_bar_ms == close_bar + BAR_MS
    assert strategy.on_tick(
        tick("same-bar-drop", close_bar + 2, 8),
        has_position=False,
        has_pending_order=False,
        allow_short=True,
    ) is None

    eligible_bar = close_bar + BAR_MS
    assert strategy.on_tick(
        tick("next-bar-wait", eligible_bar, 10),
        has_position=False,
        has_pending_order=False,
        allow_short=True,
    ) is None
    confirmed = strategy.on_tick(
        tick("next-bar-confirm", eligible_bar + 1, 8),
        has_position=False,
        has_pending_order=False,
        allow_short=True,
    )

    assert confirmed is not None
    assert confirmed.side is Side.SELL
    assert confirmed.reason == "confirmed_short_reversal"
    assert confirmed.reduce_only is False
    assert strategy.reversal_direction is None


def test_futures_reversal_confirmation_expires_after_the_next_bar() -> None:
    strategy = warmed_strategy()
    close_bar = 3 * BAR_MS
    close = strategy.on_tick(
        tick("close-short", close_bar, 10),
        has_position=True,
        has_pending_order=False,
        allow_short=True,
        is_short=True,
    )
    assert close is not None and close.reduce_only
    strategy.on_fill(close_bar + 1, filled=True)

    eligible_bar = close_bar + BAR_MS
    assert strategy.on_tick(
        tick("unconfirmed", eligible_bar, 10),
        has_position=False,
        has_pending_order=False,
        allow_short=True,
    ) is None
    assert strategy.reversal_direction == "LONG"
    assert strategy.on_tick(
        tick("expired", eligible_bar + BAR_MS, 10),
        has_position=False,
        has_pending_order=False,
        allow_short=True,
    ) is None
    assert strategy.reversal_direction is None


def test_low_trend_efficiency_blocks_a_new_position() -> None:
    strategy = ATRTickStrategy(
        period=2,
        multiplier=0.75,
        bar_minutes=15,
        trend_efficiency_period=2,
        minimum_trend_efficiency=0.5,
    )
    strategy.bootstrap(bars([10, 11, 10]))
    strategy.previous_price = Decimal("10")
    strategy.trailing_stop = Decimal("11")

    signal = strategy.on_tick(
        tick("choppy-entry", 3 * BAR_MS, 12),
        has_position=False,
        has_pending_order=False,
    )

    assert signal is None
    assert strategy.last_trend_efficiency == Decimal("1") / Decimal("3")
    assert strategy.last_cross_reason == "LOW_TREND_EFFICIENCY"
    assert strategy.action_this_bar is False


def test_low_trend_efficiency_does_not_block_an_exit() -> None:
    strategy = ATRTickStrategy(
        period=2,
        multiplier=0.75,
        bar_minutes=15,
        trend_efficiency_period=2,
        minimum_trend_efficiency=0.5,
    )
    strategy.bootstrap(bars([10, 9, 12]))
    strategy.previous_price = Decimal("12")
    strategy.trailing_stop = Decimal("11")

    signal = strategy.on_tick(
        tick("choppy-exit", 3 * BAR_MS, 10),
        has_position=True,
        has_pending_order=False,
    )

    assert strategy.last_trend_efficiency == Decimal("0.2")
    assert signal is not None
    assert signal.side is Side.SELL


def test_only_one_trade_action_is_allowed_per_bar() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS

    buy = strategy.on_tick(
        tick("buy", start, 10), has_position=False, has_pending_order=False
    )
    assert buy is not None and buy.side is Side.BUY

    sell = strategy.on_tick(
        tick("sell", start + 1_000, 7), has_position=True, has_pending_order=False
    )
    assert sell is None
    assert strategy.last_cross_reason == "ACTION_LOCKED_THIS_BAR"

    second_buy = strategy.on_tick(
        tick("second-buy", start + 2_000, 11),
        has_position=False,
        has_pending_order=False,
    )
    assert second_buy is None
    assert strategy.last_cross_reason == "ACTION_LOCKED_THIS_BAR"


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


def test_realtime_stop_moves_recursively_after_cross_check() -> None:
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
    assert strategy.trailing_stop == Decimal("9.984375")
    assert strategy.previous_price == Decimal("10")


def test_current_bar_is_built_from_received_ticks() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS

    strategy.on_tick(
        tick("first-live-tick", start, 10),
        has_position=False,
        has_pending_order=False,
    )

    assert strategy.current_bar is not None
    assert strategy.current_bar.open == Decimal("10")
    assert strategy.current_bar.high == Decimal("10")
    assert strategy.current_bar.low == Decimal("10")
    assert strategy.current_bar.close == Decimal("10")
    assert strategy.last_atr == Decimal("1.6875")
    assert strategy.trailing_stop == Decimal("8.734375")


def test_next_tick_bar_commits_locally_synthesized_ohlc() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS
    strategy.on_tick(
        tick("local-high", start, 20),
        has_position=False,
        has_pending_order=False,
    )
    strategy.on_tick(
        tick("next-bar", start + BAR_MS, 8),
        has_position=False,
        has_pending_order=False,
    )

    assert strategy.completed_bars[-1].open == Decimal("20")
    assert strategy.completed_bars[-1].high == Decimal("20")
    assert strategy.completed_bars[-1].low == Decimal("20")
    assert strategy.completed_bars[-1].close == Decimal("20")
    assert strategy.current_bar is not None
    assert strategy.current_bar.start_ms == start + BAR_MS


def test_runtime_state_persists_tick_relation_and_locks() -> None:
    strategy = warmed_strategy()
    start = 3 * BAR_MS
    strategy.on_tick(
        tick("cross", start, 12),
        has_position=False,
        has_pending_order=False,
    )

    state = strategy.runtime_state()

    assert state["algorithm_version"] == "atr_tick_v3_startup_alignment"
    assert state["period"] == 2
    assert state["multiplier"] == "0.75"
    assert state["bar_ms"] == BAR_MS
    assert state["bought_this_bar"] is True
    assert state["action_this_bar"] is True
    assert state["trend_efficiency_period"] == 2
    assert state["minimum_trend_efficiency"] == "0"
    assert state["reversal_confirmation_atr"] == "0.25"
    assert state["startup_alignment_checked"] is True
    assert state["trailing_stop"] == "9.984375"

    restored = warmed_strategy()
    restored.restore_runtime(state)
    assert restored.current_bar is not None
    assert restored.trailing_stop == strategy.trailing_stop
    assert restored.startup_alignment_checked is True


def test_restored_runtime_does_not_repeat_startup_alignment() -> None:
    original = warmed_strategy()
    original.previous_price = Decimal("8")
    original.trailing_stop = Decimal("10")
    original.on_tick(
        tick("initial-check", 3 * BAR_MS, 8),
        has_position=False,
        has_pending_order=False,
    )
    restored = warmed_strategy()
    restored.restore_runtime(original.runtime_state())
    restored.previous_price = Decimal("12")
    restored.trailing_stop = Decimal("10")

    signal = restored.on_tick(
        tick("would-align-again", 3 * BAR_MS + 1, 12),
        has_position=False,
        has_pending_order=False,
    )

    assert signal is None
    assert restored.startup_alignment_checked is True


def test_runtime_state_persists_pending_reversal() -> None:
    strategy = warmed_strategy()
    close_bar = 3 * BAR_MS
    signal = strategy.on_tick(
        tick("close-short", close_bar, 10),
        has_position=True,
        has_pending_order=False,
        allow_short=True,
        is_short=True,
    )
    assert signal is not None and signal.reduce_only
    strategy.on_fill(close_bar + 1, filled=True)

    restored = warmed_strategy()
    restored.restore_runtime(strategy.runtime_state())

    assert restored.reversal_direction == "LONG"
    assert restored.reversal_anchor == Decimal("10")
    assert restored.reversal_eligible_bar_ms == close_bar + BAR_MS


def test_runtime_state_from_different_parameters_is_not_restored() -> None:
    original = warmed_strategy()
    original.on_tick(
        tick("cross", 3 * BAR_MS, 12),
        has_position=False,
        has_pending_order=False,
    )
    changed = ATRTickStrategy(period=3, multiplier=1.25, bar_minutes=15)
    changed.bootstrap(bars([10, 9, 8, 9]))

    changed.restore_runtime(original.runtime_state())

    assert changed.current_bar is None
    assert changed.period == 3
    assert changed.multiplier == Decimal("1.25")


def test_incomplete_runtime_state_does_not_clear_warmed_indicator() -> None:
    incomplete = ATRTickStrategy(
        period=2,
        multiplier=0.75,
        bar_minutes=15,
        trend_efficiency_period=2,
        minimum_trend_efficiency=0,
    ).runtime_state()
    restored = warmed_strategy()
    expected_previous_price = restored.previous_price
    expected_stop = restored.trailing_stop
    expected_atr = restored.last_atr

    restored.restore_runtime(incomplete)

    assert restored.previous_price == expected_previous_price
    assert restored.trailing_stop == expected_stop
    assert restored.last_atr == expected_atr


def test_stale_runtime_bar_keeps_last_tick_stop_for_next_bar() -> None:
    original = warmed_strategy()
    original.on_tick(
        tick("cross", 3 * BAR_MS, 12),
        has_position=False,
        has_pending_order=False,
    )
    state = original.runtime_state()
    restored = ATRTickStrategy(period=2, multiplier=0.75, bar_minutes=15)
    restored.bootstrap([*bars([10, 9, 8]), Bar.from_dict(state["current_bar"])])

    restored.restore_runtime(state)

    assert restored.current_bar is None
    assert restored.previous_price == original.previous_price
    assert restored.trailing_stop == original.trailing_stop
    assert restored.last_atr == original.last_atr
    assert restored.bought_this_bar is False


def test_other_algorithm_state_is_not_restored_into_original_strategy() -> None:
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
