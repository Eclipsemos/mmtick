import asyncio
from dataclasses import replace
from decimal import Decimal

from mastermind_tick.config import load_settings
from mastermind_tick.engine import InstrumentRuntime, PaperEngine, _decision_view
from mastermind_tick.models import Bar, Tick
from mastermind_tick.store import PaperStore
from mastermind_tick.strategy import ATRProfitProtection, ATRTickStrategy, StrategyView


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
        "action_this_bar": False,
        "trend_efficiency": Decimal("0.5"),
        "trend_filter_passed": True,
        "reversal_direction": None,
        "reversal_anchor": None,
        "reversal_eligible_bar_ms": None,
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

    async def funding_rates(self, start_ms: int, end_ms: int) -> list:
        return []


class FailingOfficialBarFeed(OfficialBarFeed):
    async def official_bars(self, start_ms: int, end_ms: int) -> list[Bar]:
        raise ConnectionError("REST unavailable")


class FailingWarmupFeed(OfficialBarFeed):
    async def history(self, limit: int) -> list[Bar]:
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


def test_action_lock_is_exposed_for_the_current_bar() -> None:
    decision = _decision_view(
        strategy_view(action_this_bar=True),
        trading_enabled=True,
        has_position=False,
        has_pending_order=False,
        bar_ms=900_000,
    )

    assert decision["state"] == "ACTION_LOCKED"
    assert not decision["action_lock_open"]


def test_tick_signal_is_submitted_then_filled_on_next_tick(tmp_path) -> None:
    settings = replace(load_settings("config/settings.toml"), database_path=tmp_path / "paper.db")
    instrument = settings.instruments[0]
    store = PaperStore(settings.database_path)
    store.ensure_account(instrument, settings.initial_cash, 1)
    engine = PaperEngine(settings, store)
    strategy = ATRTickStrategy(
        period=2,
        multiplier=0.75,
        bar_minutes=15,
        trend_efficiency_period=2,
        minimum_trend_efficiency=0,
    )
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
    assert Decimal(fill["price"]) == Decimal("11.20224")

    asyncio.run(engine._process_official_close(runtime, official))
    stored = next(
        bar
        for bar in store.ohlcv_bars(instrument.id, 15, 2)
        if bar["start_ms"] == official.start_ms
    )
    assert stored["source"] == "test_kline_rest"


def test_paper_futures_uses_and_persists_atr_profit_protection(tmp_path) -> None:
    settings = replace(load_settings("config/settings.toml"), database_path=tmp_path / "paper.db")
    instrument = next(item for item in settings.instruments if item.id == "soxl_perp")
    store = PaperStore(settings.database_path)
    store.ensure_account(instrument, settings.initial_cash, 1)
    with store.connection() as connection:
        connection.execute(
            "UPDATE accounts SET quantity = '1', average_price = '100' WHERE id = ?",
            (instrument.id,),
        )
    strategy = ATRTickStrategy(
        period=2,
        multiplier=0.75,
        bar_minutes=15,
        trend_efficiency_period=2,
        minimum_trend_efficiency=0,
    )
    strategy.bootstrap(
        [
            Bar(0, 899_999, Decimal("10"), Decimal("10.5"), Decimal("9.5"), Decimal("10")),
            Bar(900_000, 1_799_999, Decimal("9"), Decimal("9.5"), Decimal("8.5"), Decimal("9")),
            Bar(1_800_000, 2_699_999, Decimal("8"), Decimal("8.5"), Decimal("7.5"), Decimal("8")),
        ]
    )
    strategy.previous_price = Decimal("104")
    strategy.trailing_stop = Decimal("90")
    strategy.last_atr = Decimal("2")
    strategy.startup_alignment_checked = True
    protection = ATRProfitProtection(2, 0.5)
    protection.open(entry_price=Decimal("100"), entry_atr=Decimal("2"), is_short=False)
    protection.observe(Decimal("104"), Decimal("2"), action_locked=False)
    runtime = InstrumentRuntime(
        instrument=instrument,
        feed=OfficialBarFeed([]),  # type: ignore[arg-type]
        strategy=strategy,
        profit_protection=protection,
    )
    engine = PaperEngine(settings, store)
    exit_tick = Tick("profit-exit", 4 * 900_000, Decimal("103"), Decimal("1"), "test")

    asyncio.run(engine._process_tick(runtime, exit_tick))

    order = store.orders(instrument.id, 1)[0]
    assert order["reason"] == "atr_profit_protection"
    assert order["reduce_only"] == 1
    assert order["status"] == "PENDING"
    state = store.strategy_state(instrument.id)
    assert state is not None
    assert state["profit_protection"]["active"] is True
    assert state["profit_protection"]["stop"] == "103.0"


