import asyncio
from dataclasses import replace
from decimal import Decimal

from mastermind_tick.binance_spot import SpotSymbolRules
from mastermind_tick.config import load_settings
from mastermind_tick.live_spot import LiveSpotTrader
from mastermind_tick.live_store import LiveStore
from mastermind_tick.models import Side, StrategySignal, Tick


class FakeSpotClient:
    has_credentials = True

    def __init__(self, *, base_free: str = "0", open_orders: list[dict] | None = None):
        self.base_free = base_free
        self._open_orders = open_orders or []
        self.buy_calls: list[tuple] = []

    async def close(self) -> None:
        return None

    async def sync_time(self) -> int:
        return 0

    async def symbol_rules(self, symbol: str) -> SpotSymbolRules:
        return SpotSymbolRules(
            symbol=symbol,
            status="TRADING",
            base_asset="SOXLB",
            quote_asset="USDT",
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            minimum_notional=Decimal("5"),
            maximum_notional=None,
            market_order_allowed=True,
            quote_order_quantity_allowed=True,
        )

    async def book_ticker(self, symbol: str) -> dict:
        return {"symbol": symbol, "bidPrice": "99.9", "askPrice": "100.1"}

    async def account(self) -> dict:
        return {
            "canTrade": True,
            "balances": [
                {"asset": "SOXLB", "free": self.base_free, "locked": "0"},
                {"asset": "USDT", "free": "1000", "locked": "0"},
            ],
        }

    async def open_orders(self, symbol: str) -> list[dict]:
        return self._open_orders

    async def query_order(self, symbol: str, *, client_order_id: str) -> dict:
        return {
            "symbol": symbol,
            "orderId": 7,
            "clientOrderId": client_order_id,
            "status": "FILLED",
            "executedQty": "0.1",
            "cummulativeQuoteQty": "10",
        }

    async def my_trades(self, symbol: str, *, order_id: int | None = None) -> list[dict]:
        return [
            {
                "symbol": symbol,
                "id": 9,
                "orderId": order_id,
                "time": 1_700_000_001_000,
                "price": "100",
                "qty": "0.1",
                "quoteQty": "10",
                "commission": "0.0001",
                "commissionAsset": "SOXLB",
            }
        ]

    async def market_buy(
        self,
        symbol: str,
        quote_order_quantity: Decimal,
        client_order_id: str,
        *,
        test: bool = False,
    ) -> dict:
        self.buy_calls.append((symbol, quote_order_quantity, client_order_id, test))
        return {
            "symbol": symbol,
            "orderId": 7,
            "clientOrderId": client_order_id,
            "status": "FILLED",
            "executedQty": "0.1",
            "cummulativeQuoteQty": "10",
        }

    async def market_sell(self, *args, **kwargs) -> dict:
        raise AssertionError("sell not expected")

    async def cancel_order(self, symbol: str, client_order_id: str) -> dict:
        return {
            "symbol": symbol,
            "orderId": 7,
            "clientOrderId": client_order_id,
            "status": "CANCELED",
            "executedQty": "0",
            "cummulativeQuoteQty": "0",
        }


def live_settings(tmp_path):
    settings = load_settings("config/settings.toml")
    live = replace(
        settings.live_spot,
        enabled=True,
        allow_order_submission=True,
        database_path=tmp_path / "live.db",
    )
    return replace(settings, live_spot=live)


def test_live_order_needs_every_gate_and_ingests_actual_fill(tmp_path, monkeypatch) -> None:
    settings = live_settings(tmp_path)
    monkeypatch.setenv(settings.live_spot.activation_env, settings.live_spot.activation_value)
    client = FakeSpotClient()
    store = LiveStore(settings.live_spot.database_path)
    trader = LiveSpotTrader(settings, store, client=client)  # type: ignore[arg-type]

    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())
    assert trader.order_submission_ready

    tick = Tick("signal-tick", 1_700_000_000_000, Decimal("100"), Decimal("1"), "test")
    signal = StrategySignal(
        side=Side.BUY,
        reason="price_crossed_above_atr_stop",
        signal_price=tick.price,
        trailing_stop=Decimal("99"),
        atr=Decimal("2"),
        bar_start_ms=tick.timestamp_ms // 900_000 * 900_000,
        tick_id=tick.event_id,
        signal_at_ms=tick.timestamp_ms,
    )
    asyncio.run(trader._submit_signal(signal, tick))

    assert len(client.buy_calls) == 1
    assert client.buy_calls[0][1] == Decimal("49.50")
    orders = store.orders(settings.live_spot.account_id)
    assert orders[0]["status"] == "FILLED"
    assert len(store.fills(settings.live_spot.account_id)) == 1
    assert store.metadata("managed_position") == "true"


def test_existing_position_and_unknown_order_block_activation(tmp_path, monkeypatch) -> None:
    settings = live_settings(tmp_path)
    monkeypatch.setenv(settings.live_spot.activation_env, settings.live_spot.activation_value)
    client = FakeSpotClient(
        base_free="1", open_orders=[{"clientOrderId": "manual-order"}]
    )
    trader = LiveSpotTrader(
        settings,
        LiveStore(settings.live_spot.database_path),
        client=client,  # type: ignore[arg-type]
    )

    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())

    assert not trader.order_submission_ready
    assert "UNMANAGED_EXISTING_POSITION" in trader.block_reasons
    assert "UNKNOWN_OPEN_ORDERS" in trader.block_reasons


def test_default_configuration_cannot_submit_orders(tmp_path) -> None:
    settings = load_settings("config/settings.toml")
    settings = replace(
        settings,
        live_spot=replace(settings.live_spot, database_path=tmp_path / "live.db"),
    )
    trader = LiveSpotTrader(
        settings,
        LiveStore(settings.live_spot.database_path),
        client=FakeSpotClient(),  # type: ignore[arg-type]
    )

    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())

    assert not trader.order_submission_ready
    assert trader.readiness()["allow_order_submission"] is False


def test_entry_risk_limits_do_not_block_spot_exit(tmp_path) -> None:
    settings = live_settings(tmp_path)
    store = LiveStore(settings.live_spot.database_path)
    trader = LiveSpotTrader(
        settings,
        store,
        client=FakeSpotClient(),  # type: ignore[arg-type]
    )
    tick = Tick("exit-tick", 1_700_000_000_000, Decimal("100"), Decimal("1"), "test")
    for index in range(settings.live_spot.max_orders_per_day):
        store.create_order(
            client_order_id=f"prior-{index}",
            account_id=settings.live_spot.account_id,
            symbol="SOXLBUSDT",
            side="BUY",
            reason="test",
            signal_price="100",
            signal_at_ms=tick.timestamp_ms,
            requested_quantity=None,
            requested_quote_quantity="10",
        )
        store.update_order(
            f"prior-{index}",
            status="FILLED",
            updated_at_ms=tick.timestamp_ms,
            submitted_at_ms=tick.timestamp_ms,
            payload={"executedQty": "0.1", "cummulativeQuoteQty": "10"},
        )
    signal = StrategySignal(
        side=Side.SELL,
        reason="price_crossed_below_atr_stop",
        signal_price=tick.price,
        trailing_stop=Decimal("101"),
        atr=Decimal("2"),
        bar_start_ms=tick.timestamp_ms // 900_000 * 900_000,
        tick_id=tick.event_id,
        signal_at_ms=tick.timestamp_ms,
        reduce_only=True,
    )

    assert asyncio.run(trader._risk_rejection(signal, tick)) is None
