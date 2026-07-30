from decimal import Decimal

from mastermind_tick.engine import _decision_view
from mastermind_tick.strategy import StrategyView


def strategy_view(**overrides) -> StrategyView:
    values = {
        "ready": True,
        "atr": Decimal("2"),
        "trailing_stop": Decimal("100"),
        "price": Decimal("101"),
        "relation": "above",
        "bar_start_ms": 1_800_000,
        "bought_this_bar": False,
        "flattened_this_bar": False,
        "last_cross": None,
        "last_cross_at_ms": None,
        "last_cross_result": None,
        "last_cross_reason": None,
    }
    values.update(overrides)
    return StrategyView(**values)


def test_flat_account_above_stop_waits_for_a_fresh_cross() -> None:
    decision = _decision_view(
        strategy_view(),
        trading_enabled=True,
        has_position=False,
        has_pending_order=False,
        bar_ms=900_000,
    )

    assert decision["state"] == "WAITING_FOR_RESET"
    assert decision["reason"] == "PRICE_ALREADY_ABOVE_WITHOUT_FRESH_CROSS"
    assert decision["next_trigger"] == "PRICE_BELOW_THEN_CROSS_ABOVE"
    assert decision["bar_end_ms"] == 2_700_000
    assert decision["last_signal"] is None


def test_same_bar_sell_lock_is_reported_before_price_relation() -> None:
    decision = _decision_view(
        strategy_view(flattened_this_bar=True),
        trading_enabled=True,
        has_position=False,
        has_pending_order=False,
        bar_ms=900_000,
    )

    assert decision["state"] == "REENTRY_LOCKED"
    assert decision["reason"] == "SOLD_THIS_BAR"
    assert not decision["reentry_lock_open"]


def test_latest_order_is_exposed_as_cross_history_fallback() -> None:
    decision = _decision_view(
        strategy_view(),
        trading_enabled=True,
        has_position=False,
        has_pending_order=False,
        bar_ms=900_000,
        last_order={
            "side": "SELL",
            "status": "FILLED",
            "submitted_at_ms": 2_000_000,
            "reason": "price_crossed_below_atr_stop",
        },
    )

    assert decision["last_signal"] == {
        "side": "SELL",
        "status": "FILLED",
        "timestamp_ms": 2_000_000,
        "reason": "price_crossed_below_atr_stop",
    }
