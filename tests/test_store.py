from decimal import Decimal

from mastermind_tick.config import ExecutionSettings, InstrumentSettings
from mastermind_tick.models import Side, StrategySignal, Tick
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

