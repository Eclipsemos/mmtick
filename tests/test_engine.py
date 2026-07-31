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
        "price": Decimal("101"),
        "relation": "above",
        "bar_start_ms": 1_800_000,
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


def test_flat_account_waits_for_close_confirmed_up_cross() -> None:
    decision = _decision_view(
        strategy_view(),
        trading_enabled=True,
        has_position=False,
        has_pending_order=False,
        bar_ms=900_000,
    )

    assert decision["state"] == "WAITING_BAR_CLOSE"
    assert decision["reason"] == "WAITING_CLOSE_CONFIRMED_UP_CROSS"
    assert decision["next_trigger"] == "CLOSE_CROSS_ABOVE"
    assert decision["bar_end_ms"] == 2_700_000
    assert decision["signal_confirmation"] == "BAR_CLOSE"
    assert decision["fill_timing"] == "NEXT_BAR_FIRST_TICK"


def test_long_account_waits_for_close_confirmed_down_cross() -> None:
    decision = _decision_view(
        strategy_view(relation="below", price=Decimal("99")),
        trading_enabled=True,
        has_position=True,
        has_pending_order=False,
        bar_ms=900_000,
    )

    assert decision["state"] == "HOLDING_LONG"
    assert decision["reason"] == "WAITING_CLOSE_CONFIRMED_DOWN_CROSS"
    assert decision["next_trigger"] == "CLOSE_CROSS_BELOW"


def test_latest_order_is_exposed_as_confirmed_signal_history() -> None:
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
            "reason": "close_crossed_below_atr_stop",
        },
    )

    assert decision["last_signal"] == {
        "side": "SELL",
        "status": "FILLED",
        "timestamp_ms": 2_000_000,
        "reason": "close_crossed_below_atr_stop",
    }


def test_confirmed_signal_submits_at_close_and_fills_at_next_open(tmp_path) -> None:
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
    strategy.seed_current_bar(
        Bar(2_700_000, 3_599_999, Decimal("20"), Decimal("20"), Decimal("7"), Decimal("20"))
    )
    official_bar = Bar(
        2_700_000,
        3_599_999,
        Decimal("10"),
        Decimal("10"),
        Decimal("7"),
        Decimal("10"),
    )
    runtime = InstrumentRuntime(
        instrument=instrument,
        feed=OfficialBarFeed([official_bar]),  # type: ignore[arg-type]
        strategy=strategy,
    )
    next_open = Tick(
        "next-open",
        3_600_000,
        Decimal("11"),
        Decimal("1"),
        "test",
    )

    asyncio.run(engine._process_tick(runtime, next_open))

    order = store.orders(instrument.id, 1)[0]
    fill = store.fills(instrument.id)[0]
    assert order["submitted_at_ms"] == 3_599_999
    assert order["bar_start_ms"] == 2_700_000
    assert order["signal_price"] == "10"
    assert order["submitted_tick_id"] == "bar-close:3599999"
    assert order["filled_at_ms"] == next_open.timestamp_ms
    assert fill["timestamp_ms"] == next_open.timestamp_ms
    assert Decimal(fill["price"]) == Decimal("11.0055")
    assert fill["source"] == "test"
    assert order["atr"] == "2.1875"
    stored_bar = next(
        bar
        for bar in store.ohlcv_bars(instrument.id, 15, 2)
        if bar["start_ms"] == official_bar.start_ms
    )
    assert stored_bar["close"] == "10"
    assert stored_bar["source"] == "test_kline_rest"


def test_rest_failure_does_not_commit_stream_bar_or_emit_signal(tmp_path) -> None:
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
    stream_bar = Bar(
        2_700_000,
        3_599_999,
        Decimal("10"),
        Decimal("10"),
        Decimal("7"),
        Decimal("10"),
    )
    runtime = InstrumentRuntime(
        instrument=instrument,
        feed=FailingOfficialBarFeed([stream_bar]),  # type: ignore[arg-type]
        strategy=strategy,
    )

    asyncio.run(engine._process_official_close(runtime, stream_bar))

    assert runtime.kline_validation == "REST_ERROR"
    assert strategy.next_uncommitted_bar_start_ms == stream_bar.start_ms
    assert store.orders(instrument.id) == []
    assert not any(
        bar["start_ms"] == stream_bar.start_ms
        for bar in store.ohlcv_bars(instrument.id, 15)
    )
