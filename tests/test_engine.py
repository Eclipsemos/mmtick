import asyncio
from dataclasses import replace
from decimal import Decimal

from mastermind_tick.config import load_settings
from mastermind_tick.engine import InstrumentRuntime, PaperEngine, _decision_view
from mastermind_tick.models import Side, StrategySignal, Tick
from mastermind_tick.store import PaperStore
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


class SignalSequenceStrategy:
    def __init__(self) -> None:
        self.side = Side.BUY

    def on_tick(self, tick: Tick, **_kwargs) -> StrategySignal:
        signal = StrategySignal(
            side=self.side,
            reason=f"test_{self.side.value.lower()}",
            signal_price=tick.price,
            trailing_stop=Decimal("100"),
            atr=Decimal("2"),
            bar_start_ms=900_000,
            tick_id=tick.event_id,
        )
        self.side = Side.SELL
        return signal

    def view(self) -> StrategyView:
        return strategy_view(bought_this_bar=True)

    def runtime_state(self) -> dict:
        return {}


def test_buy_and_sell_fill_on_their_own_signal_ticks(tmp_path) -> None:
    settings = replace(load_settings("config/settings.toml"), database_path=tmp_path / "paper.db")
    instrument = settings.instruments[0]
    store = PaperStore(settings.database_path)
    store.ensure_account(instrument, settings.initial_cash, 1)
    engine = PaperEngine(settings, store)
    runtime = InstrumentRuntime(
        instrument=instrument,
        feed=object(),  # type: ignore[arg-type]
        strategy=SignalSequenceStrategy(),  # type: ignore[arg-type]
    )
    buy_tick = Tick("buy-tick", 1_000_000, Decimal("100"), Decimal("1"), "test")
    sell_tick = Tick("sell-tick", 1_001_000, Decimal("99"), Decimal("1"), "test")

    asyncio.run(engine._process_tick(runtime, buy_tick))
    assert Decimal(store.account(instrument.id)["quantity"]) > 0
    asyncio.run(engine._process_tick(runtime, sell_tick))

    fills = sorted(store.fills(instrument.id), key=lambda item: item["timestamp_ms"])
    assert [(item["side"], item["timestamp_ms"]) for item in fills] == [
        ("BUY", buy_tick.timestamp_ms),
        ("SELL", sell_tick.timestamp_ms),
    ]
    assert [item["source"] for item in fills] == ["test", "test"]
