from decimal import Decimal

from mastermind_tick.config import ExecutionSettings, InstrumentSettings
from mastermind_tick.models import Bar, FundingRate, Side, StrategySignal, Tick
from mastermind_tick.store import PaperStore


def instrument() -> InstrumentSettings:
    return InstrumentSettings(
        id="soxlb",
        symbol="SOXLBUSDT",
        display_symbol="SOXLB/USDT",
        name="test",
        asset_type="tokenized_equity",
        venue="Binance",
        currency="USDT",
        feed="binance",
        quantity_step=0.001,
        reference_symbol="SOXL",
    )


def market_tick(event_id: str, timestamp_ms: int, price: str) -> Tick:
    return Tick(event_id, timestamp_ms, Decimal(price), Decimal("1"), "test")


def futures_instrument() -> InstrumentSettings:
    return InstrumentSettings(
        id="soxl_perp",
        symbol="SOXLUSDT",
        display_symbol="SOXL/USDT PERP",
        name="test futures",
        asset_type="tradifi_perpetual",
        venue="Binance USD-M Futures",
        currency="USDT",
        feed="binance_futures",
        quantity_step=0.01,
        reference_symbol="SOXL",
        paper_model="futures",
        leverage=1,
        margin_mode="isolated",
        position_fraction=0.95,
        fee_bps=5,
        slippage_bps=0,
        minimum_notional=5,
    )


def signal(side: Side, tick_id: str, price: str = "100") -> StrategySignal:
    return StrategySignal(
        side=side,
        reason="test_cross",
        signal_price=Decimal(price),
        trailing_stop=Decimal("99"),
        atr=Decimal("2"),
        bar_start_ms=0,
        tick_id=tick_id,
    )


