import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest

from mastermind_tick.binance_futures import BinanceFuturesAPIError, FuturesSymbolRules
from mastermind_tick.config import load_settings
from mastermind_tick.live_futures import LiveFuturesTrader, LiveOperationError
from mastermind_tick.live_preflight import run as run_live_preflight
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
        self.tradfi_contract_calls = 0

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

    async def sign_tradfi_perps_contract(self) -> dict:
        self.tradfi_contract_calls += 1
        return {"msg": "SUCCESS"}

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
    assert "FUTURES_TEST_ORDER_REQUIRED" in trader.block_reasons


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
    store.set_metadata("futures_test_order_passed", "true", 1_700_000_000_000)
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
    store.set_metadata("futures_test_order_passed", "true", 1_700_000_000_000)
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
    store.set_metadata("futures_test_order_passed", "true", 1_700_000_000_000)
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


def test_futures_close_short_uses_full_position_and_short_side(
    tmp_path, monkeypatch
) -> None:
    settings = futures_settings(tmp_path, allow_orders=True)
    monkeypatch.setenv(
        settings.live_futures.activation_env, settings.live_futures.activation_value
    )
    client = FakeFuturesClient(short_quantity="-1.23")
    store = LiveStore(settings.live_futures.database_path)
    store.set_metadata("futures_test_order_passed", "true", 1_700_000_000_000)
    store.set_metadata("managed_position", "true", 1_700_000_000_000)
    trader = LiveFuturesTrader(
        settings, store, client=client  # type: ignore[arg-type]
    )
    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())
    tick = Tick("close-short", 1_700_000_000_000, Decimal("120"), Decimal("1"), "test")
    signal = StrategySignal(
        side=Side.BUY,
        reason="price_crossed_above_atr_stop",
        signal_price=tick.price,
        trailing_stop=Decimal("115"),
        atr=Decimal("2"),
        bar_start_ms=tick.timestamp_ms // 900_000 * 900_000,
        tick_id=tick.event_id,
        signal_at_ms=tick.timestamp_ms,
        reduce_only=True,
    )

    asyncio.run(trader._submit_signal(signal, tick))

    call = client.market_order_calls[0]
    assert call["side"] == "BUY"
    assert call["position_side"] == "SHORT"
    assert call["quantity"] == Decimal("1.23")
    order = store.orders(settings.live_futures.account_id)[0]
    assert order["reduce_only"] == 1
    assert order["position_side"] == "SHORT"


def test_operator_can_persistently_stop_and_resume_live_strategy(
    tmp_path, monkeypatch
) -> None:
    settings = futures_settings(tmp_path, allow_orders=True)
    monkeypatch.setenv(
        settings.live_futures.activation_env, settings.live_futures.activation_value
    )
    store = LiveStore(settings.live_futures.database_path)
    store.set_metadata("futures_test_order_passed", "true", 1_700_000_000_000)
    trader = LiveFuturesTrader(
        settings, store, client=FakeFuturesClient()  # type: ignore[arg-type]
    )
    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())
    assert trader.order_submission_ready

    stopped = asyncio.run(trader.set_strategy_paused(True))

    assert stopped["strategy_paused"] is True
    assert not trader.order_submission_ready
    assert trader.status_message == "Live Futures strategy stopped by operator"
    assert store.events(settings.live_futures.account_id)[0]["code"] == "STRATEGY_STOPPED"

    resumed = asyncio.run(trader.set_strategy_paused(False))

    assert resumed["strategy_paused"] is False
    assert trader.order_submission_ready
    assert store.events(settings.live_futures.account_id)[0]["code"] == "STRATEGY_RESUMED"


