"""Real-time orchestration for feeds, strategy state, and paper execution."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from mastermind_tick.config import InstrumentSettings, Settings
from mastermind_tick.feeds import MarketFeed, build_feed
from mastermind_tick.models import Bar, Tick
from mastermind_tick.store import PaperStore
from mastermind_tick.strategy import ATRTickStrategy, StrategyView


@dataclass
class InstrumentRuntime:
    instrument: InstrumentSettings
    feed: MarketFeed
    strategy: ATRTickStrategy
    status: str = "STARTING"
    status_message: str = "Loading warm-up history"
    last_tick: Tick | None = None
    last_snapshot_ms: int = 0
    reconnects: int = 0
    funding_cursor_ms: int = 0
    last_funding_poll_ms: int = 0
    last_official_bar_start_ms: int | None = None
    last_kline_verified_at_ms: int | None = None
    kline_validation: str = "PENDING"
    kline_mismatches: int = 0
    task: asyncio.Task | None = field(default=None, repr=False)
    kline_task: asyncio.Task | None = field(default=None, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class PaperEngine:
    def __init__(self, settings: Settings, store: PaperStore):
        self.settings = settings
        self.store = store
        self.runtimes: dict[str, InstrumentRuntime] = {}
        self.trading_enabled = True
        self.started_at_ms: int | None = None
        self._stopping = False

    async def start(self) -> None:
        self.started_at_ms = _now_ms()
        self._stopping = False
        for instrument in self.settings.instruments:
            self.store.ensure_account(instrument, self.settings.initial_cash, self.started_at_ms)
            feed = build_feed(instrument.feed, instrument.symbol)
            strategy = ATRTickStrategy(
                period=self.settings.strategy.atr_period,
                multiplier=self.settings.strategy.atr_multiplier,
                bar_minutes=self.settings.strategy.bar_minutes,
            )
            runtime = InstrumentRuntime(instrument=instrument, feed=feed, strategy=strategy)
            account = self.store.account(instrument.id)
            latest_funding = self.store.latest_funding_time(instrument.id)
            runtime.funding_cursor_ms = max(
                int(account["updated_at_ms"]),
                latest_funding or 0,
            )
            self.runtimes[instrument.id] = runtime
            try:
                history = await feed.history(self.settings.warmup_bars)
                self.store.upsert_history_bars(
                    instrument,
                    self.settings.strategy.bar_minutes,
                    history,
                    feed.kline_source_name,
                )
                strategy.bootstrap(history)
                if history:
                    runtime.last_official_bar_start_ms = history[-1].start_ms
                    runtime.last_kline_verified_at_ms = self.started_at_ms
                    runtime.kline_validation = "REST_VERIFIED"
                strategy.restore_runtime(self.store.strategy_state(instrument.id))
                runtime.status_message = f"Warm-up ready: {len(history)} x 15m bars"
            except Exception as exc:
                runtime.status = "DEGRADED"
                runtime.status_message = f"Warm-up failed: {type(exc).__name__}: {exc}"
                self.store.add_event(
                    instrument.id,
                    _now_ms(),
                    "ERROR",
                    "WARMUP_FAILED",
                    runtime.status_message,
                )
            runtime.task = asyncio.create_task(
                self._run_instrument(runtime), name=f"feed-{instrument.id}"
            )
            runtime.kline_task = asyncio.create_task(
                self._run_klines(runtime), name=f"klines-{instrument.id}"
            )

    async def stop(self) -> None:
        self._stopping = True
        tasks = [
            task
            for runtime in self.runtimes.values()
            for task in (runtime.task, runtime.kline_task)
            if task
        ]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for runtime in self.runtimes.values():
            runtime.status = "STOPPED"
            runtime.status_message = "Service stopped"

    async def pause(self) -> None:
        self.trading_enabled = False
        now_ms = _now_ms()
        for account_id in self.runtimes:
            self.store.cancel_pending(account_id, now_ms)
            self.store.add_event(
                account_id,
                now_ms,
                "WARN",
                "TRADING_PAUSED",
                "Signal execution paused by operator",
            )

    async def resume(self) -> None:
        self.trading_enabled = True
        now_ms = _now_ms()
        for account_id in self.runtimes:
            self.store.add_event(
                account_id,
                now_ms,
                "INFO",
                "TRADING_RESUMED",
                "Signal execution resumed by operator",
            )

    async def _run_instrument(self, runtime: InstrumentRuntime) -> None:
        while not self._stopping:
            runtime.status = "CONNECTING"
            runtime.status_message = f"Connecting to {runtime.feed.source_name}"
            try:
                async for tick in runtime.feed.ticks():
                    runtime.status = "LIVE"
                    runtime.status_message = f"Receiving {tick.source}"
                    await self._process_tick(runtime, tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                runtime.reconnects += 1
                runtime.status = "DEGRADED"
                runtime.status_message = f"{type(exc).__name__}: {exc}"
                self.store.add_event(
                    runtime.instrument.id,
                    _now_ms(),
                    "ERROR",
                    "FEED_DISCONNECTED",
                    runtime.status_message,
                    {"reconnects": runtime.reconnects},
                )
                await asyncio.sleep(min(30, 2 ** min(runtime.reconnects, 4)))

    async def _run_klines(self, runtime: InstrumentRuntime) -> None:
        reconnects = 0
        while not self._stopping:
            try:
                async for bar in runtime.feed.closed_bars():
                    reconnects = 0
                    await self._process_official_close(runtime, bar)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reconnects += 1
                runtime.kline_validation = "STREAM_ERROR"
                self.store.add_event(
                    runtime.instrument.id,
                    _now_ms(),
                    "ERROR",
                    "KLINE_STREAM_DISCONNECTED",
                    f"{type(exc).__name__}: {exc}",
                    {"reconnects": reconnects},
                )
                await asyncio.sleep(min(30, 2 ** min(reconnects, 4)))

    async def _process_tick(self, runtime: InstrumentRuntime, tick: Tick) -> None:
        async with runtime.lock:
            self.store.record_market_tick(
                runtime.instrument,
                self.settings.strategy.bar_minutes,
                tick,
            )
            runtime.last_tick = tick
            funding_applied = await self._apply_due_funding(runtime, tick)
            account_id = runtime.instrument.id
            fill = None
            if self.trading_enabled:
                fill = self.store.fill_pending(
                    account_id,
                    tick,
                    runtime.instrument,
                    self.settings.execution,
                    _position_fraction(runtime.instrument, self.settings),
                )
            account = self.store.account(account_id)
            position_quantity = Decimal(account["quantity"])
            has_position = position_quantity != 0
            is_short = position_quantity < 0
            allow_short = runtime.instrument.paper_model == "futures"
            has_pending = self.store.has_pending_order(account_id)
            signal = runtime.strategy.on_tick(
                tick,
                has_position=has_position,
                has_pending_order=has_pending,
                allow_short=allow_short,
                is_short=is_short,
                emit_signals=self.trading_enabled,
            )
            if signal:
                self.store.submit_order(account_id, signal, tick.timestamp_ms)
            snapshot_due = (
                tick.timestamp_ms - runtime.last_snapshot_ms
                >= self.settings.equity_snapshot_seconds * 1000
            )
            if snapshot_due or fill or signal or funding_applied:
                strategy_view = _strategy_view(asdict(runtime.strategy.view()))
                self.store.snapshot(account_id, tick, strategy_view)
                runtime.last_snapshot_ms = tick.timestamp_ms
            self.store.save_strategy_state(
                account_id, runtime.strategy.runtime_state(), tick.timestamp_ms
            )

    async def _process_official_close(self, runtime: InstrumentRuntime, stream_bar: Bar) -> None:
        async with runtime.lock:
            await self._sync_official_bars_locked(
                runtime,
                stream_bar.start_ms,
                stream_bar.start_ms,
                stream_bar=stream_bar,
            )

    async def _sync_official_bars_locked(
        self,
        runtime: InstrumentRuntime,
        start_ms: int,
        end_ms: int,
        *,
        stream_bar: Bar | None = None,
    ) -> bool:
        account_id = runtime.instrument.id
        try:
            official_bars = await runtime.feed.official_bars(start_ms, end_ms)
        except Exception as exc:
            runtime.kline_validation = "REST_ERROR"
            self.store.add_event(
                account_id,
                _now_ms(),
                "ERROR",
                "KLINE_REST_FAILED",
                f"{type(exc).__name__}: {exc}",
                {"start_ms": start_ms, "end_ms": end_ms},
            )
            return False

        if not official_bars:
            runtime.kline_validation = "REST_MISSING"
            self.store.add_event(
                account_id,
                _now_ms(),
                "ERROR",
                "KLINE_REST_MISSING",
                "REST did not return the expected closed kline",
                {"start_ms": start_ms, "end_ms": end_ms},
            )
            return False

        synced = False
        for official_bar in sorted(official_bars, key=lambda value: value.start_ms):
            if (
                runtime.last_official_bar_start_ms is not None
                and official_bar.start_ms <= runtime.last_official_bar_start_ms
            ):
                continue
            expected_start = (
                runtime.last_official_bar_start_ms + runtime.strategy.bar_ms
                if runtime.last_official_bar_start_ms is not None
                else None
            )
            has_gap = expected_start is not None and official_bar.start_ms > expected_start
            if has_gap:
                self.store.add_event(
                    account_id,
                    official_bar.end_ms,
                    "ERROR",
                    "KLINE_SEQUENCE_GAP",
                    "Official warehouse kline sequence is incomplete",
                    {
                        "expected_start_ms": expected_start,
                        "received_start_ms": official_bar.start_ms,
                    },
                )
            validation = "GAP" if has_gap else "REST_VERIFIED"
            if (
                not has_gap
                and stream_bar is not None
                and official_bar.start_ms == stream_bar.start_ms
            ):
                differences = _bar_differences(stream_bar, official_bar)
                validation = "RECONCILED" if differences else "MATCHED"
                if differences:
                    runtime.kline_mismatches += 1
                    self.store.add_event(
                        account_id,
                        official_bar.end_ms,
                        "WARN",
                        "KLINE_RECONCILED",
                        "WebSocket kline differed from REST; REST values applied",
                        differences,
                    )
            runtime.kline_validation = validation
            runtime.last_official_bar_start_ms = official_bar.start_ms
            runtime.last_kline_verified_at_ms = _now_ms()
            self.store.upsert_history_bars(
                runtime.instrument,
                self.settings.strategy.bar_minutes,
                [official_bar],
                runtime.feed.kline_source_name,
            )
            synced = True
        return synced

    async def _apply_due_funding(self, runtime: InstrumentRuntime, tick: Tick) -> bool:
        if runtime.instrument.paper_model != "futures":
            return False
        if tick.timestamp_ms - runtime.last_funding_poll_ms < 60_000:
            return False
        runtime.last_funding_poll_ms = tick.timestamp_ms
        try:
            rates = await runtime.feed.funding_rates(
                runtime.funding_cursor_ms,
                tick.timestamp_ms,
            )
        except Exception as exc:
            self.store.add_event(
                runtime.instrument.id,
                tick.timestamp_ms,
                "WARN",
                "FUNDING_SYNC_FAILED",
                f"{type(exc).__name__}: {exc}",
            )
            return False

        applied = False
        for funding in rates:
            payment = self.store.apply_funding(runtime.instrument.id, funding)
            applied = payment is not None or applied
        runtime.funding_cursor_ms = tick.timestamp_ms
        return applied

    def status(self) -> dict[str, Any]:
        values = []
        for runtime in self.runtimes.values():
            view = runtime.strategy.view()
            account = self.store.account(runtime.instrument.id)
            position_quantity = Decimal(account["quantity"])
            has_position = position_quantity != 0
            is_short = position_quantity < 0
            allow_short = runtime.instrument.paper_model == "futures"
            has_pending = self.store.has_pending_order(runtime.instrument.id)
            latest_orders = self.store.orders(runtime.instrument.id, 1)
            values.append(
                {
                    "id": runtime.instrument.id,
                    "symbol": runtime.instrument.symbol,
                    "display_symbol": runtime.instrument.display_symbol,
                    "name": runtime.instrument.name,
                    "venue": runtime.instrument.venue,
                    "asset_type": runtime.instrument.asset_type,
                    "reference_symbol": runtime.instrument.reference_symbol,
                    "paper_model": runtime.instrument.paper_model,
                    "leverage": runtime.instrument.leverage,
                    "margin_mode": runtime.instrument.margin_mode,
                    "position_fraction": _position_fraction(runtime.instrument, self.settings),
                    "fee_bps": (
                        runtime.instrument.fee_bps
                        if runtime.instrument.fee_bps is not None
                        else self.settings.execution.fee_bps
                    ),
                    "slippage_bps": (
                        runtime.instrument.slippage_bps
                        if runtime.instrument.slippage_bps is not None
                        else self.settings.execution.slippage_bps
                    ),
                    "feed": runtime.feed.source_name,
                    "market_state": runtime.feed.market_state,
                    "kline_state": {
                        "source": runtime.feed.kline_source_name,
                        "validation": runtime.kline_validation,
                        "last_official_bar_start_ms": runtime.last_official_bar_start_ms,
                        "last_verified_at_ms": runtime.last_kline_verified_at_ms,
                        "mismatches": runtime.kline_mismatches,
                    },
                    "status": runtime.status,
                    "status_message": runtime.status_message,
                    "reconnects": runtime.reconnects,
                    "last_tick": runtime.last_tick.as_dict() if runtime.last_tick else None,
                    "strategy": _strategy_view(asdict(view)),
                    "decision": _decision_view(
                        view,
                        trading_enabled=self.trading_enabled,
                        has_position=has_position,
                        has_pending_order=has_pending,
                        bar_ms=runtime.strategy.bar_ms,
                        allow_short=allow_short,
                        is_short=is_short,
                        last_order=latest_orders[0] if latest_orders else None,
                    ),
                }
            )
        return {
            "service": self.settings.app_name,
            "environment": self.settings.environment,
            "trading_enabled": self.trading_enabled,
            "started_at_ms": self.started_at_ms,
            "instruments": values,
        }


def _strategy_view(value: dict[str, Any]) -> dict[str, Any]:
    for key in ("atr", "trailing_stop", "price"):
        if value[key] is not None:
            value[key] = str(value[key])
    return value


def _position_fraction(instrument: InstrumentSettings, settings: Settings) -> float:
    return (
        instrument.position_fraction
        if instrument.position_fraction is not None
        else settings.strategy.position_fraction
    )


def _bar_differences(stream_bar: Bar, rest_bar: Bar) -> dict[str, Any]:
    differences: dict[str, Any] = {}
    for field_name in (
        "start_ms",
        "end_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_count",
    ):
        stream_value = getattr(stream_bar, field_name)
        rest_value = getattr(rest_bar, field_name)
        if stream_value != rest_value:
            differences[field_name] = {
                "websocket": str(stream_value),
                "rest": str(rest_value),
            }
    return differences


def _decision_view(
    view: StrategyView,
    *,
    trading_enabled: bool,
    has_position: bool,
    has_pending_order: bool,
    bar_ms: int,
    allow_short: bool = False,
    is_short: bool = False,
    last_order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not trading_enabled:
        state = "PAUSED"
        reason = "TRADING_DISABLED"
        next_trigger = "RESUME_TRADING"
    elif not view.ready:
        state = "WARMING_UP"
        reason = "ATR_NOT_READY"
        next_trigger = "WAIT_FOR_ATR"
    elif has_pending_order:
        state = "ORDER_PENDING"
        reason = "WAITING_NEXT_TICK_FILL"
        next_trigger = "NEXT_TICK_FILL"
    elif has_position and is_short:
        state = "HOLDING_SHORT"
        reason = "PRICE_BELOW_STOP" if view.relation == "below" else "NO_FRESH_UP_CROSS"
        next_trigger = "PRICE_CROSS_ABOVE"
    elif has_position:
        state = "HOLDING_LONG"
        reason = "PRICE_ABOVE_STOP" if view.relation == "above" else "NO_FRESH_DOWN_CROSS"
        next_trigger = "PRICE_CROSS_BELOW"
    elif view.flattened_this_bar:
        state = "REENTRY_LOCKED"
        reason = "SOLD_THIS_BAR"
        next_trigger = "NEXT_BAR_AND_FRESH_UP_CROSS"
    elif view.bought_this_bar:
        state = "BUY_LOCKED"
        reason = "BUY_SIGNAL_USED_THIS_BAR"
        next_trigger = "NEXT_BAR"
    elif allow_short and view.relation == "below":
        state = "ARMED_FOR_LONG"
        reason = "PRICE_BELOW_STOP"
        next_trigger = "PRICE_CROSS_ABOVE"
    elif allow_short:
        state = "ARMED_FOR_SHORT"
        reason = "PRICE_ABOVE_STOP"
        next_trigger = "PRICE_CROSS_BELOW"
    elif view.relation == "below":
        state = "ARMED_FOR_BUY"
        reason = "PRICE_BELOW_STOP"
        next_trigger = "PRICE_CROSS_ABOVE"
    else:
        state = "WAITING_FOR_RESET"
        reason = "PRICE_ALREADY_ABOVE_WITHOUT_FRESH_CROSS"
        next_trigger = "PRICE_BELOW_THEN_CROSS_ABOVE"

    return {
        "state": state,
        "reason": reason,
        "next_trigger": next_trigger,
        "trading_enabled": trading_enabled,
        "has_position": has_position,
        "position_side": "SHORT" if is_short else "LONG" if has_position else "FLAT",
        "allow_short": allow_short,
        "has_pending_order": has_pending_order,
        "strategy_ready": view.ready,
        "buy_lock_open": not view.bought_this_bar,
        "reentry_lock_open": not view.flattened_this_bar,
        "fresh_up_cross": view.last_cross == "UP" and view.last_cross_result == "BUY_SIGNAL",
        "bar_end_ms": view.bar_start_ms + bar_ms if view.bar_start_ms is not None else None,
        "signal_confirmation": "TICK",
        "fill_timing": "NEXT_TICK",
        "last_signal": (
            {
                "side": last_order["side"],
                "status": last_order["status"],
                "timestamp_ms": last_order["submitted_at_ms"],
                "reason": last_order["reason"],
            }
            if last_order
            else None
        ),
    }


def _now_ms() -> int:
    return int(time.time() * 1000)