def test_order_fills_only_on_next_tick_and_updates_ledger(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    item = instrument()
    execution = ExecutionSettings(fee_bps=10, slippage_bps=5, minimum_notional=5)
    store.ensure_account(item, 10_000, 1)
    store.submit_order("soxlb", signal(Side.BUY, "tick-1"), 1)

    assert store.fill_pending("soxlb", market_tick("tick-1", 1, "100"), item, execution, 1) is None
    result = store.fill_pending(
        "soxlb", market_tick("tick-2", 2, "101"), item, execution, 1
    )

    assert result is not None
    assert result["status"] == "FILLED"
    account = store.account("soxlb")
    assert Decimal(account["quantity"]) > 0
    assert Decimal(account["cash"]) >= 0
    assert Decimal(account["total_fees"]) > 0
    assert len(store.fills("soxlb")) == 1


def test_round_trip_realized_pnl_includes_both_fees(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    item = instrument()
    execution = ExecutionSettings(fee_bps=10, slippage_bps=0, minimum_notional=5)
    store.ensure_account(item, 10_000, 1)
    store.submit_order("soxlb", signal(Side.BUY, "buy-signal"), 1)
    store.fill_pending("soxlb", market_tick("buy-fill", 2, "100"), item, execution, 1)
    store.submit_order("soxlb", signal(Side.SELL, "sell-signal", "110"), 3)
    store.fill_pending("soxlb", market_tick("sell-fill", 4, "110"), item, execution, 1)

    account = store.account("soxlb")
    assert Decimal(account["quantity"]) == 0
    assert Decimal(account["cash"]) > Decimal("10000")
    assert Decimal(account["realized_pnl"]) > 0
    assert len(store.fills("soxlb")) == 2


def test_pending_sell_can_fill_on_signal_tick_for_pine_immediately(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    item = instrument()
    execution = ExecutionSettings(fee_bps=10, slippage_bps=0, minimum_notional=5)
    store.ensure_account(item, 10_000, 1)
    store.submit_order("soxlb", signal(Side.BUY, "buy-signal"), 1)
    store.fill_pending("soxlb", market_tick("buy-fill", 2, "100"), item, execution, 1)
    store.submit_order("soxlb", signal(Side.SELL, "sell-signal", "101"), 3)
    sell_tick = market_tick("sell-signal", 3, "101")

    assert store.fill_pending("soxlb", sell_tick, item, execution, 1) is None
    result = store.fill_pending(
        "soxlb",
        sell_tick,
        item,
        execution,
        1,
        allow_same_tick=True,
    )

    assert result is not None
    assert result["side"] == "SELL"
    assert Decimal(store.account("soxlb")["quantity"]) == 0


def test_equity_snapshot_persists_atr_chart_values(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    item = instrument()
    store.ensure_account(item, 10_000, 1)

    store.snapshot(
        "soxlb",
        market_tick("chart-tick", 2, "101.25"),
        {"atr": "2.5", "trailing_stop": "98.75", "relation": "above"},
    )

    point = store.equity("soxlb")[-1]
    assert point["atr"] == "2.5"
    assert point["trailing_stop"] == "98.75"
    assert point["relation"] == "above"


def test_market_warehouse_deduplicates_ticks_and_updates_ohlcv(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    item = instrument()
    history = Bar(
        start_ms=0,
        end_ms=899_999,
        open=Decimal("99"),
        high=Decimal("101"),
        low=Decimal("98"),
        close=Decimal("100"),
        volume=Decimal("12"),
        trade_count=8,
    )
    store.upsert_history_bars(item, 15, [history], "test_history")
    store.upsert_history_bars(item, 15, [history], "test_history")

    first = Tick(
        event_id="agg-1",
        timestamp_ms=900_000,
        price=Decimal("101"),
        quantity=Decimal("2"),
        source="test_live",
        aggregate_trade_id=1,
        first_trade_id=10,
        last_trade_id=12,
        buyer_is_maker=True,
        event_time_ms=900_001,
    )
    second = Tick(
        event_id="agg-2",
        timestamp_ms=901_000,
        price=Decimal("99"),
        quantity=Decimal("3"),
        source="test_live",
        aggregate_trade_id=2,
        first_trade_id=13,
        last_trade_id=13,
        buyer_is_maker=False,
        event_time_ms=901_001,
    )

    assert store.record_market_tick(item, 15, first)
    assert not store.record_market_tick(item, 15, first)
    assert store.record_market_tick(item, 15, second)

    trades = store.agg_trades("soxlb", 10)
    assert len(trades) == 2
    assert trades[-1]["buyer_is_maker"] is True
    bars = store.ohlcv_bars("soxlb", 15, 10)
    assert len(bars) == 2
    assert bars[0]["open"] == "101"
    assert bars[0]["high"] == "101"
    assert bars[0]["low"] == "99"
    assert bars[0]["close"] == "99"
    assert bars[0]["volume"] == "5"
    assert bars[0]["trade_count"] == 4
    assert not bars[0]["is_closed"]
    assert bars[1]["is_closed"]

    summary = store.warehouse_summary((item,), 15)
    assert summary["instruments"][0]["agg_trades"]["row_count"] == 2
    assert summary["instruments"][0]["ohlcv"]["row_count"] == 2


def test_aggregated_tick_preserves_new_bar_ohlc(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    item = futures_instrument()
    tick = Tick(
        event_id="futures-bucket",
        timestamp_ms=900_000,
        price=Decimal("101"),
        quantity=Decimal("5"),
        source="binance_futures",
        first_trade_id=10,
        last_trade_id=11,
        open_price=Decimal("100"),
        high_price=Decimal("102"),
        low_price=Decimal("99"),
        notional=Decimal("503"),
    )

    assert store.record_market_tick(item, 15, tick)
    bar = store.ohlcv_bars(item.id, 15, 1)[0]
    assert bar["open"] == "100"
    assert bar["high"] == "102"
    assert bar["low"] == "99"
    assert bar["close"] == "101"


def test_late_tick_is_archived_without_overwriting_official_closed_bar(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    item = instrument()
    official = Bar(
        start_ms=900_000,
        end_ms=1_799_999,
        open=Decimal("100"),
        high=Decimal("104"),
        low=Decimal("98"),
        close=Decimal("103"),
        volume=Decimal("50"),
        trade_count=25,
    )
    store.upsert_history_bars(item, 15, [official], "binance_public_kline_rest")
    late_tick = Tick(
        event_id="late-trade",
        timestamp_ms=1_799_000,
        price=Decimal("110"),
        quantity=Decimal("2"),
        source="binance_public",
        aggregate_trade_id=100,
        first_trade_id=200,
        last_trade_id=200,
    )

    assert store.record_market_tick(item, 15, late_tick)

    bar = store.ohlcv_bars(item.id, 15, 1)[0]
    assert bar["open"] == "100"
    assert bar["high"] == "104"
    assert bar["low"] == "98"
    assert bar["close"] == "103"
    assert bar["volume"] == "50"
    assert bar["trade_count"] == 25
    assert bar["is_closed"] is True
    assert bar["source"] == "binance_public_kline_rest"
    assert store.agg_trades(item.id, 1)[0]["event_id"] == "late-trade"


def test_futures_round_trip_uses_margin_accounting(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    item = futures_instrument()
    execution = ExecutionSettings(fee_bps=10, slippage_bps=5, minimum_notional=5)
    store.ensure_account(item, 10_000, 1)
    store.submit_order(item.id, signal(Side.BUY, "buy-signal"), 1)
    store.fill_pending(item.id, market_tick("buy-fill", 2, "100"), item, execution, 0.95)

    account = store.account(item.id)
    assert Decimal(account["quantity"]) == Decimal("95")
    assert Decimal(account["cash"]) == Decimal("9995.2500")

    tick = Tick(
        "mark",
        3,
        Decimal("110"),
        Decimal("1"),
        "test",
        mark_price=Decimal("109"),
        index_price=Decimal("108.9"),
        funding_rate=Decimal("0.001"),
    )
    point = store.snapshot(item.id, tick)
    assert Decimal(point["unrealized_pnl"]) == Decimal("855")
    assert Decimal(point["equity"]) == Decimal("10850.2500")
    assert Decimal(point["initial_margin"]) == Decimal("10355")

    store.submit_order(item.id, signal(Side.SELL, "sell-signal", "110"), 4)
    store.fill_pending(item.id, market_tick("sell-fill", 5, "110"), item, execution, 0.95)
    closed = store.account(item.id)
    assert Decimal(closed["quantity"]) == 0
    assert Decimal(closed["cash"]) == Decimal("10940.0250")


def test_futures_funding_is_idempotent_and_updates_equity(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    item = futures_instrument()
    execution = ExecutionSettings(fee_bps=10, slippage_bps=5, minimum_notional=5)
    store.ensure_account(item, 10_000, 1)
    store.submit_order(item.id, signal(Side.BUY, "buy-signal"), 1)
    store.fill_pending(item.id, market_tick("buy-fill", 2, "100"), item, execution, 0.95)
    funding = FundingRate(
        timestamp_ms=28_800_000,
        rate=Decimal("0.001"),
        mark_price=Decimal("100"),
    )

    payment = store.apply_funding(item.id, funding)
    duplicate = store.apply_funding(item.id, funding)

    assert payment is not None
    assert Decimal(str(payment["amount"])) == Decimal("-9.5")
    assert duplicate is None
    account = store.account(item.id)
    assert Decimal(account["total_funding"]) == Decimal("-9.500")
    assert len(store.funding_payments(item.id)) == 1
