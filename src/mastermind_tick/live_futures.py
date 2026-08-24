"""Credential-gated Binance USD-M Futures execution for SOXLUSDT."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import time
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from mastermind_tick.binance_futures import (
    BinanceFuturesAPIError,
    BinanceFuturesClient,
    BinanceFuturesRateLimitError,
    FuturesSymbolRules,
)
from mastermind_tick.config import InstrumentSettings, Settings
from mastermind_tick.engine import PaperEngine
from mastermind_tick.live_spot import load_live_credentials
from mastermind_tick.live_store import LiveStore
from mastermind_tick.models import Side, StrategySignal, Tick
from mastermind_tick.strategy import (
    ATRProfitProtection,
    ATRTickStrategy,
    atr_profit_protection_signal,
)

TERMINAL_ORDER_STATES = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
AMBIGUOUS_BINANCE_CODES = {-1006, -1007}


class LiveOperationError(RuntimeError):
    """A safe operator action could not be completed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class LiveFuturesTrader:
    """Run the configured ATR strategy against the actual USD-M account."""

    def __init__(
        self,
        settings: Settings,
        store: LiveStore,
        *,
        client: BinanceFuturesClient | None = None,
    ):
        self.settings = settings
        self.config = settings.live_futures
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
        self.client = client or BinanceFuturesClient(
            self.config.api_base_url,
            api_key,
            api_secret,
            spot_api_base_url=self.config.spot_api_base_url,
            recv_window_ms=self.config.recv_window_ms,
        )
        self.credential_error = None if client is not None else credential_error
        self.strategy = ATRTickStrategy(
            period=self.config.atr_period,
            multiplier=self.config.atr_multiplier,
            bar_minutes=settings.strategy.bar_minutes,
            trend_efficiency_period=self.config.trend_efficiency_period,
            minimum_trend_efficiency=self.config.minimum_trend_efficiency,
            reversal_confirmation_atr=self.config.reversal_confirmation_atr,
        )
        self.profit_protection = (
            ATRProfitProtection(
                self.config.profit_activation_atr,
                self.config.profit_trailing_atr,
            )
            if self.config.profit_activation_atr > 0 and self.config.profit_trailing_atr > 0
            else None
        )
        self.continuation_direction: str | None = None
        self.continuation_anchor: Decimal | None = None
        self.continuation_eligible_bar_ms: int | None = None
        self.rules: FuturesSymbolRules | None = None
        self.status = "STARTING"
        self.status_message = "Live Futures preflight has not run"
        self.public_capability = False
        self.signed_account_verified = False
        self.api_reading_enabled = False
        self.futures_trading_permitted = False
        self.withdrawals_enabled = False
        self.ip_restricted = False
        self.reconciliation_ok = False
        self.block_reasons: list[str] = []
        self.last_reconciled_at_ms: int | None = None
        self.last_account_gate_sync_at_ms: int | None = None
        self.last_trade_sync_at_ms: int | None = None
        self.last_tick: Tick | None = None
        self.wallet_balance = Decimal("0")
        self.margin_balance = Decimal("0")
        self.available_balance = Decimal("0")
        self.unrealized_pnl = Decimal("0")
        self.position_quantity = Decimal("0")
        self.entry_price = Decimal("0")
        self.mark_price = Decimal("0")
        self.liquidation_price: Decimal | None = None
        self.current_leverage = 0
        self.current_margin_mode = "unknown"
        self.current_position_mode = "unknown"
        self.multi_assets_enabled = False
        self._active_intent: dict[str, Any] | None = None
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
    def test_order_passed(self) -> bool:
        return self.store.metadata("futures_test_order_passed") == "true"

    @property
    def strategy_resume_ready(self) -> bool:
        return (
            self.config.enabled
            and self.config.allow_order_submission
            and self.activation_confirmed
            and self.client.has_credentials
            and self.public_capability
            and self.signed_account_verified
            and self.api_reading_enabled
            and self.futures_trading_permitted
            and not self.withdrawals_enabled
            and self.ip_restricted
            and self.reconciliation_ok
            and self.test_order_passed
            and self._client_rate_limit_cooldown() <= 0
            and not self.block_reasons
        )

    @property
    def order_submission_ready(self) -> bool:
        return self.strategy_resume_ready and not self.persisted_paused

    async def start(self, engine: PaperEngine) -> None:
        self._engine = engine
        self._stopping = False
        await self.public_preflight()
        if not self.config.enabled:
            self.status = "DISABLED"
            self.status_message = "Live Futures runtime is disabled"
            return
        runtime = next(
            (
                value
                for value in engine.runtimes.values()
                if value.instrument.market_id == self.instrument.market_id
            ),
            None,
        )
        if runtime is None or not runtime.strategy_ready:
            self._block("MARKET_STRATEGY_NOT_READY")
            self.status = "BLOCKED"
            self.status_message = "SOXL Futures market strategy warm-up is unavailable"
            return
        history = await runtime.feed.history(self.settings.warmup_bars)
        self.strategy.bootstrap(history)
        saved_state = self.store.strategy_state(self.config.account_id)
        self.strategy.restore_runtime(saved_state)
        self._restore_continuation_state(saved_state)
        if self.profit_protection is not None and saved_state is not None:
            self.profit_protection.restore_runtime(saved_state.get("profit_protection"))
        self._active_intent = self.store.active_execution_intent(self.config.account_id)
        # LIVE must never chase the currently observed trend after either a first
        # start or a strategy-parameter migration. It only acts on a fresh cross.
        self.strategy.startup_alignment_checked = True
        engine.add_tick_listener(self.instrument.market_id, self.enqueue_tick)
        self._tick_task = asyncio.create_task(self._run_ticks(), name="soxl-perp-live-ticks")
        if not self.client.has_credentials:
            self._block(self.credential_error or "CREDENTIALS_MISSING")
            self.status = "BLOCKED"
            self.status_message = "Binance Futures credentials are missing"
            return
        try:
            await self.client.sync_time()
            await self.reconcile()
        except Exception as exc:
            self._block("SIGNED_PREFLIGHT_FAILED")
            self.status = "BLOCKED"
            self.status_message = (
                f"Signed Binance Futures preflight failed: {type(exc).__name__}: {exc}"
            )
            self._event("ERROR", "SIGNED_PREFLIGHT_FAILED", self.status_message)
        self._reconcile_task = asyncio.create_task(
            self._run_reconciliation(), name="soxl-perp-live-reconciliation"
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
        self.status_message = "Live Futures runtime stopped"

    async def set_strategy_paused(self, paused: bool) -> dict[str, Any]:
        """Persistently stop or resume strategy-driven order submission."""
        async with self._lock:
            return self._persist_strategy_paused(paused, _now_ms())

    async def resume_strategy(self) -> dict[str, Any]:
        """Refresh signed account gates, then persistently arm strategy execution."""
        try:
            await self.reconcile()
        except Exception as exc:
            raise LiveOperationError(
                "LIVE_RESUME_RECONCILIATION_FAILED",
                f"Fresh Binance reconciliation failed: {type(exc).__name__}: {exc}",
            ) from exc
        async with self._lock:
            if not self.strategy_resume_ready:
                reasons = ", ".join(sorted(self.block_reasons)) or "execution gates are not ready"
                raise LiveOperationError(
                    "LIVE_RESUME_BLOCKED",
                    f"LIVE strategy cannot resume: {reasons}",
                )
            return self._persist_strategy_paused(False, _now_ms())

    def _persist_strategy_paused(self, paused: bool, timestamp_ms: int) -> dict[str, Any]:
        self.store.set_metadata("trading_paused", "true" if paused else "false", timestamp_ms)
        if (
            paused
            and self._active_intent is not None
            and self._active_intent["status"]
            in {
                "PENDING",
                "VALIDATING",
            }
        ):
            self._cancel_active_intent("OPERATOR_STOPPED", timestamp_ms)
        self._event(
            "WARN" if paused else "INFO",
            "STRATEGY_STOPPED" if paused else "STRATEGY_RESUMED",
            "Live Futures strategy stopped by operator"
            if paused
            else "Live Futures strategy resumed by operator",
            timestamp_ms=timestamp_ms,
        )
        self._refresh_status()
        return {
            "ok": True,
            "strategy_paused": self.persisted_paused,
            "order_submission_ready": self.order_submission_ready,
        }

    async def manual_flatten(self) -> dict[str, Any]:
        """Close every SOXL leg found by a fresh signed position query."""
        async with self._lock:
            if not self.config.enabled:
                raise LiveOperationError("LIVE_RUNTIME_DISABLED", "LIVE runtime is disabled")
            if not self.client.has_credentials or not self.signed_account_verified:
                raise LiveOperationError(
                    "SIGNED_ACCOUNT_UNAVAILABLE", "Signed Futures account is unavailable"
                )
            if not self.public_capability or not self.futures_trading_permitted:
                raise LiveOperationError(
                    "FUTURES_TRADING_UNAVAILABLE", "Futures trading is unavailable"
                )
            if not self.ip_restricted:
                raise LiveOperationError(
                    "IP_RESTRICTION_DISABLED", "API key IP restriction is disabled"
                )
            if self.store.pending_orders(self.config.account_id):
                raise LiveOperationError(
                    "PENDING_ORDER_PRESENT", "A managed order is still pending"
                )

            positions, open_orders = await asyncio.gather(
                self.client.position_risk(self.instrument.symbol),
                self.client.open_orders(self.instrument.symbol),
            )
            if open_orders:
                raise LiveOperationError(
                    "OPEN_ORDER_PRESENT", "Cancel or reconcile open orders before flattening"
                )
            legs: list[tuple[str, str, Decimal]] = []
            for row in positions:
                quantity = Decimal(str(row.get("positionAmt", "0")))
                if quantity == 0:
                    continue
                position_side = str(row.get("positionSide", "BOTH"))
                if position_side == "LONG":
                    side = "SELL"
                elif position_side == "SHORT":
                    side = "BUY"
                else:
                    side = "SELL" if quantity > 0 else "BUY"
                legs.append((side, position_side, abs(quantity)))

            if not legs:
                return {
                    "ok": True,
                    "already_flat": True,
                    "flat_confirmed": True,
                    "orders": [],
                }

            book = await self.client.book_ticker(self.instrument.symbol)
            results: list[dict[str, Any]] = []
            for index, (side, position_side, quantity) in enumerate(legs):
                now_ms = _now_ms()
                signal_price = Decimal(
                    str(book["bidPrice"] if side == "SELL" else book["askPrice"])
                )
                client_order_id = _manual_close_client_order_id(position_side, now_ms, index)
                created = self.store.create_order(
                    client_order_id=client_order_id,
                    account_id=self.config.account_id,
                    symbol=self.instrument.symbol,
                    side=side,
                    position_side=position_side,
                    reduce_only=True,
                    reason="operator_manual_flatten",
                    signal_price=str(signal_price),
                    signal_at_ms=now_ms,
                    requested_quantity=str(quantity),
                    requested_quote_quantity=None,
                )
                if not created:
                    raise LiveOperationError(
                        "DUPLICATE_CLOSE_ORDER", "Could not create a unique close order"
                    )
                self.store.update_order(
                    client_order_id,
                    status="SUBMITTING",
                    updated_at_ms=now_ms,
                    submitted_at_ms=now_ms,
                )
                try:
                    payload = await self.client.market_order(
                        symbol=self.instrument.symbol,
                        side=side,
                        position_side=position_side,
                        quantity=quantity,
                        client_order_id=client_order_id,
                    )
                except BinanceFuturesAPIError as exc:
                    if exc.code in AMBIGUOUS_BINANCE_CODES:
                        self._event(
                            "ERROR",
                            "MANUAL_CLOSE_RESULT_UNKNOWN",
                            "Manual close result requires reconciliation",
                        )
                        raise LiveOperationError(
                            "MANUAL_CLOSE_RESULT_UNKNOWN",
                            "Close order result is unknown; do not retry before reconciliation",
                        ) from exc
                    self.store.update_order(
                        client_order_id,
                        status="REJECTED",
                        updated_at_ms=_now_ms(),
                        payload={"code": exc.code, "msg": exc.message},
                    )
                    self._event("ERROR", "MANUAL_CLOSE_REJECTED", exc.message)
                    raise LiveOperationError("MANUAL_CLOSE_REJECTED", exc.message) from exc

                status = str(payload["status"])
                self.store.update_order(
                    client_order_id,
                    status=status,
                    updated_at_ms=_now_ms(),
                    payload=payload,
                )
                await self._ingest_order_trades(client_order_id, int(payload["orderId"]))
                if status in TERMINAL_ORDER_STATES:
                    self._apply_terminal_strategy_result(client_order_id, _now_ms())
                results.append(
                    {
                        "client_order_id": client_order_id,
                        "side": side,
                        "position_side": position_side,
                        "quantity": str(quantity),
                        "status": status,
                    }
                )

            flat_confirmed = all(row["status"] == "FILLED" for row in results)
            completed_at_ms = _now_ms()
            if flat_confirmed:
                self.position_quantity = Decimal("0")
                self.entry_price = Decimal("0")
                self.unrealized_pnl = Decimal("0")
                self.strategy.on_manual_flatten(completed_at_ms)
                if self.profit_protection is not None:
                    self.profit_protection.reset()
                self.store.save_strategy_state(
                    self.config.account_id,
                    self._runtime_state(),
                    completed_at_ms,
                )
            self._event(
                "WARN",
                "MANUAL_FLATTEN_SUBMITTED",
                "Operator submitted market close for all SOXL Futures legs",
                timestamp_ms=completed_at_ms,
                details={"flat_confirmed": flat_confirmed, "orders": results},
            )
            return {
                "ok": True,
                "already_flat": False,
                "flat_confirmed": flat_confirmed,
                "orders": results,
            }

    async def public_preflight(self) -> dict[str, Any]:
        try:
            self.rules = await self.client.symbol_rules(self.instrument.symbol)
            book = await self.client.book_ticker(self.instrument.symbol)
            blockers = []
            if self.rules.status != "TRADING":
                blockers.append("SYMBOL_NOT_TRADING")
            if not self.rules.market_order_allowed:
                blockers.append("MARKET_ORDERS_UNAVAILABLE")
            self.public_capability = not blockers and bool(book.get("bidPrice"))
            for reason in blockers:
                self._block(reason)
            return {
                "ok": self.public_capability,
                "symbol": self.rules.symbol,
                "status": self.rules.status,
                "market_order_allowed": self.rules.market_order_allowed,
                "minimum_notional": str(self.rules.minimum_notional),
                "quantity_step": str(self.rules.quantity_step),
            }
        except Exception as exc:
            self.public_capability = False
            self._block("PUBLIC_PREFLIGHT_FAILED")
            self.status = "BLOCKED"
            self.status_message = (
                f"Public Binance Futures preflight failed: {type(exc).__name__}: {exc}"
            )
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
                self._event("ERROR", "TICK_PROCESSING_FAILED", f"{type(exc).__name__}: {exc}")

    async def _run_reconciliation(self) -> None:
        delay = float(self.config.reconcile_seconds)
        while not self._stopping:
            await asyncio.sleep(delay)
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except BinanceFuturesRateLimitError as exc:
                delay = max(
                    float(self.config.reconcile_seconds),
                    math.ceil(exc.retry_after_seconds),
                )
                self._mark_reconciliation_failed("RATE_LIMIT_BACKOFF")
                self._event(
                    "ERROR",
                    "RATE_LIMIT_BACKOFF",
                    f"Binance request cooldown active for {delay:.0f} seconds",
                    details={"retry_after_seconds": delay, "status_code": exc.status_code},
                )
            except Exception as exc:
                delay = min(max(delay * 2, float(self.config.reconcile_seconds)), 60.0)
                self._mark_reconciliation_failed("RECONCILIATION_FAILED")
                self._event("ERROR", "RECONCILIATION_FAILED", f"{type(exc).__name__}: {exc}")
            else:
                delay = float(self.config.reconcile_seconds)

    async def reconcile(self) -> None:
        if self.rules is None:
            raise RuntimeError("public Futures symbol rules are unavailable")
        async with self._lock:
            now_ms = _now_ms()
            refresh_account_gates = (
                self.last_account_gate_sync_at_ms is None
                or now_ms - self.last_account_gate_sync_at_ms
                >= self.config.account_gate_sync_seconds * 1000
            )
            # Keep the high-frequency safety view small and sequential. In particular,
            # symbol-scoped openOrders is far cheaper than the account-wide endpoint.
            account = await self.client.account()
            positions = await self.client.position_risk(self.instrument.symbol)
            open_orders = await self.client.open_orders(self.instrument.symbol)
            self.signed_account_verified = True
            self.futures_trading_permitted = bool(account.get("canTrade", False))
            self._set_gate("FUTURES_TRADING_PERMISSION_MISSING", not self.futures_trading_permitted)
            if refresh_account_gates:
                restrictions = await self.client.api_restrictions()
                position_mode = await self.client.position_mode()
                multi_assets_mode = await self.client.multi_assets_mode()
                self.api_reading_enabled = bool(restrictions.get("enableReading", False))
                self.withdrawals_enabled = bool(restrictions.get("enableWithdrawals", False))
                self.ip_restricted = bool(restrictions.get("ipRestrict", False))
                self.current_position_mode = (
                    "hedge" if bool(position_mode.get("dualSidePosition")) else "one_way"
                )
                self.multi_assets_enabled = bool(multi_assets_mode.get("multiAssetsMargin"))
                self._set_gate("READ_PERMISSION_MISSING", not self.api_reading_enabled)
                self._set_gate("WITHDRAWAL_PERMISSION_ENABLED", self.withdrawals_enabled)
                self._set_gate("IP_RESTRICTION_DISABLED", not self.ip_restricted)
                self._set_gate(
                    "POSITION_MODE_MISMATCH",
                    self.current_position_mode != self.config.position_mode,
                )
                self._set_gate("MULTI_ASSET_MODE_ENABLED", self.multi_assets_enabled)
                self.last_account_gate_sync_at_ms = now_ms
            self._set_gate("FUTURES_TEST_ORDER_REQUIRED", not self.test_order_passed)
            other_positions = [
                row
                for row in account.get("positions", [])
                if row.get("symbol") != self.instrument.symbol
                and Decimal(str(row.get("positionAmt", "0"))) != 0
            ]
            self._set_gate("OTHER_OPEN_POSITIONS", bool(other_positions))
            self._unblock("SIGNED_PREFLIGHT_FAILED")

            long_row = next((row for row in positions if row.get("positionSide") == "LONG"), {})
            short_row = next((row for row in positions if row.get("positionSide") == "SHORT"), {})
            both_row = next((row for row in positions if row.get("positionSide") == "BOTH"), {})
            long_quantity = Decimal(str(long_row.get("positionAmt", "0")))
            short_quantity = Decimal(str(short_row.get("positionAmt", "0")))
            both_quantity = Decimal(str(both_row.get("positionAmt", "0")))
            both_legs = long_quantity != 0 and short_quantity != 0
            self._set_gate("SIMULTANEOUS_LONG_SHORT_POSITION", both_legs)
            self._set_gate(
                "SHORT_POSITION_NOT_ALLOWED",
                not self.config.allow_short and short_quantity != 0,
            )
            self.position_quantity = long_quantity + short_quantity + both_quantity
            active = (
                long_row
                if long_quantity
                else short_row
                if short_quantity
                else both_row
                if both_quantity
                else long_row or short_row or both_row
            )
            self.wallet_balance = Decimal(str(account.get("totalWalletBalance", "0")))
            self.margin_balance = Decimal(str(account.get("totalMarginBalance", "0")))
            self.available_balance = Decimal(str(account.get("availableBalance", "0")))
            self.unrealized_pnl = Decimal(str(account.get("totalUnrealizedProfit", "0")))
            self.entry_price = Decimal(str(active.get("entryPrice", "0")))
            self.mark_price = Decimal(str(active.get("markPrice", "0")))
            if self.mark_price <= 0:
                book = await self.client.book_ticker(self.instrument.symbol)
                self.mark_price = Decimal(str(book["bidPrice"]))
            liquidation = Decimal(str(active.get("liquidationPrice", "0")))
            self.liquidation_price = liquidation if liquidation > 0 else None
            self.current_leverage = int(active.get("leverage", 0) or 0)
            self.current_margin_mode = str(active.get("marginType", "unknown")).lower()
            self._set_gate("LEVERAGE_MISMATCH", self.current_leverage != self.config.leverage)
            self._set_gate(
                "MARGIN_MODE_MISMATCH",
                self.current_margin_mode != self.config.margin_mode,
            )

            strategy_view = self.strategy.view()
            self.store.save_futures_snapshot(
                account_id=self.config.account_id,
                timestamp_ms=now_ms,
                wallet_balance=str(self.wallet_balance),
                margin_balance=str(self.margin_balance),
                available_balance=str(self.available_balance),
                unrealized_pnl=str(self.unrealized_pnl),
                position_quantity=str(self.position_quantity),
                entry_price=str(self.entry_price),
                mark_price=str(self.mark_price),
                liquidation_price=(str(self.liquidation_price) if self.liquidation_price else None),
                leverage=self.current_leverage,
                margin_type=self.current_margin_mode,
                position_side=(
                    "LONG"
                    if self.position_quantity > 0
                    else "SHORT"
                    if self.position_quantity < 0
                    else "FLAT"
                ),
                atr=str(strategy_view.atr) if strategy_view.atr is not None else None,
                trailing_stop=(
                    str(strategy_view.trailing_stop)
                    if strategy_view.trailing_stop is not None
                    else None
                ),
                relation=strategy_view.relation,
            )
            known_ids = {
                row["client_order_id"] for row in self.store.orders(self.config.account_id, 10_000)
            }
            unknown = [row for row in open_orders if row.get("clientOrderId") not in known_ids]
            self._set_gate("UNKNOWN_OPEN_ORDERS", bool(unknown))
            managed = self.store.metadata("managed_position") == "true"
            if self.position_quantity != 0 and not managed:
                if self.config.adopt_existing_position:
                    self.store.set_metadata("managed_position", "true", now_ms)
                    self._event("WARN", "POSITION_ADOPTED", "Existing Futures position adopted")
                else:
                    self._block("UNMANAGED_EXISTING_POSITION")
            else:
                self._unblock("UNMANAGED_EXISTING_POSITION")
            for pending in self.store.pending_orders(self.config.account_id):
                await self._reconcile_order(pending, now_ms)
            if (
                self.last_trade_sync_at_ms is None
                or now_ms - self.last_trade_sync_at_ms >= self.config.trade_sync_seconds * 1000
            ):
                await self._sync_account_history(now_ms)
            self.last_reconciled_at_ms = now_ms
            self.reconciliation_ok = (
                self.signed_account_verified and self.api_reading_enabled and not unknown
            )
            self._unblock("RECONCILIATION_FAILED")
            self._unblock("RATE_LIMIT_BACKOFF")
            self._refresh_status()
            self._active_intent = self.store.active_execution_intent(self.config.account_id)
            self._resolve_active_intent_against_position(now_ms)
            intent_tick = self._intent_tick(now_ms)
            if (
                self._active_intent is not None
                and intent_tick is not None
                and not self.store.pending_orders(self.config.account_id)
                and self.order_submission_ready
            ):
                await self._attempt_active_intent(intent_tick)

    async def _sync_account_history(self, now_ms: int) -> None:
        trades = await self.client.user_trades(self.instrument.symbol)
        income = await self.client.income_history(self.instrument.symbol)
        transfers = await self.client.transfer_history()
        trades_by_order: dict[int, list[dict[str, Any]]] = {}
        for trade in trades:
            trades_by_order.setdefault(int(trade["orderId"]), []).append(trade)
        for order_id, order_trades in trades_by_order.items():
            first_trade = min(order_trades, key=lambda item: int(item["time"]))
            side = str(first_trade["side"])
            position_side = str(first_trade.get("positionSide", "BOTH"))
            is_close = (position_side == "LONG" and side == "SELL") or (
                position_side == "SHORT" and side == "BUY"
            )
            existing = self.store.order_by_exchange_id(self.config.account_id, order_id)
            client_order_id = (
                str(existing["client_order_id"]) if existing else f"binance-futures-sync-{order_id}"
            )
            executed_quantity = sum(
                (Decimal(str(trade["qty"])) for trade in order_trades), Decimal("0")
            )
            cumulative_quote = sum(
                (Decimal(str(trade["quoteQty"])) for trade in order_trades), Decimal("0")
            )
            if existing is None:
                self.store.create_order(
                    client_order_id=client_order_id,
                    account_id=self.config.account_id,
                    symbol=self.instrument.symbol,
                    side=side,
                    position_side=position_side,
                    reduce_only=is_close,
                    reason="binance_futures_readonly_sync",
                    signal_price=str(first_trade["price"]),
                    signal_at_ms=int(first_trade["time"]),
                    requested_quantity=str(executed_quantity),
                    requested_quote_quantity=None,
                )
            self.store.update_order(
                client_order_id,
                status="FILLED",
                updated_at_ms=now_ms,
                submitted_at_ms=int(first_trade["time"]),
                payload={
                    "orderId": order_id,
                    "executedQty": str(executed_quantity),
                    "cummulativeQuoteQty": str(cumulative_quote),
                    "positionSide": position_side,
                    "source": "binance_futures_user_trades",
                },
            )
            self.store.merge_synced_order_duplicates(
                self.config.account_id, order_id, client_order_id
            )
            for trade in order_trades:
                self.store.upsert_fill(
                    account_id=self.config.account_id,
                    symbol=self.instrument.symbol,
                    side=side,
                    client_order_id=client_order_id,
                    payload=trade,
                )
        for row in income:
            self.store.upsert_income(
                account_id=self.config.account_id,
                symbol=self.instrument.symbol,
                payload=row,
            )
        for row in transfers:
            amount = Decimal(str(row.get("income", "0")))
            if amount == 0 or str(row.get("asset", "")) != self.instrument.currency:
                continue
            transaction_id = int(row["tranId"])
            self.store.record_cash_flow(
                flow_id=f"binance-futures-transfer-{transaction_id}",
                account_id=self.config.account_id,
                timestamp_ms=int(row["time"]),
                amount_quote=str(amount),
                flow_type="DEPOSIT" if amount > 0 else "WITHDRAWAL",
                reason="binance_futures_transfer",
                source="binance_usdm_income",
                created_at_ms=now_ms,
            )
        self.last_trade_sync_at_ms = now_ms

    async def process_tick(self, tick: Tick) -> None:
        async with self._lock:
            self.last_tick = tick
            pending_orders = bool(self.store.pending_orders(self.config.account_id))
            self._active_intent = self.store.active_execution_intent(self.config.account_id)
            self._resolve_active_intent_against_position(tick.timestamp_ms)
            has_intent = self._active_intent is not None
            execution_ready = self.order_submission_ready
            capture_intent = self._intent_capture_enabled and not pending_orders and not has_intent
            previous_cross_at_ms = self.strategy.last_cross_at_ms
            signal = self.strategy.on_tick(
                tick,
                has_position=self.position_quantity != 0,
                has_pending_order=pending_orders or has_intent,
                allow_short=self.config.allow_short,
                is_short=self.position_quantity < 0,
                emit_signals=capture_intent,
                lock_on_signal=False,
            )
            profit_signal = self._profit_protection_signal(
                tick,
                has_pending_order=pending_orders or has_intent,
                emit_signals=capture_intent,
            )
            if signal is None:
                signal = self._continuation_reentry_signal(
                    tick,
                    has_pending_order=pending_orders or has_intent,
                    emit_signals=capture_intent,
                )
            if signal is None:
                signal = profit_signal
            if signal is not None:
                self._create_execution_intent(signal, tick)
            self._expire_or_cancel_entry_intent(tick)
            self.store.save_strategy_state(
                self.config.account_id, self._runtime_state(), tick.timestamp_ms
            )
            if (
                not capture_intent
                and self.strategy.last_cross_at_ms is not None
                and self.strategy.last_cross_at_ms != previous_cross_at_ms
            ):
                view = self.strategy.view()
                self._event(
                    "INFO",
                    "SHADOW_CROSS",
                    "Live Futures ATR crossing observed while execution is disabled",
                    timestamp_ms=self.strategy.last_cross_at_ms,
                    details={
                        "direction": self.strategy.last_cross,
                        "result": self.strategy.last_cross_result,
                        "reason": self.strategy.last_cross_reason,
                        "price": str(tick.price),
                        "atr": str(view.atr) if view.atr is not None else None,
                        "trailing_stop": (
                            str(view.trailing_stop) if view.trailing_stop is not None else None
                        ),
                    },
                )
            if self._active_intent is not None and not pending_orders and execution_ready:
                await self._attempt_active_intent(tick)

    def _profit_protection_signal(
        self,
        tick: Tick,
        *,
        has_pending_order: bool,
        emit_signals: bool,
    ) -> StrategySignal | None:
        return atr_profit_protection_signal(
            self.profit_protection,
            self.strategy,
            tick,
            position_quantity=self.position_quantity,
            entry_price=self.entry_price,
            has_pending_order=has_pending_order,
            emit_signals=emit_signals,
        )

    def _continuation_reentry_signal(
        self,
        tick: Tick,
        *,
        has_pending_order: bool,
        emit_signals: bool,
    ) -> StrategySignal | None:
        eligible_bar = self.continuation_eligible_bar_ms
        if (
            self.config.continuation_reentry_atr <= 0
            or self.position_quantity != 0
            or self.continuation_direction is None
            or self.continuation_anchor is None
            or eligible_bar is None
        ):
            return None
        bar_start = tick.timestamp_ms // self.strategy.bar_ms * self.strategy.bar_ms
        if bar_start > eligible_bar:
            self._clear_continuation_state()
            return None
        return self.strategy.continuation_reentry_signal(
            tick,
            direction=self.continuation_direction,
            exit_anchor=self.continuation_anchor,
            eligible_bar_ms=eligible_bar,
            threshold_atr=Decimal(str(self.config.continuation_reentry_atr)),
            has_pending_order=has_pending_order,
            emit_signals=emit_signals,
            lock_on_signal=False,
        )

    @property
    def _intent_capture_enabled(self) -> bool:
        return (
            self.config.enabled
            and self.config.allow_order_submission
            and self.activation_confirmed
            and self.client.has_credentials
            and self.test_order_passed
            and not self.persisted_paused
        )

    def _create_execution_intent(self, signal: StrategySignal, tick: Tick) -> None:
        if self._active_intent is not None:
            return
        signal_at_ms = signal.signal_at_ms or tick.timestamp_ms
        intent_id = _execution_intent_id(self.config.account_id, self.config.strategy_name, signal)
        expires_at_ms = (
            None if signal.reduce_only else signal.bar_start_ms + self.strategy.bar_ms - 1
        )
        created = self.store.create_execution_intent(
            intent_id=intent_id,
            account_id=self.config.account_id,
            symbol=self.instrument.symbol,
            side=signal.side.value,
            reduce_only=signal.reduce_only,
            reason=signal.reason,
            strategy_name=self.config.strategy_name,
            signal_price=str(signal.signal_price),
            trailing_stop=str(signal.trailing_stop),
            atr=str(signal.atr),
            bar_start_ms=signal.bar_start_ms,
            signal_at_ms=signal_at_ms,
            expires_at_ms=expires_at_ms,
            created_at_ms=tick.timestamp_ms,
        )
        intent = self.store.execution_intent(intent_id)
        self._active_intent = (
            intent
            if intent is not None
            and intent["status"] in {"PENDING", "VALIDATING", "SUBMITTING", "ACCEPTED"}
            else None
        )
        if created:
            self._event(
                "WARN" if not self.order_submission_ready else "INFO",
                "EXECUTION_INTENT_CREATED",
                "Strategy signal persisted before external execution",
                timestamp_ms=tick.timestamp_ms,
                details={
                    "intent_id": intent_id,
                    "side": signal.side.value,
                    "reduce_only": signal.reduce_only,
                    "reason": signal.reason,
                    "expires_at_ms": expires_at_ms,
                    "execution_ready": self.order_submission_ready,
                },
            )

    def _intent_signal(self, intent: dict[str, Any]) -> StrategySignal:
        return StrategySignal(
            side=Side(str(intent["side"])),
            reason=str(intent["reason"]),
            signal_price=Decimal(str(intent["signal_price"])),
            trailing_stop=Decimal(str(intent["trailing_stop"])),
            atr=Decimal(str(intent["atr"])),
            bar_start_ms=int(intent["bar_start_ms"]),
            tick_id=f"intent:{intent['intent_id']}",
            signal_at_ms=int(intent["signal_at_ms"]),
            reduce_only=bool(intent["reduce_only"]),
        )

    def _intent_tick(self, timestamp_ms: int) -> Tick | None:
        if self.last_tick is not None:
            return self.last_tick
        if self._active_intent is None or not bool(self._active_intent["reduce_only"]):
            return None
        price = self.mark_price
        if price <= 0:
            price = Decimal(str(self._active_intent["signal_price"]))
        return Tick(
            event_id=f"intent-recovery:{self._active_intent['intent_id']}",
            timestamp_ms=timestamp_ms,
            price=price,
            quantity=Decimal("0"),
            source="live_execution_intent_recovery",
        )

    def _resolve_active_intent_against_position(self, timestamp_ms: int) -> None:
        intent = self._active_intent
        if intent is None:
            return
        reduce_only = bool(intent["reduce_only"])
        if reduce_only and self.position_quantity == 0:
            self._finish_active_intent("COMPLETED", "POSITION_ALREADY_FLAT", timestamp_ms)
            return
        if not reduce_only and self.position_quantity != 0:
            self._finish_active_intent("CANCELED", "POSITION_CHANGED", timestamp_ms)
            return
        if reduce_only:
            side = str(intent["side"])
            wrong_side = (self.position_quantity > 0 and side != "SELL") or (
                self.position_quantity < 0 and side != "BUY"
            )
            if wrong_side:
                self._finish_active_intent("CANCELED", "POSITION_DIRECTION_CHANGED", timestamp_ms)

    def _expire_or_cancel_entry_intent(self, tick: Tick) -> None:
        intent = self._active_intent
        if intent is None or bool(intent["reduce_only"]):
            return
        expires_at_ms = intent.get("expires_at_ms")
        if expires_at_ms is not None and tick.timestamp_ms > int(expires_at_ms):
            self._finish_active_intent("EXPIRED", "SIGNAL_BAR_EXPIRED", tick.timestamp_ms)
            return
        view = self.strategy.view()
        if view.trailing_stop is None:
            return
        if str(intent["side"]) == "BUY" and tick.price <= view.trailing_stop:
            self._finish_active_intent("CANCELED", "ATR_CROSS_INVALIDATED", tick.timestamp_ms)
        elif str(intent["side"]) == "SELL" and tick.price >= view.trailing_stop:
            self._finish_active_intent("CANCELED", "ATR_CROSS_INVALIDATED", tick.timestamp_ms)
        elif not view.trend_filter_passed:
            self._finish_active_intent("CANCELED", "TREND_FILTER_INVALIDATED", tick.timestamp_ms)

    def _finish_active_intent(self, status: str, reason: str, timestamp_ms: int) -> None:
        intent = self._active_intent
        if intent is None:
            return
        self.store.update_execution_intent(
            str(intent["intent_id"]),
            status=status,
            updated_at_ms=timestamp_ms,
            attempts=int(intent["attempts"]),
            last_error=reason,
        )
        self._event(
            "INFO" if status == "COMPLETED" else "WARN",
            f"EXECUTION_INTENT_{status}",
            f"Execution intent {status.lower()}: {reason}",
            timestamp_ms=timestamp_ms,
            details={"intent_id": intent["intent_id"], "reason": reason},
        )
        self._active_intent = None

    def _cancel_active_intent(self, reason: str, timestamp_ms: int) -> None:
        self._finish_active_intent("CANCELED", reason, timestamp_ms)

    async def _attempt_active_intent(self, tick: Tick) -> None:
        intent = self._active_intent
        if intent is None or intent["status"] not in {"PENDING", "VALIDATING"}:
            return
        now_ms = _now_ms()
        if (
            int(intent["attempts"]) > 0
            and now_ms - int(intent["updated_at_ms"]) < self.config.reconcile_seconds * 1000
        ):
            return
        self._expire_or_cancel_entry_intent(tick)
        intent = self._active_intent
        if intent is None:
            return
        attempt = int(intent["attempts"]) + 1
        intent_id = str(intent["intent_id"])
        self.store.update_execution_intent(
            intent_id,
            status="VALIDATING",
            updated_at_ms=now_ms,
            attempts=attempt,
            last_error=None,
        )
        self._active_intent = self.store.execution_intent(intent_id)
        result, reason = await self._submit_signal(
            self._intent_signal(intent),
            tick,
            execution_intent_id=intent_id,
            attempt=attempt,
        )
        if result == "RETRY":
            self.store.update_execution_intent(
                intent_id,
                status="PENDING",
                updated_at_ms=_now_ms(),
                attempts=attempt,
                last_error=reason,
            )
        elif result == "CANCELED":
            self.store.update_execution_intent(
                intent_id,
                status="CANCELED",
                updated_at_ms=_now_ms(),
                attempts=attempt,
                last_error=reason,
            )
        self._active_intent = self.store.active_execution_intent(self.config.account_id)

    def _runtime_state(self) -> dict[str, Any]:
        state = self.strategy.runtime_state()
        if self.profit_protection is not None:
            state["profit_protection"] = self.profit_protection.runtime_state()
        state["continuation_reentry"] = {
            "threshold_atr": str(self.config.continuation_reentry_atr),
            "direction": self.continuation_direction,
            "anchor": (
                str(self.continuation_anchor) if self.continuation_anchor is not None else None
            ),
            "eligible_bar_ms": self.continuation_eligible_bar_ms,
        }
        return state

    def _restore_continuation_state(self, saved_state: dict[str, Any] | None) -> None:
        self._clear_continuation_state()
        if self.config.continuation_reentry_atr <= 0 or not saved_state:
            return
        state = saved_state.get("continuation_reentry")
        if not isinstance(state, dict):
            return
        if Decimal(str(state.get("threshold_atr", "-1"))) != Decimal(
            str(self.config.continuation_reentry_atr)
        ):
            return
        direction = state.get("direction")
        anchor = state.get("anchor")
        eligible_bar_ms = state.get("eligible_bar_ms")
        if direction not in {"LONG", "SHORT"} or anchor is None or eligible_bar_ms is None:
            return
        self.continuation_direction = str(direction)
        self.continuation_anchor = Decimal(str(anchor))
        self.continuation_eligible_bar_ms = int(eligible_bar_ms)

    def _clear_continuation_state(self) -> None:
        self.continuation_direction = None
        self.continuation_anchor = None
        self.continuation_eligible_bar_ms = None

    def continuation_reentry_view(self) -> dict[str, Any]:
        return {
            "continuation_reentry_atr": self.config.continuation_reentry_atr,
            "continuation_reentry_direction": self.continuation_direction,
            "continuation_reentry_anchor": (
                str(self.continuation_anchor) if self.continuation_anchor is not None else None
            ),
            "continuation_reentry_eligible_bar_ms": self.continuation_eligible_bar_ms,
        }

    def profit_protection_view(self) -> dict[str, Any]:
        protection = self.profit_protection
        return {
            "profit_protection_active": bool(protection and protection.active),
            "profit_stop": str(protection.stop) if protection and protection.stop else None,
            "profit_favorable_extreme": (
                str(protection.favorable_extreme)
                if protection and protection.favorable_extreme is not None
                else None
            ),
        }

    async def _submit_signal(
        self,
        signal: StrategySignal,
        tick: Tick,
        *,
        execution_intent_id: str | None = None,
        attempt: int = 0,
    ) -> tuple[str, str | None]:
        if not self.order_submission_ready or self.rules is None:
            return "RETRY", "EXECUTION_GATE_BLOCKED"
        try:
            rejection = await self._risk_rejection(signal, tick)
        except BinanceFuturesRateLimitError as exc:
            self._mark_reconciliation_failed("RATE_LIMIT_BACKOFF")
            return "RETRY", f"RATE_LIMIT_BACKOFF:{math.ceil(exc.retry_after_seconds)}"
        except Exception as exc:
            self._event(
                "ERROR",
                "ORDER_VALIDATION_FAILED",
                f"{type(exc).__name__}: {exc}",
                details={"intent_id": execution_intent_id},
            )
            return "RETRY", f"ORDER_VALIDATION_FAILED:{type(exc).__name__}"
        if rejection:
            self._event("WARN", "ORDER_RISK_BLOCKED", rejection)
            return (
                ("RETRY", rejection) if rejection == "SLIPPAGE_LIMIT" else ("CANCELED", rejection)
            )
        if signal.reduce_only:
            if self.position_quantity > 0:
                side, position_side = "SELL", "LONG"
            elif self.position_quantity < 0:
                side, position_side = "BUY", "SHORT"
            else:
                return "CANCELED", "POSITION_ALREADY_FLAT"
            quantity = self.rules.floor_quantity(abs(self.position_quantity))
        else:
            side = signal.side.value
            position_side = "LONG" if signal.side is Side.BUY else "SHORT"
            target_notional = (
                self.margin_balance
                * Decimal(str(self.config.position_fraction))
                * Decimal(self.config.leverage)
            )
            if self.config.max_order_notional > 0:
                target_notional = min(
                    target_notional,
                    Decimal(str(self.config.max_order_notional)),
                )
            quantity = self.rules.floor_quantity(target_notional / tick.price)
        if quantity <= 0 or (
            not signal.reduce_only
            and (
                quantity < self.rules.minimum_quantity
                or quantity * tick.price < self.rules.minimum_notional
            )
        ):
            self._event("WARN", "ORDER_RISK_BLOCKED", "ORDER_BELOW_MINIMUM")
            return "CANCELED", "ORDER_BELOW_MINIMUM"
        client_order_id = _client_order_id(
            self.config.account_id,
            signal,
            position_side,
            execution_intent_id=execution_intent_id,
            attempt=attempt,
        )
        created = self.store.create_order(
            client_order_id=client_order_id,
            account_id=self.config.account_id,
            symbol=self.instrument.symbol,
            side=side,
            position_side=position_side,
            reduce_only=signal.reduce_only,
            reason=signal.reason,
            signal_price=str(signal.signal_price),
            signal_at_ms=signal.signal_at_ms or tick.timestamp_ms,
            requested_quantity=str(quantity),
            requested_quote_quantity=None,
            execution_intent_id=execution_intent_id,
        )
        if not created:
            existing = self.store.order(client_order_id)
            if existing and existing["status"] in {
                "CREATED",
                "SUBMITTING",
                "NEW",
                "PARTIALLY_FILLED",
                "FILLED",
            }:
                return "SUBMITTED", None
            return "RETRY", "DUPLICATE_TERMINAL_ATTEMPT"
        now_ms = _now_ms()
        self.store.update_order(
            client_order_id,
            status="SUBMITTING",
            updated_at_ms=now_ms,
            submitted_at_ms=now_ms,
        )
        if execution_intent_id is not None:
            self.store.update_execution_intent(
                execution_intent_id,
                status="SUBMITTING",
                updated_at_ms=now_ms,
                attempts=attempt,
                last_error=None,
                client_order_id=client_order_id,
            )
        self.strategy.on_submit(signal)
        self.store.save_strategy_state(
            self.config.account_id,
            self._runtime_state(),
            now_ms,
        )
        try:
            payload = await self.client.market_order(
                symbol=self.instrument.symbol,
                side=side,
                position_side=position_side,
                quantity=quantity,
                client_order_id=client_order_id,
            )
        except BinanceFuturesRateLimitError as exc:
            self.store.update_order(
                client_order_id,
                status="REJECTED",
                updated_at_ms=_now_ms(),
                payload={"code": exc.code, "msg": exc.message},
            )
            self._mark_reconciliation_failed("RATE_LIMIT_BACKOFF")
            self._event(
                "ERROR",
                "ORDER_RATE_LIMITED",
                "Binance rejected order submission under an active rate limit",
                details={
                    "intent_id": execution_intent_id,
                    "retry_after_seconds": math.ceil(exc.retry_after_seconds),
                },
            )
            return "RETRY", f"RATE_LIMIT_BACKOFF:{math.ceil(exc.retry_after_seconds)}"
        except BinanceFuturesAPIError as exc:
            if exc.code in AMBIGUOUS_BINANCE_CODES or exc.status_code >= 500:
                self._event("ERROR", "ORDER_RESULT_UNKNOWN", "Order result requires reconciliation")
                return "SUBMITTED", "ORDER_RESULT_UNKNOWN"
            self.store.update_order(
                client_order_id,
                status="REJECTED",
                updated_at_ms=_now_ms(),
                payload={"code": exc.code, "msg": exc.message},
            )
            self.strategy.on_fill(tick.timestamp_ms, filled=False)
            self._event("ERROR", "ORDER_REJECTED", exc.message)
            return (
                ("RETRY", f"ORDER_REJECTED:{exc.code}")
                if signal.reduce_only
                else ("CANCELED", f"ORDER_REJECTED:{exc.code}")
            )
        except Exception as exc:
            # A timeout or disconnect after POST has an unknown execution result. The
            # deterministic client ID must be reconciled; never submit a second order here.
            self._event(
                "ERROR",
                "ORDER_RESULT_UNKNOWN",
                f"{type(exc).__name__}: order result requires reconciliation",
                details={"intent_id": execution_intent_id},
            )
            return "SUBMITTED", "ORDER_RESULT_UNKNOWN"
        status = str(payload["status"])
        self.store.update_order(
            client_order_id, status=status, updated_at_ms=_now_ms(), payload=payload
        )
        if execution_intent_id is not None and status not in TERMINAL_ORDER_STATES:
            self.store.update_execution_intent(
                execution_intent_id,
                status="ACCEPTED",
                updated_at_ms=_now_ms(),
                attempts=attempt,
                last_error=None,
                client_order_id=client_order_id,
            )
        try:
            await self._ingest_order_trades(client_order_id, int(payload["orderId"]))
        except Exception as exc:
            self._event(
                "ERROR",
                "ORDER_POST_ACCEPT_SYNC_FAILED",
                f"{type(exc).__name__}: accepted order will be reconciled",
                details={"client_order_id": client_order_id},
            )
        if status in TERMINAL_ORDER_STATES:
            if status == "FILLED":
                if signal.reduce_only:
                    self.position_quantity = Decimal("0")
                    self.entry_price = Decimal("0")
                    self.unrealized_pnl = Decimal("0")
                else:
                    self.position_quantity = quantity if side == "BUY" else -quantity
            self._apply_terminal_strategy_result(client_order_id, _now_ms())
        self._event("INFO", "ORDER_ACCEPTED", f"Binance accepted {side} {position_side}")
        return "SUBMITTED", None

    async def _risk_rejection(self, signal: StrategySignal, tick: Tick) -> str | None:
        if self.persisted_paused:
            return "LIVE_TRADING_PAUSED"
        # A risk-reducing exit must not depend on a second public book request or
        # entry-only slippage/daily caps. Use the freshly reconciled full position.
        if signal.reduce_only:
            return None
        if not signal.reduce_only and signal.side is Side.SELL and not self.config.allow_short:
            return "SHORT_ENTRY_DISABLED"
        if not signal.reduce_only:
            day_start_ms = tick.timestamp_ms // 86_400_000 * 86_400_000
            if (
                self.config.max_orders_per_day > 0
                and self.store.order_count_since(self.config.account_id, day_start_ms)
                >= self.config.max_orders_per_day
            ):
                return "DAILY_ORDER_LIMIT"
            initial = self.store.day_start_futures_equity(self.config.account_id, day_start_ms)
            latest = self.store.latest_futures_snapshot(self.config.account_id)
            if self.config.max_daily_loss > 0 and initial and latest:
                loss = Decimal(initial) - Decimal(latest["margin_balance"])
                if loss >= Decimal(str(self.config.max_daily_loss)):
                    return "DAILY_LOSS_ENTRY_LIMIT"
        book = await self.client.book_ticker(self.instrument.symbol)
        execution_price = Decimal(
            str(book["askPrice"] if signal.side is Side.BUY else book["bidPrice"])
        )
        slippage_bps = abs(execution_price - tick.price) / tick.price * Decimal("10000")
        if not signal.reduce_only and slippage_bps > Decimal(str(self.config.max_slippage_bps)):
            return "SLIPPAGE_LIMIT"
        return None

    async def _reconcile_order(self, order: dict[str, Any], now_ms: int) -> None:
        client_order_id = str(order["client_order_id"])
        try:
            payload = await self.client.query_order(
                self.instrument.symbol, client_order_id=client_order_id
            )
        except BinanceFuturesAPIError as exc:
            if exc.code == -2013 and order["status"] in {"CREATED", "SUBMITTING"}:
                submitted_at_ms = order.get("submitted_at_ms") or order["signal_at_ms"]
                if now_ms - int(submitted_at_ms) < self.config.order_timeout_seconds * 1000:
                    return
                self.store.update_order(
                    client_order_id,
                    status="REJECTED",
                    updated_at_ms=now_ms,
                    payload={"code": exc.code, "msg": exc.message},
                )
                self._apply_terminal_strategy_result(client_order_id, now_ms)
                return
            raise
        status = str(payload["status"])
        submitted_at_ms = order.get("submitted_at_ms") or order["signal_at_ms"]
        if (
            status in {"NEW", "PARTIALLY_FILLED"}
            and now_ms - int(submitted_at_ms) >= self.config.order_timeout_seconds * 1000
        ):
            try:
                payload = await self.client.cancel_order(self.instrument.symbol, client_order_id)
                status = str(payload["status"])
            except BinanceFuturesAPIError as exc:
                if exc.code not in {-2011, -2013}:
                    raise
        self.store.update_order(
            client_order_id, status=status, updated_at_ms=now_ms, payload=payload
        )
        order_id = payload.get("orderId") or order.get("exchange_order_id")
        if order_id is not None:
            await self._ingest_order_trades(client_order_id, int(order_id))
        if status in TERMINAL_ORDER_STATES:
            self._apply_terminal_strategy_result(client_order_id, now_ms)

    async def _ingest_order_trades(self, client_order_id: str, order_id: int) -> None:
        order = self.store.order(client_order_id)
        if order is None:
            return
        for trade in await self.client.user_trades(self.instrument.symbol, order_id=order_id):
            self.store.upsert_fill(
                account_id=self.config.account_id,
                symbol=self.instrument.symbol,
                side=str(trade["side"]),
                client_order_id=client_order_id,
                payload=trade,
            )

    def _apply_terminal_strategy_result(self, client_order_id: str, timestamp_ms: int) -> None:
        marker = f"strategy_fill_applied:{client_order_id}"
        if self.store.metadata(marker) is not None:
            return
        order = self.store.order(client_order_id)
        if order is None:
            return
        filled = Decimal(str(order["executed_quantity"])) > 0
        fills = self.store.fills_for_order(self.config.account_id, client_order_id)
        fill_timestamp_ms = (
            max(int(fill["timestamp_ms"]) for fill in fills) if fills else timestamp_ms
        )
        self.strategy.on_fill(fill_timestamp_ms, filled=filled)
        self.store.set_metadata(marker, str(order["status"]), timestamp_ms)
        intent_id = order.get("execution_intent_id")
        if intent_id:
            if order["status"] == "FILLED" or (filled and not order["reduce_only"]):
                intent_status = "COMPLETED"
                intent_error = None
            else:
                intent_status = "PENDING"
                intent_error = f"ORDER_{order['status']}"
            intent = self.store.execution_intent(str(intent_id))
            self.store.update_execution_intent(
                str(intent_id),
                status=intent_status,
                updated_at_ms=timestamp_ms,
                attempts=int(intent["attempts"]) if intent else None,
                last_error=intent_error,
                client_order_id=client_order_id,
            )
            self._active_intent = self.store.active_execution_intent(self.config.account_id)
        if filled:
            managed = not order["reduce_only"] or (
                order["status"] != "FILLED" and self.position_quantity != 0
            )
            self.store.set_metadata(
                "managed_position", "true" if managed else "false", timestamp_ms
            )
            if order["reduce_only"]:
                if (
                    self.config.continuation_reentry_atr > 0
                    and order["status"] == "FILLED"
                    and order["reason"] != "operator_manual_flatten"
                    and fills
                ):
                    total_quantity = sum(
                        (Decimal(str(fill["quantity"])) for fill in fills), Decimal("0")
                    )
                    total_quote = sum(
                        (Decimal(str(fill["quote_quantity"])) for fill in fills),
                        Decimal("0"),
                    )
                    if total_quantity > 0:
                        self.continuation_direction = str(order["position_side"])
                        self.continuation_anchor = total_quote / total_quantity
                        fill_bar_start = (
                            fill_timestamp_ms // self.strategy.bar_ms * self.strategy.bar_ms
                        )
                        self.continuation_eligible_bar_ms = fill_bar_start + self.strategy.bar_ms
                        self._event(
                            "INFO",
                            "CONTINUATION_REENTRY_ARMED",
                            "Next-bar continuation re-entry opportunity armed",
                            timestamp_ms=fill_timestamp_ms,
                            details=self.continuation_reentry_view(),
                        )
                else:
                    self._clear_continuation_state()
            else:
                self._clear_continuation_state()
            self.store.save_strategy_state(
                self.config.account_id, self._runtime_state(), timestamp_ms
            )

    def _client_rate_limit_cooldown(self) -> float:
        return float(getattr(self.client, "rate_limit_cooldown_seconds", 0.0))

    def _rate_limit_view(self) -> dict[str, Any]:
        status = getattr(self.client, "rate_limit_status", None)
        if callable(status):
            return status()
        return {"cooldown_seconds": 0.0, "used_weight": {}}

    def _mark_reconciliation_failed(self, reason: str) -> None:
        self.reconciliation_ok = False
        self._block("RECONCILIATION_FAILED")
        if reason == "RATE_LIMIT_BACKOFF":
            self._block("RATE_LIMIT_BACKOFF")
        self._refresh_status()

    def readiness(self) -> dict[str, Any]:
        return {
            "account_id": self.config.account_id,
            "symbol": self.instrument.symbol,
            "display_symbol": "SOXL/USDT PERP LIVE",
            "product": "Binance USD-M Futures",
            "enabled": self.config.enabled,
            "status": self.status,
            "status_message": self.status_message,
            "public_capability": self.public_capability,
            "credentials_present": self.client.has_credentials,
            "credential_file_secure": self.credential_error is None,
            "signed_account_verified": self.signed_account_verified,
            "api_reading_enabled": self.api_reading_enabled,
            "trading_permitted": self.futures_trading_permitted,
            "spot_trading_permitted": False,
            "withdrawals_enabled": self.withdrawals_enabled,
            "ip_restricted": self.ip_restricted,
            "allow_order_submission": self.config.allow_order_submission,
            "activation_confirmed": self.activation_confirmed,
            "test_order_passed": self.test_order_passed,
            "order_submission_ready": self.order_submission_ready,
            "strategy_resume_ready": self.strategy_resume_ready,
            "persisted_paused": self.persisted_paused,
            "reconciliation_ok": self.reconciliation_ok,
            "last_reconciled_at_ms": self.last_reconciled_at_ms,
            "last_account_gate_sync_at_ms": self.last_account_gate_sync_at_ms,
            "last_trade_sync_at_ms": self.last_trade_sync_at_ms,
            "rate_limit": self._rate_limit_view(),
            "execution_intent": self._active_intent,
            "synced_trade_count": self.store.fill_count(self.config.account_id),
            "block_reasons": sorted(self.block_reasons),
            "current_leverage": self.current_leverage,
            "target_leverage": self.config.leverage,
            "current_margin_mode": self.current_margin_mode,
            "target_margin_mode": self.config.margin_mode,
            "current_position_mode": self.current_position_mode,
            "multi_assets_enabled": self.multi_assets_enabled,
            "strategy_name": self.config.strategy_name,
            "allow_short": self.config.allow_short,
            "strategy": {
                **_json_decimals(asdict(self.strategy.view())),
                **self.continuation_reentry_view(),
            },
            "database": str(self.config.database_path),
            "risk_limits": {
                "position_fraction": self.config.position_fraction,
                "max_order_notional": self.config.max_order_notional,
                "quote_reserve": 0,
                "max_slippage_bps": self.config.max_slippage_bps,
                "max_daily_loss": self.config.max_daily_loss,
                "max_orders_per_day": self.config.max_orders_per_day,
            },
        }

    def _refresh_status(self) -> None:
        if self.order_submission_ready:
            self.status = "ARMED"
            self.status_message = "Live Futures execution is armed"
        elif self.config.enabled and self.signed_account_verified and self.persisted_paused:
            self.status = "OBSERVE_ONLY"
            self.status_message = "Live Futures strategy stopped by operator"
        elif self.config.enabled and self.signed_account_verified:
            self.status = "OBSERVE_ONLY"
            self.status_message = "Signed Futures reconciliation active; orders remain closed"
        elif self.config.enabled:
            self.status = "BLOCKED"
            self.status_message = "Live Futures execution is blocked"

    def _set_gate(self, reason: str, blocked: bool) -> None:
        if blocked:
            self._block(reason)
        else:
            self._unblock(reason)

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
        *,
        timestamp_ms: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.store.add_event(
            self.config.account_id,
            timestamp_ms if timestamp_ms is not None else _now_ms(),
            level,
            code,
            message,
            details,
        )


def _execution_intent_id(
    account_id: str,
    strategy_name: str,
    signal: StrategySignal,
) -> str:
    raw = (
        f"{account_id}:{strategy_name}:{signal.side.value}:{int(signal.reduce_only)}:"
        f"{signal.reason}:{signal.bar_start_ms}"
    )
    return f"intent-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def _client_order_id(
    account_id: str,
    signal: StrategySignal,
    position_side: str,
    *,
    execution_intent_id: str | None = None,
    attempt: int = 0,
) -> str:
    raw = (
        f"{account_id}:{signal.side.value}:{position_side}:"
        f"{execution_intent_id or signal.tick_id}:{signal.bar_start_ms}:{attempt}"
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()[:18]
    return f"mmt-{signal.side.value.lower()}-{digest}"[:36]


def _manual_close_client_order_id(position_side: str, timestamp_ms: int, index: int) -> str:
    side = position_side[:1].lower() or "x"
    return f"mmt-close-{side}-{timestamp_ms}-{index}"


def _instrument(settings: Settings, instrument_id: str) -> InstrumentSettings:
    return next(item for item in settings.instruments if item.id == instrument_id)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_decimals(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_decimals(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_decimals(item) for item in value]
    return value
