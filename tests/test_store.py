from decimal import Decimal

from mastermind_tick.config import ExecutionSettings, InstrumentSettings
from mastermind_tick.models import Bar, Side, StrategySignal, Tick
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
