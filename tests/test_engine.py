import asyncio
from dataclasses import replace
from decimal import Decimal

from mastermind_tick.config import load_settings
from mastermind_tick.engine import InstrumentRuntime, PaperEngine, _decision_view
from mastermind_tick.models import Bar, Tick
from mastermind_tick.store import PaperStore
from mastermind_tick.strategy import ATRTickStrategy, StrategyView


def strategy_view(**overrides) -> StrategyView:
    values = {
        "ready": True,
        "atr": Decimal("2"),
        "trailing_stop": Decimal("100"),
        "price": Decimal("99"),
        "relation": "below",
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


class OfficialBarFeed:
    source_name = "test_trades"
    kline_source_name = "test_kline_rest"

    def __init__(self, bars: list[Bar]):
        self.bars = bars

    async def official_bars(self, start_ms: int, end_ms: int) -> list[Bar]:
        return [bar for bar in self.bars if start_ms <= bar.start_ms <= end_ms]


class FailingOfficialBarFeed(OfficialBarFeed):
    async def official_bars(self, start_ms: int, end_ms: int) -> list[Bar]:
        raise ConnectionError("REST unavailable")


def test_flat_account_below_stop_is_armed_for_tick_cross() -> None:
    decision = _decision_view(
        strategy_view(),
        trading_enabled=True,
        has_position=False,
        has_pending_order=False,
        bar_ms=900_000,
    )

    assert decision["state"] == "ARMED_FOR_BUY"
    assert decision["next_trigger"] == "PRICE_CROSS_ABOVE"
    assert decision["signal_confirmation"] == "TICK"
    assert decision["fill_timing"] == "NEXT_TICK"


def test_long_account_waits_for_realtime_down_cross() -> None:
    decision = _decision_view(
        strategy_view(relation="above", price=Decimal("101")),
        trading_enabled=True,
        has_position=True,
        has_pending_order=False,
        bar_ms=900_000,
    )

    assert decision["state"] == "HOLDING_LONG"
    assert decision["reason"] == "PRICE_ABOVE_STOP"
    assert decision["next_trigger"] == "PRICE_CROSS_BELOW"


def test_short_account_waits_for_realtime_up_cross() -> None:
    decision = _decision_view(
        strategy_view(relation="below", price=Decimal("99")),
        trading_enabled=True,
        has_position=True,
        has_pending_order=False,
        bar_ms=900_000,
        allow_short=True,
        is_short=True,
    )

    assert decision["state"] == "HOLDING_SHORT"
    assert decision["position_side"] == "SHORT"
    assert decision["next_trigger"] == "PRICE_CROSS_ABOVE"


def test_sell_lock_is_exposed_as_reentry_locked() -> None:
    decision = _decision_view(
        strategy_view(flattened_this_bar=True),
        trading_enabled=True,
        has_position=False,
        has_pending_order=False,
        bar_ms=900_000,
    )

    assert decision["state"] == "REENTRY_LOCKED"
    assert not decision["reentry_lock_open"]


def test_tick_signal_is_submitted_then_filled_on_next_tick(tmp_path) -> None:
    settings = replace(load_settings("config/settings.toml"), database_path=tmp_path / "paper.db")
    instrument = settings.instruments[0]
    store = PaperStore(settings.database_path)
    store.ensure_account(instrument, settings.initial_cash, 1)
    engine = PaperEngine(settings, store)
    strategy = ATRTickStrategy(period=2, multiplier=0.75, bar_minutes=15)
    strategy.bootstrap(
        [
            Bar(0, 899_999, Decimal("10"), Decimal("10.5"), Decimal("9.5"), Decimal("10")),
            Bar(900_000, 1_799_999, Decimal("9"), Decimal("9.5"), Decimal("8.5"), Decimal("9")),
            Bar(1_800_000, 2_699_999, Decimal("8"), Decimal("8.5"), Decimal("7.5"), Decimal("8")),
        ]
    )
    official = Bar(
        2_700_000,
        3_599_999,
        Decimal("8"),
        Decimal("8.5"),
        Decimal("7.5"),
        Decimal("8"),
    )
    runtime = InstrumentRuntime(
        instrument=instrument,
        feed=OfficialBarFeed([official]),  # type: ignore[arg-type]
        strategy=strategy,
    )
    crossing_tick = Tick("tick-cross", 3_600_000, Decimal("11"), Decimal("1"), "test")

    asyncio.run(engine._process_tick(runtime, crossing_tick))

    order = store.orders(instrument.id, 1)[0]
    assert order["reason"] == "price_crossed_above_atr_stop"
    assert order["submitted_tick_id"] == crossing_tick.event_id
    assert order["submitted_at_ms"] == crossing_tick.timestamp_ms
    assert order["status"] == "PENDING"
    assert store.fills(instrument.id) == []

    fill_tick = Tick("fill-tick", 3_601_000, Decimal("11.2"), Decimal("1"), "test")
    asyncio.run(engine._process_tick(runtime, fill_tick))

    filled_order = store.orders(instrument.id, 1)[0]
    fill = store.fills(instrument.id)[0]
    assert filled_order["filled_at_ms"] == fill_tick.timestamp_ms
    assert fill["timestamp_ms"] == fill_tick.timestamp_ms
    assert Decimal(fill["price"]) == Decimal("11.20560")

    asyncio.run(engine._process_official_close(runtime, official))
    stored = next(
        bar for bar in store.ohlcv_bars(instrument.id, 15, 2)
        if bar["start_ms"] == official.start_ms
    )
    assert stored["source"] == "test_kline_rest"


def test_rest_failure_does_not_block_original_tick_strategy(tmp_path) -> None:
    settings = replace(load_settings("config/settings.toml"), database_path=tmp_path / "paper.db")
    instrument = settings.instruments[0]
    store = PaperStore(settings.database_path)
    store.ensure_account(instrument, settings.initial_cash, 1)
    engine = PaperEngine(settings, store)
    strategy = ATRTickStrategy(period=2, multiplier=0.75, bar_minutes=15)
    strategy.bootstrap(
        [
            Bar(0, 899_999, Decimal("10"), Decimal("10.5"), Decimal("9.5"), Decimal("10")),
            Bar(900_000, 1_799_999, Decimal("9"), Decimal("9.5"), Decimal("8.5"), Decimal("9")),
            Bar(1_800_000, 2_699_999, Decimal("8"), Decimal("8.5"), Decimal("7.5"), Decimal("8")),
        ]
    )
    missing = Bar(
        2_700_000,
        3_599_999,
        Decimal("8"),
        Decimal("8.5"),
        Decimal("7.5"),
        Decimal("8"),
    )
    runtime = InstrumentRuntime(
        instrument=instrument,
        feed=FailingOfficialBarFeed([missing]),  # type: ignore[arg-type]
        strategy=strategy,
    )

    asyncio.run(engine._process_official_close(runtime, missing))
    asyncio.run(
        engine._process_tick(
            runtime,
            Tick("blocked-cross", 3_600_000, Decimal("11"), Decimal("1"), "test"),
        )
    )

    assert runtime.kline_validation == "REST_ERROR"
    order = store.orders(instrument.id, 1)[0]
    assert order["reason"] == "price_crossed_above_atr_stop"
    assert order["status"] == "PENDING"
