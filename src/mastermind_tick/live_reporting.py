"""Read models for the authenticated Binance Spot live-account console."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from mastermind_tick.config import Settings
from mastermind_tick.engine import PaperEngine, _decision_view, _strategy_view
from mastermind_tick.live_spot import LiveSpotTrader
from mastermind_tick.live_store import LiveStore
from mastermind_tick.reporting import (
    _max_drawdown,
    _sharpe_ratio,
    _trade_stats,
    build_return_summary,
)


def build_live_overview(
    settings: Settings,
    paper_engine: PaperEngine,
    store: LiveStore,
    trader: LiveSpotTrader,
) -> dict[str, Any]:
    account = build_live_account(settings, paper_engine, store, trader)
    runtime = account["runtime"]
    return {
        "service": settings.app_name,
        "environment": "live_readonly",
        "trading_enabled": trader.order_submission_ready,
        "started_at_ms": paper_engine.started_at_ms,
        "instruments": [runtime],
        "accounts": [account],
        "strategy_config": {
            "name": settings.strategy.name,
            "bar_minutes": settings.strategy.bar_minutes,
            "atr_period": settings.strategy.atr_period,
            "atr_multiplier": settings.strategy.atr_multiplier,
            "trend_efficiency_period": settings.strategy.trend_efficiency_period,
            "minimum_trend_efficiency": settings.strategy.minimum_trend_efficiency,
            "reversal_confirmation_atr": settings.strategy.reversal_confirmation_atr,
            "one_action_per_bar": True,
            "startup_alignment": False,
            "futures_reversal_mode": "close_then_confirm",
            "signal_confirmation": "tick",
            "fill_timing": "binance_actual",
            "position_fraction": settings.live_spot.position_fraction,
            "fee_bps": 0.0,
            "slippage_bps": 0.0,
        },
    }


def build_live_account(
    settings: Settings,
    paper_engine: PaperEngine,
    store: LiveStore,
    trader: LiveSpotTrader,
) -> dict[str, Any]:
    account_id = settings.live_spot.account_id
    first = store.first_balance(account_id)
    latest = store.latest_balance(account_id)
    points = live_equity(store, account_id, 100_000)
    fills = live_fills(store, account_id, 100_000)
    ledger = _spot_ledger(fills)
    initial_equity = Decimal(first["equity_quote"]) if first else Decimal("0")
    current_equity = Decimal(latest["equity_quote"]) if latest else initial_equity
    total_pnl = current_equity - initial_equity
    total_return = total_pnl / initial_equity if initial_equity else Decimal("0")
    quote_total = (
        Decimal(latest["quote_free"]) + Decimal(latest["quote_locked"])
        if latest
        else Decimal("0")
    )
    base_total = (
        Decimal(latest["base_free"]) + Decimal(latest["base_locked"])
        if latest
        else Decimal("0")
    )
    price = Decimal(latest["reference_price"]) if latest else Decimal("0")
    average_price = ledger["average_price"] if base_total > 0 else Decimal("0")
    unrealized = (
        base_total * (price - average_price) if average_price > 0 else Decimal("0")
    )
    market_runtime = paper_engine.runtimes.get(trader.instrument.market_id)
    market_status = paper_engine.status()
    market_view = next(
        (
            item
            for item in market_status.get("instruments", [])
            if item["id"] == trader.instrument.market_id
        ),
        {},
    )
    strategy_view = trader.strategy.view()
    strategy = _strategy_view(asdict(strategy_view))
    pending = store.pending_orders(account_id)
    normalized_orders = live_orders(store, account_id, 1)
    runtime = {
        "id": account_id,
        "symbol": trader.instrument.symbol,
        "display_symbol": "SOXLB/USDT LIVE",
        "name": "SOXLB Binance Spot Live Account",
        "venue": "Binance Spot",
        "asset_type": "live_tokenized_equity",
        "reference_symbol": trader.instrument.reference_symbol,
        "paper_model": "spot",
        "market_data_id": trader.instrument.market_id,
        "allow_short": False,
        "leverage": 1,
        "margin_mode": "cash",
        "position_fraction": settings.live_spot.position_fraction,
        "target_exposure": settings.live_spot.position_fraction,
        "fee_bps": 0.0,
        "slippage_bps": 0.0,
        "strategy_config": {
            "algorithm_version": trader.strategy.ALGORITHM_VERSION,
            "bar_minutes": settings.strategy.bar_minutes,
            "atr_period": settings.strategy.atr_period,
            "atr_multiplier": settings.strategy.atr_multiplier,
            "trend_efficiency_period": settings.strategy.trend_efficiency_period,
            "minimum_trend_efficiency": settings.strategy.minimum_trend_efficiency,
            "reversal_confirmation_atr": settings.strategy.reversal_confirmation_atr,
            "one_action_per_bar": True,
            "startup_alignment": False,
            "futures_reversal_mode": "close_then_confirm",
            "signal_confirmation": "tick",
            "fill_timing": "binance_actual",
        },
        "feed": "binance_spot_signed",
        "market_state": {},
        "kline_state": market_view.get(
            "kline_state",
            {
                "source": "binance_public_kline_rest",
                "validation": "PENDING",
                "last_official_bar_start_ms": None,
                "last_verified_at_ms": None,
                "mismatches": 0,
            },
        ),
        "status": trader.status,
        "status_message": trader.status_message,
        "reconnects": market_view.get("reconnects", 0),
        "last_tick": (
            trader.last_tick.as_dict()
            if trader.last_tick
            else market_runtime.last_tick.as_dict()
            if market_runtime and market_runtime.last_tick
            else None
        ),
        "strategy": strategy,
        "decision": _decision_view(
            strategy_view,
            trading_enabled=trader.order_submission_ready,
            has_position=base_total > 0,
            has_pending_order=bool(pending),
            bar_ms=trader.strategy.bar_ms,
            last_order=normalized_orders[0] if normalized_orders else None,
        ),
    }
    stats = _trade_stats(fills)
    return {
        "id": account_id,
        "symbol": trader.instrument.symbol,
        "display_symbol": "SOXLB/USDT LIVE",
        "venue": "Binance Spot",
        "currency": trader.instrument.currency,
        "initial_cash": str(initial_equity),
        "cash": str(quote_total),
        "quantity": str(base_total),
        "average_price": str(average_price),
        "realized_pnl": str(ledger["realized_pnl"]),
        "total_fees": str(ledger["total_fees"]),
        "total_funding": "0",
        "equity": str(current_equity),
        "total_pnl": str(total_pnl),
        "total_return": float(total_return),
        "max_drawdown": _max_drawdown(points),
        "sharpe_ratio": _sharpe_ratio(points, settings.strategy.bar_minutes),
        "win_rate": stats["win_rate"],
        "winning_trades": stats["winning_trades"],
        "losing_trades": stats["losing_trades"],
        "last_price": str(price) if latest else None,
        "last_snapshot_ms": int(latest["timestamp_ms"]) if latest else None,
        "unrealized_pnl": str(unrealized),
        "market_value": str(base_total * price),
        "mark_price": None,
        "index_price": None,
        "funding_rate": None,
        "initial_margin": "0",
        "available_balance": str(quote_total),
        "funding_count": 0,
        "fill_count": len(fills),
        "round_trips": stats["round_trips"],
        "runtime": runtime,
    }


def live_equity(
    store: LiveStore,
    account_id: str,
    limit: int = 1000,
    before_ms: int | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "timestamp_ms": int(row["timestamp_ms"]),
            "price": row["reference_price"] or "0",
            "cash": str(Decimal(row["quote_free"]) + Decimal(row["quote_locked"])),
            "quantity": str(Decimal(row["base_free"]) + Decimal(row["base_locked"])),
            "market_value": str(
                (Decimal(row["base_free"]) + Decimal(row["base_locked"]))
                * Decimal(row["reference_price"] or "0")
            ),
            "equity": row["equity_quote"] or "0",
            "unrealized_pnl": "0",
            "realized_pnl": "0",
            "mark_price": None,
            "index_price": None,
            "funding_rate": None,
            "initial_margin": "0",
            "available_balance": str(
                Decimal(row["quote_free"]) + Decimal(row["quote_locked"])
            ),
            "total_funding": "0",
            "atr": row.get("atr"),
            "trailing_stop": row.get("trailing_stop"),
            "relation": row.get("relation"),
            "source": row["source"],
        }
        for row in store.balance_snapshots(account_id, limit, before_ms)
    ]


def live_fills(
    store: LiveStore, account_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    rows = sorted(store.fills(account_id, limit), key=lambda row: int(row["timestamp_ms"]))
    quantity = Decimal("0")
    average_price = Decimal("0")
    result = []
    for row in rows:
        price = Decimal(row["price"])
        fill_quantity = Decimal(row["quantity"])
        notional = Decimal(row["quote_quantity"])
        fee = _fee_in_quote(row)
        before = quantity
        if row["side"] == "BUY":
            next_quantity = quantity + fill_quantity
            average_price = (
                (quantity * average_price + notional + fee) / next_quantity
                if next_quantity
                else Decimal("0")
            )
            quantity = next_quantity
            realized = -fee
            effect = "OPEN"
        else:
            matched = min(quantity, fill_quantity)
            realized = matched * (price - average_price) - fee
            quantity = max(Decimal("0"), quantity - fill_quantity)
            if quantity == 0:
                average_price = Decimal("0")
            effect = "CLOSE"
        result.append(
            {
                "id": f"binance-trade-{row['trade_id']}",
                "account_id": account_id,
                "side": row["side"],
                "timestamp_ms": int(row["timestamp_ms"]),
                "price": row["price"],
                "quantity": row["quantity"],
                "notional": row["quote_quantity"],
                "fee": str(fee),
                "reason": "binance_actual_fill",
                "source": "binance_spot_actual",
                "position_effect": effect,
                "position_before": str(before),
                "position_after": str(quantity),
                "realized_pnl": str(realized),
            }
        )
    return list(reversed(result))


def live_orders(
    store: LiveStore, account_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    result = []
    for row in store.orders(account_id, limit):
        executed = Decimal(row["executed_quantity"])
        quote = Decimal(row["cumulative_quote_quantity"])
        fill_price = quote / executed if executed else None
        result.append(
            {
                "id": row["client_order_id"],
                "account_id": account_id,
                "side": row["side"],
                "status": row["status"],
                "reason": row["reason"],
                "signal_price": row["signal_price"],
                "atr": None,
                "trailing_stop": None,
                "submitted_at_ms": row["submitted_at_ms"] or row["signal_at_ms"],
                "filled_at_ms": (
                    row["updated_at_ms"] if row["status"] == "FILLED" else None
                ),
                "fill_price": str(fill_price) if fill_price is not None else None,
            }
        )
    return result


def build_live_return_summary(
    store: LiveStore, account_id: str, timezone_offset_minutes: int
) -> dict[str, Any]:
    return build_return_summary(
        _LiveReturnStore(store), account_id, timezone_offset_minutes
    )


class _LiveReturnStore:
    def __init__(self, store: LiveStore):
        self.store = store

    def account(self, account_id: str) -> dict[str, Any]:
        first = self.store.first_balance(account_id)
        if first is None:
            raise LookupError(account_id)
        return {
            "initial_cash": first["equity_quote"],
            "created_at_ms": first["timestamp_ms"],
        }

    def equity(self, account_id: str, limit: int) -> list[dict[str, Any]]:
        return live_equity(self.store, account_id, limit)

    def equity_at_boundaries(
        self, account_id: str, boundaries_ms: list[int]
    ) -> dict[int, dict[str, Any] | None]:
        return self.store.balance_at_boundaries(account_id, boundaries_ms)


def _spot_ledger(fills: list[dict[str, Any]]) -> dict[str, Decimal]:
    quantity = Decimal("0")
    average_price = Decimal("0")
    realized = Decimal("0")
    fees = Decimal("0")
    for fill in sorted(fills, key=lambda row: int(row["timestamp_ms"])):
        fill_quantity = Decimal(fill["quantity"])
        notional = Decimal(fill["notional"])
        fee = Decimal(fill["fee"])
        fees += fee
        if fill["side"] == "BUY":
            next_quantity = quantity + fill_quantity
            average_price = (
                (quantity * average_price + notional + fee) / next_quantity
                if next_quantity
                else Decimal("0")
            )
            quantity = next_quantity
            realized -= fee
        else:
            matched = min(quantity, fill_quantity)
            realized += matched * (Decimal(fill["price"]) - average_price) - fee
            quantity = max(Decimal("0"), quantity - fill_quantity)
            if quantity == 0:
                average_price = Decimal("0")
    return {
        "quantity": quantity,
        "average_price": average_price,
        "realized_pnl": realized,
        "total_fees": fees,
    }


def _fee_in_quote(row: dict[str, Any]) -> Decimal:
    commission = Decimal(row["commission"])
    if row["commission_asset"] == "USDT":
        return commission
    if row["commission_asset"] == "SOXLB":
        return commission * Decimal(row["price"])
    return Decimal("0")