def test_shared_market_tick_is_stored_once_and_fanned_out_to_both_accounts(
    tmp_path,
) -> None:
    settings = replace(load_settings("config/settings.toml"), database_path=tmp_path / "paper.db")
    market = next(item for item in settings.instruments if item.id == "soxl_perp")
    long_only = next(item for item in settings.instruments if item.id == "soxl_perp_long")
    store = PaperStore(settings.database_path)
    engine = PaperEngine(settings, store)
    feed = OfficialBarFeed([])
    bars = [
        Bar(
            index * 900_000,
            (index + 1) * 900_000 - 1,
            Decimal(100 + index),
            Decimal(102 + index),
            Decimal(99 + index),
            Decimal(101 + index),
        )
        for index in range(settings.strategy.atr_period)
    ]
    for instrument in (market, long_only):
        store.ensure_account(instrument, settings.initial_cash, 1)
        strategy = ATRTickStrategy(
            period=settings.strategy.atr_period,
            multiplier=settings.strategy.atr_multiplier,
            bar_minutes=settings.strategy.bar_minutes,
            trend_efficiency_period=settings.strategy.trend_efficiency_period,
            minimum_trend_efficiency=settings.strategy.minimum_trend_efficiency,
            reversal_confirmation_atr=settings.strategy.reversal_confirmation_atr,
        )
        strategy.bootstrap(bars)
        engine.runtimes[instrument.id] = InstrumentRuntime(
            instrument=instrument,
            feed=feed,  # type: ignore[arg-type]
            strategy=strategy,
        )
    tick = Tick("shared-tick", 30 * 900_000, Decimal("130"), Decimal("1"), "test")

    asyncio.run(engine._process_market_tick(engine.runtimes[market.id], tick))

    assert len(store.agg_trades(market.id, 10)) == 1
    assert store.agg_trades(long_only.id, 10) == []
    assert engine.runtimes[market.id].last_tick == tick
    assert engine.runtimes[long_only.id].last_tick == tick
    assert store.equity(market.id, 1)[0]["timestamp_ms"] == tick.timestamp_ms
    assert store.equity(long_only.id, 1)[0]["timestamp_ms"] == tick.timestamp_ms


def test_rest_failure_does_not_block_original_tick_strategy(tmp_path) -> None:
    settings = replace(load_settings("config/settings.toml"), database_path=tmp_path / "paper.db")
    instrument = settings.instruments[0]
    store = PaperStore(settings.database_path)
    store.ensure_account(instrument, settings.initial_cash, 1)
    engine = PaperEngine(settings, store)
    strategy = ATRTickStrategy(
        period=2,
        multiplier=0.75,
        bar_minutes=15,
        trend_efficiency_period=2,
        minimum_trend_efficiency=0,
    )
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


def test_unready_strategy_does_not_overwrite_persisted_checkpoint(tmp_path) -> None:
    settings = replace(load_settings("config/settings.toml"), database_path=tmp_path / "paper.db")
    instrument = settings.instruments[0]
    store = PaperStore(settings.database_path)
    store.ensure_account(instrument, settings.initial_cash, 1)
    persisted = ATRTickStrategy(
        period=settings.strategy.atr_period,
        multiplier=settings.strategy.atr_multiplier,
        bar_minutes=settings.strategy.bar_minutes,
        trend_efficiency_period=settings.strategy.trend_efficiency_period,
        minimum_trend_efficiency=settings.strategy.minimum_trend_efficiency,
        reversal_confirmation_atr=settings.strategy.reversal_confirmation_atr,
    )
    persisted.previous_price = Decimal("120")
    persisted.trailing_stop = Decimal("124.50")
    persisted.last_atr = Decimal("1.125")
    checkpoint = persisted.runtime_state()
    store.save_strategy_state(instrument.id, checkpoint, 2)

    runtime = InstrumentRuntime(
        instrument=instrument,
        feed=OfficialBarFeed([]),  # type: ignore[arg-type]
        strategy=ATRTickStrategy(
            period=settings.strategy.atr_period,
            multiplier=settings.strategy.atr_multiplier,
            bar_minutes=settings.strategy.bar_minutes,
            trend_efficiency_period=settings.strategy.trend_efficiency_period,
            minimum_trend_efficiency=settings.strategy.minimum_trend_efficiency,
            reversal_confirmation_atr=settings.strategy.reversal_confirmation_atr,
        ),
        strategy_ready=False,
    )
    engine = PaperEngine(settings, store)

    asyncio.run(
        engine._process_tick(
            runtime,
            Tick("degraded-tick", 3_600_000, Decimal("119"), Decimal("1"), "test"),
        )
    )

    assert store.strategy_state(instrument.id) == checkpoint
    assert store.orders(instrument.id, 1) == []
    assert runtime.status == "DEGRADED"


