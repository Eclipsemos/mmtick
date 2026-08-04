"""Credential-gated Binance Spot execution for the independent SOXLB live account."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.binance_spot import (
    BinanceSpotAPIError,
    BinanceSpotClient,
    SpotSymbolRules,
)
from mastermind_tick.config import InstrumentSettings, Settings
from mastermind_tick.engine import PaperEngine
from mastermind_tick.live_store import LiveStore
from mastermind_tick.models import Side, StrategySignal, Tick
from mastermind_tick.strategy import ATRTickStrategy

TERMINAL_ORDER_STATES = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"}
AMBIGUOUS_BINANCE_CODES = {-1006, -1007}


class LiveSpotTrader:
    """Run the existing ATR strategy against actual Binance Spot account state."""

    def __init__(
        self,
        settings: Settings,
        store: LiveStore,
        *,
        client: BinanceSpotClient | None = None,
    ):
        self.settings = settings
        self.config = settings.live_spot
        self.store = store
        self.instrument = _instrument(settings, self.config.instrument_id)
        if client is None:
            api_key, api_secret, credential_error = load_live_credentials(
                self.config.credentials_path,
                self.config.api_key_env,
                self.config.api_secret_env,
            )
        else:
            api_key, api_secret, credential_error = None, None, None
        self.client = client or BinanceSpotClient(
            self.config.api_base_url,
            api_key,
            api_secret,
            recv_window_ms=self.config.recv_window_ms,
        )
        self.credential_error = None if client is not None else credential_error
        self.strategy = ATRTickStrategy(
            period=settings.strategy.atr_period,
            multiplier=settings.strategy.atr_multiplier,
            bar_minutes=settings.strategy.bar_minutes,
            trend_efficiency_period=settings.strategy.trend_efficiency_period,
            minimum_trend_efficiency=settings.strategy.minimum_trend_efficiency,
            reversal_confirmation_atr=settings.strategy.reversal_confirmation_atr,
        )
        self.rules: SpotSymbolRules | None = None
        self.status = "STARTING"
        self.status_message = "Live Spot preflight has not run"
        self.public_capability = False
        self.signed_account_verified = False
        self.api_reading_enabled = False
        self.spot_trading_permitted = False
        self.withdrawals_enabled = False
        self.ip_restricted = False
        self.reconciliation_ok = False
        self.block_reasons: list[str] = []
        self.last_reconciled_at_ms: int | None = None
        self.last_trade_sync_at_ms: int | None = None
        self.last_tick: Tick | None = None
        self.base_free = Decimal("0")
        self.base_locked = Decimal("0")
        self.quote_free = Decimal("0")
        self.quote_locked = Decimal("0")
        self._engine: PaperEngine | None = None
        self._queue: asyncio.Queue[Tick] = asyncio.Queue()
        self._tick_task: asyncio.Task[None] | None = None
        self._reconcile_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._lock = asyncio.Lock()

    @property
    def activation_confirmed(self) -> bool:
        return os.getenv(self.config.activation_env) == self.config.activation_value

    @property
    def persisted_paused(self) -> bool:
        return self.store.metadata("trading_paused") == "true"

    @property
    def order_submission_ready(self) -> bool:
        return (
            self.config.enabled
            and self.config.allow_order_submission
            and self.activation_confirmed
            and self.client.has_credentials
            and self.public_capability
            and self.signed_account_verified
            and self.api_reading_enabled
            and self.spot_trading_permitted
            and not self.withdrawals_enabled
            and self.ip_restricted
            and self.reconciliation_ok
            and not self.persisted_paused
            and not self.block_reasons
        )

    async def start(self, engine: PaperEngine) -> None:
        self._engine = engine
        self._stopping = False
        await self.public_preflight()
        if not self.config.enabled:
            self.status = "DISABLED"
            self.status_message = "Live Spot runtime is disabled; real orders cannot be sent"
            return

        runtime = engine.runtimes.get(self.instrument.market_id)
        if runtime is None or not runtime.strategy_ready:
            self._block("MARKET_STRATEGY_NOT_READY")
            self.status = "BLOCKED"
            self.status_message = "SOXLB market strategy warm-up is unavailable"
            return
        history = await runtime.feed.history(self.settings.warmup_bars)
        self.strategy.bootstrap(history)
        saved_state = self.store.strategy_state(self.config.account_id)
        self.strategy.restore_runtime(saved_state)
        if saved_state is None:
            # A newly enabled account waits for a fresh cross instead of buying at startup.
            self.strategy.startup_alignment_checked = True

        engine.add_tick_listener(self.instrument.market_id, self.enqueue_tick)
        self._tick_task = asyncio.create_task(self._run_ticks(), name="soxlb-live-ticks")

        if not self.client.has_credentials:
            self._block(self.credential_error or "CREDENTIALS_MISSING")
            self.status = "BLOCKED"
            self.status_message = "Binance Spot credentials are missing"
            return

        try:
            await self.client.sync_time()
            await self.reconcile()
        except Exception as exc:
            self._block("SIGNED_PREFLIGHT_FAILED")
            self.status = "BLOCKED"
            self.status_message = f"Signed Binance preflight failed: {type(exc).__name__}: {exc}"
            self._event("ERROR", "SIGNED_PREFLIGHT_FAILED", self.status_message)
            return
        self._reconcile_task = asyncio.create_task(
            self._run_reconciliation(), name="soxlb-live-reconciliation"
        )
        self._refresh_status()

    async def stop(self) -> None:
        self._stopping = True
        if self._engine is not None:
            self._engine.remove_tick_listener(self.instrument.market_id, self.enqueue_tick)
        for task in (self._tick_task, self._reconcile_task):
            if task:
                task.cancel()
        for task in (self._tick_task, self._reconcile_task):
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await self.client.close()
        self.status = "STOPPED"
        self.status_message = "Live Spot runtime stopped"

    async def public_preflight(self) -> dict[str, Any]:
        try:
            self.rules = await self.client.symbol_rules(self.instrument.symbol)
            book = await self.client.book_ticker(self.instrument.symbol)
            blockers = []
            if self.rules.status != "TRADING":
                blockers.append("SYMBOL_NOT_TRADING")
            if not self.rules.market_order_allowed:
                blockers.append("MARKET_ORDERS_UNAVAILABLE")
            if not self.rules.quote_order_quantity_allowed:
                blockers.append("QUOTE_ORDER_QUANTITY_UNAVAILABLE")
            self.public_capability = not blockers and bool(book.get("bidPrice"))
            for reason in blockers:
                self._block(reason)
            return {
                "ok": self.public_capability,
                "symbol": self.rules.symbol,
                "status": self.rules.status,
                "market_order_allowed": self.rules.market_order_allowed,
                "quote_order_quantity_allowed": self.rules.quote_order_quantity_allowed,
                "minimum_notional": str(self.rules.minimum_notional),
                "quantity_step": str(self.rules.quantity_step),
                "bid_price": str(book.get("bidPrice")),
                "ask_price": str(book.get("askPrice")),
            }
        except Exception as exc:
            self.public_capability = False
            self._block("PUBLIC_PREFLIGHT_FAILED")
            self.status = "BLOCKED"
            self.status_message = f"Public Binance preflight failed: {type(exc).__name__}: {exc}"
            return {"ok": False, "error": self.status_message}

    async def enqueue_tick(self, tick: Tick) -> None:
        self._queue.put_nowait(tick)

    async def _run_ticks(self) -> None:
        while not self._stopping:
            tick = await self._queue.get()
            try:
                await self.process_tick(tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._block("TICK_PROCESSING_FAILED")
                self._event(
                    "ERROR",
                    "TICK_PROCESSING_FAILED",
                    f"{type(exc).__name__}: {exc}",
                )

    async def _run_reconciliation(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.config.reconcile_seconds)
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.reconciliation_ok = False
                self._block("RECONCILIATION_FAILED")
                self._refresh_status()
                self._event(
                    "ERROR",
                    "RECONCILIATION_FAILED",
                    f"{type(exc).__name__}: {exc}",
                )

    async def reconcile(self) -> None:
        if self.rules is None:
            raise RuntimeError("public symbol rules are unavailable")
        async with self._lock:
            account, open_orders, restrictions = await asyncio.gather(
                self.client.account(),
                self.client.open_orders(self.instrument.symbol),
                self.client.api_restrictions(),
            )
            self.signed_account_verified = True
            self.api_reading_enabled = bool(restrictions.get("enableReading", False))
            self.spot_trading_permitted = bool(
                restrictions.get("enableSpotAndMarginTrading", False)
            )
            self.withdrawals_enabled = bool(restrictions.get("enableWithdrawals", False))
            self.ip_restricted = bool(restrictions.get("ipRestrict", False))
            if not self.api_reading_enabled:
                self._block("READ_PERMISSION_MISSING")
            else:
                self._unblock("READ_PERMISSION_MISSING")
                self._unblock("SIGNED_PREFLIGHT_FAILED")
            if not self.spot_trading_permitted:
                self._block("SPOT_TRADING_PERMISSION_MISSING")
            else:
                self._unblock("SPOT_TRADING_PERMISSION_MISSING")
            if self.withdrawals_enabled:
                self._block("WITHDRAWAL_PERMISSION_ENABLED")
            else:
                self._unblock("WITHDRAWAL_PERMISSION_ENABLED")
            if not self.ip_restricted:
                self._block("IP_RESTRICTION_DISABLED")
            else:
                self._unblock("IP_RESTRICTION_DISABLED")

            balances = {item["asset"]: item for item in account.get("balances", [])}
            base = balances.get(self.rules.base_asset, {})
            quote = balances.get(self.rules.quote_asset, {})
            self.base_free = Decimal(str(base.get("free", "0")))
            self.base_locked = Decimal(str(base.get("locked", "0")))
            self.quote_free = Decimal(str(quote.get("free", "0")))
            self.quote_locked = Decimal(str(quote.get("locked", "0")))
            book = await self.client.book_ticker(self.instrument.symbol)
            reference_price = Decimal(str(book["bidPrice"]))
            equity = (
                self.quote_free
                + self.quote_locked
                + (self.base_free + self.base_locked) * reference_price
            )
            now_ms = _now_ms()
            self.store.save_balance_snapshot(
                account_id=self.config.account_id,
                timestamp_ms=now_ms,
                base_free=str(self.base_free),
                base_locked=str(self.base_locked),
                quote_free=str(self.quote_free),
                quote_locked=str(self.quote_locked),
                reference_price=str(reference_price),
                equity_quote=str(equity),
            )

            known_ids = {
                row["client_order_id"] for row in self.store.orders(self.config.account_id, 10_000)
            }
            unknown = [row for row in open_orders if row.get("clientOrderId") not in known_ids]
            if unknown:
                self._block("UNKNOWN_OPEN_ORDERS")
            else:
                self._unblock("UNKNOWN_OPEN_ORDERS")

            total_base = self.base_free + self.base_locked
            managed = self.store.metadata("managed_position") == "true"
            has_material_position = (
                total_base >= self.rules.minimum_quantity
                and total_base * reference_price >= self.rules.minimum_notional
            )
            if has_material_position and not managed:
                if self.config.adopt_existing_position:
                    self.store.set_metadata("managed_position", "true", now_ms)
                    self._event(
                        "WARN",
                        "POSITION_ADOPTED",
                        "Existing SOXLB balance adopted into the live strategy",
                        {"quantity": str(total_base)},
                    )
                else:
                    self._block("UNMANAGED_EXISTING_POSITION")
            else:
                self._unblock("UNMANAGED_EXISTING_POSITION")

            for pending in self.store.pending_orders(self.config.account_id):
                await self._reconcile_order(pending, now_ms)
            if (
                self.last_trade_sync_at_ms is None
                or now_ms - self.last_trade_sync_at_ms
                >= self.config.trade_sync_seconds * 1000
            ):
                await self._sync_account_trades(now_ms)
            self.last_reconciled_at_ms = now_ms
            self.reconciliation_ok = (
                self.signed_account_verified
                and self.api_reading_enabled
                and not unknown
            )
            self._unblock("RECONCILIATION_FAILED")
            self._refresh_status()

    async def _sync_account_trades(self, now_ms: int) -> None:
        trades = await self.client.my_trades(self.instrument.symbol)
        for trade in trades:
            order_id = int(trade["orderId"])
            client_order_id = f"binance-sync-{order_id}"
            side = Side.BUY.value if bool(trade.get("isBuyer", False)) else Side.SELL.value
            quote_quantity = str(
                trade.get("quoteQty")
                or Decimal(str(trade["price"])) * Decimal(str(trade["qty"]))
            )
            self.store.create_order(
                client_order_id=client_order_id,
                account_id=self.config.account_id,
                symbol=self.instrument.symbol,
                side=side,
                reason="binance_readonly_sync",
                signal_price=str(trade["price"]),
                signal_at_ms=int(trade["time"]),
                requested_quantity=str(trade["qty"]),
                requested_quote_quantity=None,
            )
            self.store.update_order(
                client_order_id,
                status="FILLED",
                updated_at_ms=now_ms,
                submitted_at_ms=int(trade["time"]),
                payload={
                    "orderId": order_id,
                    "executedQty": str(trade["qty"]),
                    "cummulativeQuoteQty": quote_quantity,
                    "source": "binance_my_trades",
                },
            )
            self.store.upsert_fill(
                account_id=self.config.account_id,
                symbol=self.instrument.symbol,
                side=side,
                client_order_id=client_order_id,
                payload=trade,
            )
        self.last_trade_sync_at_ms = now_ms

    async def _reconcile_order(self, order: dict[str, Any], now_ms: int) -> None:
        client_order_id = order["client_order_id"]
        try:
            payload = await self.client.query_order(
                self.instrument.symbol, client_order_id=client_order_id
            )
        except BinanceSpotAPIError as exc:
            if exc.code == -2013 and order["status"] == "CREATED":
                return
            raise
        status = str(payload["status"])
        submitted_at_ms = order.get("submitted_at_ms") or order["signal_at_ms"]
        if (
            status in {"NEW", "PARTIALLY_FILLED"}
            and now_ms - int(submitted_at_ms) >= self.config.order_timeout_seconds * 1000
        ):
            try:
                payload = await self.client.cancel_order(
                    self.instrument.symbol, client_order_id
                )
                status = str(payload["status"])
            except BinanceSpotAPIError as exc:
                if exc.code not in {-2011, -2013}:
                    raise
                payload = await self.client.query_order(
                    self.instrument.symbol, client_order_id=client_order_id
                )
                status = str(payload["status"])
        self.store.update_order(
            client_order_id,
            status=status,
            updated_at_ms=now_ms,
            payload=payload,
        )
        await self._ingest_order_trades(order, payload)
        if status in TERMINAL_ORDER_STATES:
            self._apply_terminal_strategy_result(client_order_id, now_ms)

    async def _ingest_order_trades(self, order: dict[str, Any], payload: dict[str, Any]) -> None:
        order_id = payload.get("orderId") or order.get("exchange_order_id")
        if order_id is None:
            return
        trades = await self.client.my_trades(self.instrument.symbol, order_id=int(order_id))
        for trade in trades:
            self.store.upsert_fill(
                account_id=self.config.account_id,
                symbol=self.instrument.symbol,
                side=order["side"],
                client_order_id=order["client_order_id"],
                payload=trade,
            )

    async def process_tick(self, tick: Tick) -> None:
        async with self._lock:
            self.last_tick = tick
            total_base = self.base_free + self.base_locked
            has_position = False
            if self.rules is not None:
                has_position = (
                    total_base >= self.rules.minimum_quantity
                    and total_base * tick.price >= self.rules.minimum_notional
                )
            pending = bool(self.store.pending_orders(self.config.account_id))
            signal = self.strategy.on_tick(
                tick,
                has_position=has_position,
                has_pending_order=pending,
                allow_short=False,
                is_short=False,
                emit_signals=self.order_submission_ready,
            )
            self.store.save_strategy_state(
                self.config.account_id, self.strategy.runtime_state(), tick.timestamp_ms
            )
            if signal is not None:
                await self._submit_signal(signal, tick)

    async def _submit_signal(self, signal: StrategySignal, tick: Tick) -> None:
        if not self.order_submission_ready or self.rules is None:
            return
        risk_reason = await self._risk_rejection(signal, tick)
        if risk_reason:
            self._event("WARN", "ORDER_RISK_BLOCKED", risk_reason)
            return

        client_order_id = _client_order_id(self.config.account_id, signal)
        requested_quantity: Decimal | None = None
        requested_quote_quantity: Decimal | None = None
        if signal.side is Side.BUY:
            spendable = max(Decimal("0"), self.quote_free - Decimal(str(self.config.quote_reserve)))
            requested_quote_quantity = min(
                spendable * Decimal(str(self.config.position_fraction)),
                Decimal(str(self.config.max_order_notional)),
            )
            if requested_quote_quantity < self.rules.minimum_notional:
                self._event("WARN", "ORDER_RISK_BLOCKED", "INSUFFICIENT_QUOTE_BALANCE")
                return
        else:
            requested_quantity = self.rules.floor_quantity(self.base_free)
            if (
                requested_quantity < self.rules.minimum_quantity
                or requested_quantity * tick.price < self.rules.minimum_notional
            ):
                self._event("WARN", "ORDER_RISK_BLOCKED", "INSUFFICIENT_BASE_BALANCE")
                return

        created = self.store.create_order(
            client_order_id=client_order_id,
            account_id=self.config.account_id,
            symbol=self.instrument.symbol,
            side=signal.side.value,
            reason=signal.reason,
            signal_price=str(signal.signal_price),
            signal_at_ms=signal.signal_at_ms or tick.timestamp_ms,
            requested_quantity=str(requested_quantity) if requested_quantity else None,
            requested_quote_quantity=(
                str(requested_quote_quantity) if requested_quote_quantity else None
            ),
        )
        if not created:
            return
        now_ms = _now_ms()
        self.store.update_order(
            client_order_id,
            status="SUBMITTING",
            updated_at_ms=now_ms,
            submitted_at_ms=now_ms,
        )
        try:
            if signal.side is Side.BUY:
                assert requested_quote_quantity is not None
                payload = await self.client.market_buy(
                    self.instrument.symbol, requested_quote_quantity, client_order_id
                )
            else:
                assert requested_quantity is not None
                payload = await self.client.market_sell(
                    self.instrument.symbol, requested_quantity, client_order_id
                )
        except BinanceSpotAPIError as exc:
            if exc.code in AMBIGUOUS_BINANCE_CODES:
                self._event(
                    "ERROR",
                    "ORDER_RESULT_UNKNOWN",
                    "Binance did not confirm the result; reconciliation will query by client ID",
                    {"client_order_id": client_order_id, "code": exc.code},
                )
                return
            self.store.update_order(
                client_order_id,
                status="REJECTED",
                updated_at_ms=_now_ms(),
                payload={"code": exc.code, "msg": exc.message},
            )
            self.strategy.on_fill(tick.timestamp_ms, filled=False)
            self._event(
                "ERROR",
                "ORDER_REJECTED",
                exc.message,
                {"client_order_id": client_order_id, "code": exc.code},
            )
            return
        status = str(payload["status"])
        self.store.update_order(
            client_order_id,
            status=status,
            updated_at_ms=_now_ms(),
            payload=payload,
        )
        order = self.store.order(client_order_id)
        if order is not None:
            await self._ingest_order_trades(order, payload)
        if status in TERMINAL_ORDER_STATES:
            self._apply_terminal_strategy_result(client_order_id, _now_ms())
        self._event(
            "INFO",
            "ORDER_ACCEPTED",
            f"Binance accepted {signal.side.value} market order",
            {"client_order_id": client_order_id, "status": status},
        )

    async def _risk_rejection(self, signal: StrategySignal, tick: Tick) -> str | None:
        if self.persisted_paused:
            return "LIVE_TRADING_PAUSED"
        if signal.side is Side.BUY:
            day_start_ms = tick.timestamp_ms // 86_400_000 * 86_400_000
            if self.store.order_count_since(self.config.account_id, day_start_ms) >= (
                self.config.max_orders_per_day
            ):
                return "DAILY_ORDER_LIMIT"
            latest = self.store.latest_balance(self.config.account_id)
            initial = self.store.day_start_equity(self.config.account_id, day_start_ms)
            if latest and latest.get("equity_quote") and initial:
                loss = Decimal(initial) - Decimal(latest["equity_quote"])
                if loss >= Decimal(str(self.config.max_daily_loss)):
                    return "DAILY_LOSS_ENTRY_LIMIT"
        book = await self.client.book_ticker(self.instrument.symbol)
        execution_price = Decimal(
            str(book["askPrice"] if signal.side is Side.BUY else book["bidPrice"])
        )
        if tick.price <= 0:
            return "INVALID_REFERENCE_PRICE"
        slippage_bps = abs(execution_price - tick.price) / tick.price * Decimal("10000")
        if (
            signal.side is Side.BUY
            and slippage_bps > Decimal(str(self.config.max_slippage_bps))
        ):
            return "SLIPPAGE_LIMIT"
        return None

    def _apply_terminal_strategy_result(
        self, client_order_id: str, timestamp_ms: int
    ) -> None:
        marker = f"strategy_fill_applied:{client_order_id}"
        if self.store.metadata(marker) is not None:
            return
        order = self.store.order(client_order_id)
        if order is None:
            return
        status = str(order["status"])
        executed_quantity = Decimal(str(order["executed_quantity"]))
        filled = executed_quantity > 0
        self.strategy.on_fill(timestamp_ms, filled=filled)
        self.store.set_metadata(marker, status, timestamp_ms)
        if filled:
            if order["side"] == "BUY":
                self.store.set_metadata("managed_position", "true", timestamp_ms)
            elif status == "FILLED":
                self.store.set_metadata("managed_position", "false", timestamp_ms)

    def readiness(self) -> dict[str, Any]:
        return {
            "account_id": self.config.account_id,
            "symbol": self.instrument.symbol,
            "enabled": self.config.enabled,
            "status": self.status,
            "status_message": self.status_message,
            "public_capability": self.public_capability,
            "credentials_present": self.client.has_credentials,
            "credential_file_secure": self.credential_error is None,
            "signed_account_verified": self.signed_account_verified,
            "api_reading_enabled": self.api_reading_enabled,
            "spot_trading_permitted": self.spot_trading_permitted,
            "withdrawals_enabled": self.withdrawals_enabled,
            "ip_restricted": self.ip_restricted,
            "allow_order_submission": self.config.allow_order_submission,
            "activation_confirmed": self.activation_confirmed,
            "order_submission_ready": self.order_submission_ready,
            "persisted_paused": self.persisted_paused,
            "reconciliation_ok": self.reconciliation_ok,
            "last_reconciled_at_ms": self.last_reconciled_at_ms,
            "last_trade_sync_at_ms": self.last_trade_sync_at_ms,
            "synced_trade_count": self.store.fill_count(self.config.account_id),
            "block_reasons": sorted(self.block_reasons),
            "strategy": _json_decimals(asdict(self.strategy.view())),
            "database": str(self.config.database_path),
            "risk_limits": {
                "position_fraction": self.config.position_fraction,
                "max_order_notional": self.config.max_order_notional,
                "quote_reserve": self.config.quote_reserve,
                "max_slippage_bps": self.config.max_slippage_bps,
                "max_daily_loss": self.config.max_daily_loss,
                "max_orders_per_day": self.config.max_orders_per_day,
            },
        }

    def _refresh_status(self) -> None:
        if self.order_submission_ready:
            self.status = "ARMED"
            self.status_message = "Live Spot execution is armed"
        elif self.config.enabled and self.signed_account_verified:
            self.status = "OBSERVE_ONLY"
            self.status_message = "Signed reconciliation active; order gates remain closed"
        elif self.config.enabled:
            self.status = "BLOCKED"
            self.status_message = "Live Spot execution is blocked"

    def _block(self, reason: str) -> None:
        if reason not in self.block_reasons:
            self.block_reasons.append(reason)

    def _unblock(self, reason: str) -> None:
        if reason in self.block_reasons:
            self.block_reasons.remove(reason)

    def _event(
        self,
        level: str,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.store.add_event(self.config.account_id, _now_ms(), level, code, message, details)


def _client_order_id(account_id: str, signal: StrategySignal) -> str:
    raw = f"{account_id}:{signal.side.value}:{signal.tick_id}:{signal.bar_start_ms}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:18]
    return f"mmtick-{signal.side.value.lower()}-{digest}"


def _instrument(settings: Settings, instrument_id: str) -> InstrumentSettings:
    for instrument in settings.instruments:
        if instrument.id == instrument_id:
            return instrument
    raise LookupError(instrument_id)


def load_live_credentials(
    credentials_path: Path | None,
    api_key_name: str,
    api_secret_name: str,
) -> tuple[str | None, str | None, str | None]:
    api_key = os.getenv(api_key_name)
    api_secret = os.getenv(api_secret_name)
    if api_key and api_secret:
        return api_key, api_secret, None
    if credentials_path is None or not credentials_path.exists():
        return api_key, api_secret, None
    mode = credentials_path.stat().st_mode & 0o777
    if mode & 0o077:
        return None, None, "CREDENTIAL_FILE_PERMISSIONS_INSECURE"
    values: dict[str, str] = {}
    for raw_line in credentials_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key in {api_key_name, api_secret_name}:
            values[key] = value
    return (
        api_key or values.get(api_key_name),
        api_secret or values.get(api_secret_name),
        None,
    )


def _json_decimals(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_decimals(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_decimals(item) for item in value]
    return value


def _now_ms() -> int:
    return int(time.time() * 1000)