def test_operator_flatten_closes_fresh_long_position_while_strategy_is_stopped(
    tmp_path,
) -> None:
    settings = futures_settings(tmp_path)
    client = FakeFuturesClient(long_quantity="1.23")
    store = LiveStore(settings.live_futures.database_path)
    store.set_metadata("trading_paused", "true", 1_700_000_000_000)
    trader = LiveFuturesTrader(
        settings, store, client=client  # type: ignore[arg-type]
    )
    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())

    result = asyncio.run(trader.manual_flatten())

    assert result["ok"] is True
    assert result["already_flat"] is False
    assert result["flat_confirmed"] is True
    assert len(client.market_order_calls) == 1
    call = client.market_order_calls[0]
    assert call["side"] == "SELL"
    assert call["position_side"] == "LONG"
    assert call["quantity"] == Decimal("1.23")
    order = store.orders(settings.live_futures.account_id)[0]
    assert order["reason"] == "operator_manual_flatten"
    assert order["reduce_only"] == 1
    assert trader.strategy.flattened_this_bar
    assert trader.strategy.reversal_direction is None
    assert store.events(settings.live_futures.account_id)[0]["code"] == (
        "MANUAL_FLATTEN_SUBMITTED"
    )


def test_operator_flatten_is_noop_when_exchange_position_is_flat(tmp_path) -> None:
    settings = futures_settings(tmp_path)
    client = FakeFuturesClient()
    store = LiveStore(settings.live_futures.database_path)
    trader = LiveFuturesTrader(
        settings, store, client=client  # type: ignore[arg-type]
    )
    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())

    result = asyncio.run(trader.manual_flatten())

    assert result == {
        "ok": True,
        "already_flat": True,
        "flat_confirmed": True,
        "orders": [],
    }
    assert client.market_order_calls == []


def test_operator_flatten_rejects_when_exchange_has_open_order(tmp_path) -> None:
    settings = futures_settings(tmp_path)
    client = FakeFuturesClient(
        long_quantity="1.23", open_orders=[{"clientOrderId": "manual-order"}]
    )
    store = LiveStore(settings.live_futures.database_path)
    trader = LiveFuturesTrader(
        settings, store, client=client  # type: ignore[arg-type]
    )
    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())

    with pytest.raises(LiveOperationError, match="open orders") as raised:
        asyncio.run(trader.manual_flatten())

    assert raised.value.code == "OPEN_ORDER_PRESENT"
    assert client.market_order_calls == []


def test_futures_daily_order_limit_blocks_new_entry(tmp_path, monkeypatch) -> None:
    settings = futures_settings(tmp_path, allow_orders=True)
    monkeypatch.setenv(
        settings.live_futures.activation_env, settings.live_futures.activation_value
    )
    client = FakeFuturesClient()
    store = LiveStore(settings.live_futures.database_path)
    store.set_metadata("futures_test_order_passed", "true", 1_700_000_000_000)
    trader = LiveFuturesTrader(
        settings, store, client=client  # type: ignore[arg-type]
    )
    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())
    timestamp_ms = 1_700_000_000_000
    day_start_ms = timestamp_ms // 86_400_000 * 86_400_000
    for index in range(settings.live_futures.max_orders_per_day):
        order_id = f"daily-{index}"
        store.create_order(
            client_order_id=order_id,
            account_id=settings.live_futures.account_id,
            symbol="SOXLUSDT",
            side="BUY",
            position_side="LONG",
            reason="test",
            signal_price="120",
            signal_at_ms=day_start_ms + index,
            requested_quantity="0.1",
            requested_quote_quantity=None,
        )
        store.update_order(
            order_id,
            status="FILLED",
            updated_at_ms=day_start_ms + index,
            submitted_at_ms=day_start_ms + index,
        )
    signal = StrategySignal(
        side=Side.BUY,
        reason="entry",
        signal_price=Decimal("120"),
        trailing_stop=Decimal("115"),
        atr=Decimal("2"),
        bar_start_ms=day_start_ms,
        tick_id="risk",
    )
    tick = Tick("risk", timestamp_ms, Decimal("120"), Decimal("1"), "test")

    rejection = asyncio.run(trader._risk_rejection(signal, tick))

    assert rejection == "DAILY_ORDER_LIMIT"