def test_warmup_uses_persisted_closed_bars_when_rest_is_unavailable(tmp_path) -> None:
    settings = replace(load_settings("config/settings.toml"), database_path=tmp_path / "paper.db")
    instrument = settings.instruments[0]
    store = PaperStore(settings.database_path)
    store.ensure_account(instrument, settings.initial_cash, 1)
    bars = [
        Bar(
            index * 900_000,
            (index + 1) * 900_000 - 1,
            Decimal(100 + index),
            Decimal(102 + index),
            Decimal(99 + index),
            Decimal(101 + index),
        )
        for index in range(settings.strategy.atr_period)
    ]
    store.upsert_history_bars(instrument, 15, bars, "binance_public_kline_rest")
    strategy = ATRTickStrategy(
        period=settings.strategy.atr_period,
        multiplier=settings.strategy.atr_multiplier,
        bar_minutes=settings.strategy.bar_minutes,
        trend_efficiency_period=settings.strategy.trend_efficiency_period,
        minimum_trend_efficiency=settings.strategy.minimum_trend_efficiency,
        reversal_confirmation_atr=settings.strategy.reversal_confirmation_atr,
    )
    runtime = InstrumentRuntime(
        instrument=instrument,
        feed=FailingWarmupFeed([]),  # type: ignore[arg-type]
        strategy=strategy,
        strategy_ready=False,
    )
    engine = PaperEngine(settings, store)

    asyncio.run(engine._warmup_runtime(runtime))

    assert runtime.strategy_ready
    assert runtime.strategy.view().ready
    assert runtime.kline_validation == "WAREHOUSE_FALLBACK"
    assert runtime.last_official_bar_start_ms == bars[-1].start_ms


def test_rest_reconciliation_closes_missing_bars_but_not_current_bar(tmp_path) -> None:
    settings = replace(load_settings("config/settings.toml"), database_path=tmp_path / "paper.db")
    instrument = settings.instruments[0]
    store = PaperStore(settings.database_path)
    store.ensure_account(instrument, settings.initial_cash, 1)
    bars = [
        Bar(
            index * 900_000,
            (index + 1) * 900_000 - 1,
            Decimal(100 + index),
            Decimal(102 + index),
            Decimal(99 + index),
            Decimal(101 + index),
        )
        for index in range(4)
    ]
    store.upsert_history_bars(instrument, 15, [bars[0]], "test_kline_rest")
    strategy = ATRTickStrategy(period=2, multiplier=1, bar_minutes=15)
    strategy.bootstrap(bars[:3])
    runtime = InstrumentRuntime(
        instrument=instrument,
        feed=OfficialBarFeed(bars),  # type: ignore[arg-type]
        strategy=strategy,
        last_official_bar_start_ms=bars[0].start_ms,
    )
    engine = PaperEngine(settings, store)

    synced = asyncio.run(engine._reconcile_closed_klines(runtime, now_ms=bars[3].start_ms + 10_000))

    assert synced
    assert runtime.last_official_bar_start_ms == bars[2].start_ms
    assert runtime.kline_validation == "REST_RECONCILED"
    stored = store.ohlcv_bars(instrument.id, 15, 10)
    assert [row["start_ms"] for row in stored] == [
        bars[2].start_ms,
        bars[1].start_ms,
        bars[0].start_ms,
    ]
    assert all(row["is_closed"] for row in stored)
    backfill = next(
        event
        for event in store.events(instrument.id, 10)
        if event["event_type"] == "KLINE_REST_BACKFILL"
    )
    assert backfill["payload"]["start_ms"] == bars[1].start_ms
    assert backfill["payload"]["end_ms"] == bars[2].start_ms
