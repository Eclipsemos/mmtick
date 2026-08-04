import asyncio
from dataclasses import replace
from decimal import Decimal

from mastermind_tick.binance_futures import FuturesSymbolRules
from mastermind_tick.config import load_settings
from mastermind_tick.live_futures import LiveFuturesTrader
from mastermind_tick.live_store import LiveStore
from mastermind_tick.models import Side, StrategySignal, Tick


class FakeFuturesClient:
    has_credentials = True

    def __init__(
        self,
        *,
        leverage: int = 2,
        margin_type: str = "isolated",
        multi_assets: bool = False,
        long_quantity: str = "0",
        short_quantity: str = "0",
        open_orders: list[dict] | None = None,
    ):
        self.leverage = leverage
        self.margin_type = margin_type
        self.multi_assets = multi_assets
        self.long_quantity = long_quantity
        self.short_quantity = short_quantity
        self._open_orders = open_orders or []
        self.market_order_calls: list[dict] = []

    async def close(self) -> None:
        return None

    async def sync_time(self) -> int:
        return 0

    async def symbol_rules(self, symbol: str) -> FuturesSymbolRules:
        return FuturesSymbolRules(
            symbol=symbol,
            status="TRADING",
            quantity_step=Decimal("0.01"),
            minimum_quantity=Decimal("0.01"),
            minimum_notional=Decimal("5"),
            market_order_allowed=True,
        )

    async def book_ticker(self, symbol: str) -> dict:
        return {"symbol": symbol, "bidPrice": "119.9", "askPrice": "120.1"}

    async def account(self) -> dict:
        return {
            "canTrade": True,
            "totalWalletBalance": "1600",
            "totalMarginBalance": "1600",
            "availableBalance": "1600",
            "totalUnrealizedProfit": "0",
        }

    async def position_risk(self, symbol: str) -> list[dict]:
        common = {
            "symbol": symbol,
            "entryPrice": "120",
            "markPrice": "120",
            "liquidationPrice": "0",
            "leverage": str(self.leverage),
            "marginType": self.margin_type,
        }
        return [
            {**common, "positionSide": "LONG", "positionAmt": self.long_quantity},
            {**common, "positionSide": "SHORT", "positionAmt": self.short_quantity},
        ]

    async def position_mode(self) -> dict:
        return {"dualSidePosition": True}

    async def multi_assets_mode(self) -> dict:
        return {"multiAssetsMargin": self.multi_assets}

    async def api_restrictions(self) -> dict:
        return {
            "enableReading": True,
            "enableWithdrawals": False,
            "ipRestrict": True,
        }

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        return self._open_orders

    async def user_trades(
        self, symbol: str, *, order_id: int | None = None
    ) -> list[dict]:
        if order_id is None:
            return []
        call = self.market_order_calls[-1]
        quantity = str(call["quantity"])
        return [
            {
                "symbol": symbol,
                "id": 91,
                "orderId": order_id,
                "time": 1_700_000_001_000,
                "side": call["side"],
                "positionSide": call["position_side"],
                "price": "120",
                "qty": quantity,
                "quoteQty": str(Decimal(quantity) * Decimal("120")),
                "commission": "0.05",
                "commissionAsset": "USDT",
                "realizedPnl": "0",
            }
        ]

    async def income_history(self, symbol: str) -> list[dict]:
        return []

    async def market_order(self, **kwargs) -> dict:
        self.market_order_calls.append(kwargs)
        return {
            "symbol": kwargs["symbol"],
            "orderId": 71,
            "clientOrderId": kwargs["client_order_id"],
            "status": "FILLED",
            "executedQty": str(kwargs["quantity"]),
            "cumQuote": str(kwargs["quantity"] * Decimal("120")),
        }

    async def query_order(self, symbol: str, *, client_order_id: str) -> dict:
        return {
            "symbol": symbol,
            "orderId": 71,
            "clientOrderId": client_order_id,
            "status": "FILLED",
            "executedQty": "0.83",
            "cumQuote": "99.6",
        }

    async def cancel_order(self, symbol: str, client_order_id: str) -> dict:
        return {"status": "CANCELED", "orderId": 71, "executedQty": "0"}


def futures_settings(tmp_path, *, allow_orders: bool = False):
    settings = load_settings("config/settings.toml")
    live = replace(
        settings.live_futures,
        enabled=True,
        allow_order_submission=allow_orders,
        database_path=tmp_path / "live-futures.db",
        credentials_path=None,
    )
    return replace(settings, live_futures=live)


def test_futures_readonly_reconciliation_persists_actual_account(tmp_path) -> None:
    settings = futures_settings(tmp_path)
    store = LiveStore(settings.live_futures.database_path)
    trader = LiveFuturesTrader(
        settings, store, client=FakeFuturesClient()  # type: ignore[arg-type]
    )

    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())

    snapshot = store.latest_futures_snapshot(settings.live_futures.account_id)
    assert snapshot is not None
    assert snapshot["margin_balance"] == "1600"
    assert snapshot["position_quantity"] == "0"
    assert trader.reconciliation_ok
    assert trader.status == "OBSERVE_ONLY"
    assert not trader.order_submission_ready
    assert trader.readiness()["symbol"] == "SOXLUSDT"