def test_futures_ambiguous_submission_waits_for_reconciliation(
    tmp_path, monkeypatch
) -> None:
    class AmbiguousClient(FakeFuturesClient):
        async def market_order(self, **kwargs) -> dict:
            self.market_order_calls.append(kwargs)
            raise BinanceFuturesAPIError(503, -1007, "execution status unknown")

    settings = futures_settings(tmp_path, allow_orders=True)
    monkeypatch.setenv(
        settings.live_futures.activation_env, settings.live_futures.activation_value
    )
    client = AmbiguousClient()
    store = LiveStore(settings.live_futures.database_path)
    store.set_metadata("futures_test_order_passed", "true", 1_700_000_000_000)
    trader = LiveFuturesTrader(
        settings, store, client=client  # type: ignore[arg-type]
    )
    asyncio.run(trader.public_preflight())
    asyncio.run(trader.reconcile())
    tick = Tick("ambiguous", 1_700_000_000_000, Decimal("120"), Decimal("1"), "test")
    signal = StrategySignal(
        side=Side.BUY,
        reason="entry",
        signal_price=tick.price,
        trailing_stop=Decimal("115"),
        atr=Decimal("2"),
        bar_start_ms=tick.timestamp_ms // 900_000 * 900_000,
        tick_id=tick.event_id,
    )

    asyncio.run(trader._submit_signal(signal, tick))

    assert len(client.market_order_calls) == 1
    assert store.orders(settings.live_futures.account_id)[0]["status"] == "SUBMITTING"
    assert store.events(settings.live_futures.account_id)[0]["code"] == "ORDER_RESULT_UNKNOWN"


def test_futures_preflight_uses_test_endpoint_without_real_order(
    tmp_path, capsys
) -> None:
    client = FakeFuturesClient()

    exit_code = asyncio.run(
        run_live_preflight("config/settings.toml", test_order=True, client=client)  # type: ignore[arg-type]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"endpoint": "/fapi/v1/order/test"' in output
    assert '"real_orders_sent": false' in output
    assert len(client.market_order_calls) == 1
    assert client.market_order_calls[0]["test"] is True
    assert client.market_order_calls[0]["position_side"] == "LONG"
    assert client.market_order_calls[0]["quantity"] == Decimal("0.05")


def test_futures_preflight_can_sign_tradfi_contract(capsys) -> None:
    client = FakeFuturesClient()

    exit_code = asyncio.run(
        run_live_preflight(
            "config/settings.toml",
            test_order=False,
            sign_tradfi_contract=True,
            client=client,  # type: ignore[arg-type]
        )
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"endpoint": "/fapi/v1/stock/contract"' in output
    assert '"ok": true' in output
    assert client.tradfi_contract_calls == 1
    assert client.market_order_calls == []


def test_observe_only_cross_is_persisted_as_shadow_event(tmp_path) -> None:
    class ShadowStrategy:
        last_cross_at_ms = None
        last_cross = None
        last_cross_result = None
        last_cross_reason = None

        def on_tick(self, tick, **_kwargs):
            self.last_cross_at_ms = tick.timestamp_ms
            self.last_cross = "UP"
            self.last_cross_result = "BLOCKED"
            self.last_cross_reason = "TRADING_PAUSED"
            return None

        def runtime_state(self):
            return {}

        def view(self):
            class View:
                atr = Decimal("2")
                trailing_stop = Decimal("115")

            return View()

    settings = futures_settings(tmp_path)
    store = LiveStore(settings.live_futures.database_path)
    trader = LiveFuturesTrader(
        settings, store, client=FakeFuturesClient()  # type: ignore[arg-type]
    )
    trader.strategy = ShadowStrategy()  # type: ignore[assignment]
    tick = Tick("shadow", 1_700_000_000_000, Decimal("120"), Decimal("1"), "test")

    asyncio.run(trader.process_tick(tick))

    event = store.events(settings.live_futures.account_id)[0]
    assert event["code"] == "SHADOW_CROSS"
    assert event["timestamp_ms"] == tick.timestamp_ms
    assert event["details_json"]["direction"] == "UP"