def test_futures_account_mode_mismatches_block_execution(tmp_path, monkeypatch) -> None:
    settings = futures_settings(tmp_path, allow_orders=True)
    monkeypatch.setenv(
        settings.live_futures.activation_env, settings.live_futures.activation_value
    )
    trader = LiveFuturesTrader(
        settings,
        LiveStore(settings.live_futures.database_path),
        client=FakeFuturesClient(  # type: ignore[arg-type]
            leverage=20, margin_type="cross", multi_assets=True
        ),
    )

    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())

    assert not trader.order_submission_ready
    assert {"LEVERAGE_MISMATCH", "MARGIN_MODE_MISMATCH", "MULTI_ASSET_MODE_ENABLED"} <= set(
        trader.block_reasons
    )


def test_futures_long_order_uses_hedge_position_side_and_actual_fill(
    tmp_path, monkeypatch
) -> None:
    settings = futures_settings(tmp_path, allow_orders=True)
    monkeypatch.setenv(
        settings.live_futures.activation_env, settings.live_futures.activation_value
    )
    client = FakeFuturesClient()
    store = LiveStore(settings.live_futures.database_path)
    trader = LiveFuturesTrader(
        settings, store, client=client  # type: ignore[arg-type]
    )
    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())
    assert trader.order_submission_ready
    tick = Tick("signal", 1_700_000_000_000, Decimal("120"), Decimal("1"), "test")
    signal = StrategySignal(
        side=Side.BUY,
        reason="price_crossed_above_atr_stop",
        signal_price=tick.price,
        trailing_stop=Decimal("115"),
        atr=Decimal("2"),
        bar_start_ms=tick.timestamp_ms // 900_000 * 900_000,
        tick_id=tick.event_id,
        signal_at_ms=tick.timestamp_ms,
    )

    asyncio.run(trader._submit_signal(signal, tick))

    assert len(client.market_order_calls) == 1
    call = client.market_order_calls[0]
    assert call["side"] == "BUY"
    assert call["position_side"] == "LONG"
    assert call["quantity"] == Decimal("0.83")
    assert store.fill_count(settings.live_futures.account_id) == 1
    assert store.orders(settings.live_futures.account_id)[0]["position_side"] == "LONG"


def test_futures_short_order_uses_hedge_position_side(tmp_path, monkeypatch) -> None:
    settings = futures_settings(tmp_path, allow_orders=True)
    monkeypatch.setenv(
        settings.live_futures.activation_env, settings.live_futures.activation_value
    )
    client = FakeFuturesClient()
    store = LiveStore(settings.live_futures.database_path)
    trader = LiveFuturesTrader(
        settings, store, client=client  # type: ignore[arg-type]
    )
    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())
    tick = Tick("short", 1_700_000_000_000, Decimal("120"), Decimal("1"), "test")
    signal = StrategySignal(
        side=Side.SELL,
        reason="price_crossed_below_atr_stop",
        signal_price=tick.price,
        trailing_stop=Decimal("125"),
        atr=Decimal("2"),
        bar_start_ms=tick.timestamp_ms // 900_000 * 900_000,
        tick_id=tick.event_id,
        signal_at_ms=tick.timestamp_ms,
    )

    asyncio.run(trader._submit_signal(signal, tick))

    assert len(client.market_order_calls) == 1
    call = client.market_order_calls[0]
    assert call["side"] == "SELL"
    assert call["position_side"] == "SHORT"
    assert store.orders(settings.live_futures.account_id)[0]["position_side"] == "SHORT"


def test_futures_close_long_uses_full_position_and_long_side(
    tmp_path, monkeypatch
) -> None:
    settings = futures_settings(tmp_path, allow_orders=True)
    monkeypatch.setenv(
        settings.live_futures.activation_env, settings.live_futures.activation_value
    )
    client = FakeFuturesClient(long_quantity="1.23")
    store = LiveStore(settings.live_futures.database_path)
    store.set_metadata("managed_position", "true", 1_700_000_000_000)
    trader = LiveFuturesTrader(
        settings, store, client=client  # type: ignore[arg-type]
    )
    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())
    tick = Tick("close", 1_700_000_000_000, Decimal("120"), Decimal("1"), "test")
    signal = StrategySignal(
        side=Side.SELL,
        reason="price_crossed_below_atr_stop",
        signal_price=tick.price,
        trailing_stop=Decimal("125"),
        atr=Decimal("2"),
        bar_start_ms=tick.timestamp_ms // 900_000 * 900_000,
        tick_id=tick.event_id,
        signal_at_ms=tick.timestamp_ms,
        reduce_only=True,
    )

    asyncio.run(trader._submit_signal(signal, tick))

    assert len(client.market_order_calls) == 1
    call = client.market_order_calls[0]
    assert call["side"] == "SELL"
    assert call["position_side"] == "LONG"
    assert call["quantity"] == Decimal("1.23")
    order = store.orders(settings.live_futures.account_id)[0]
    assert order["reduce_only"] == 1
    assert order["position_side"] == "LONG"
