"""Read models for the authenticated SOXL USD-M Futures live console."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from mastermind_tick.config import Settings
from mastermind_tick.engine import PaperEngine, _decision_view, _strategy_view
from mastermind_tick.live_futures import LiveFuturesTrader
from mastermind_tick.live_reporting import (
    _performance_equity_points,
    _return_inception,
    build_cash_flow_return_summary,
)
from mastermind_tick.live_store import LiveStore
from mastermind_tick.reporting import _max_drawdown, _sharpe_ratio, _trade_stats


def build_live_futures_overview(
    settings: Settings,
    paper_engine: PaperEngine,
    store: LiveStore,
    trader: LiveFuturesTrader,
) -> dict[str, Any]:
    account = build_live_futures_account(settings, paper_engine, store, trader)
    runtime = account["runtime"]
    return {
        "service": settings.app_name,
        "environment": "live_futures_readonly",
        "trading_enabled": trader.order_submission_ready,
        "started_at_ms": paper_engine.started_at_ms,
        "instruments": [runtime],
        "accounts": [account],
        "strategy_config": {
            "name": settings.live_futures.strategy_name,
            "bar_minutes": settings.strategy.bar_minutes,
            "atr_period": settings.live_futures.atr_period,
            "atr_multiplier": settings.live_futures.atr_multiplier,
            "trend_efficiency_period": settings.live_futures.trend_efficiency_period,
            "minimum_trend_efficiency": settings.live_futures.minimum_trend_efficiency,
            "reversal_confirmation_atr": settings.live_futures.reversal_confirmation_atr,
            "profit_activation_atr": settings.live_futures.profit_activation_atr,
            "profit_trailing_atr": settings.live_futures.profit_trailing_atr,
            "continuation_reentry_atr": settings.live_futures.continuation_reentry_atr,
            "allow_short": settings.live_futures.allow_short,
            "one_action_per_bar": True,
            "startup_alignment": False,
            "futures_reversal_mode": "close_then_confirm",
            "signal_confirmation": "tick",
            "fill_timing": "binance_actual",
            "position_fraction": settings.live_futures.position_fraction,
            "fee_bps": 0.0,
            "slippage_bps": 0.0,
        },
    }


def build_live_futures_account(
    settings: Settings,
    paper_engine: PaperEngine,
    store: LiveStore,
    trader: LiveFuturesTrader,
) -> dict[str, Any]:
    config = settings.live_futures
    account_id = config.account_id
    first = store.first_futures_snapshot(account_id)
    latest = store.latest_futures_snapshot(account_id)
    points = live_futures_equity(store, account_id, 100_000)
    flows = store.cash_flows(account_id)
    performance_points = _performance_equity_points(points, flows)
    fills = live_futures_fills(store, account_id, 100_000)
    funding = live_futures_funding(store, account_id, 100_000)
    initial_equity = Decimal(first["margin_balance"]) if first else Decimal("0")
    current_equity = Decimal(latest["margin_balance"]) if latest else initial_equity
    first_timestamp_ms = int(first["timestamp_ms"]) if first else 0
    return_base, _, return_points = _return_inception(
        performance_points,
        flows,
        initial_equity,
        first_timestamp_ms,
    )
    net_cash_flow = sum(
        (
            Decimal(flow["amount_quote"])
            for flow in flows
            if int(flow["timestamp_ms"]) > first_timestamp_ms
        ),
        Decimal("0"),
    )
    performance_equity = (
        Decimal(performance_points[-1]["equity"]) if performance_points else initial_equity
    )
    total_pnl = current_equity - initial_equity - net_cash_flow
    total_return = performance_equity / return_base - Decimal("1") if return_base else Decimal("0")
    quantity = Decimal(latest["position_quantity"]) if latest else Decimal("0")
    entry_price = Decimal(latest["entry_price"]) if latest else Decimal("0")
    mark_price = Decimal(latest["mark_price"]) if latest else Decimal("0")
    unrealized = Decimal(latest["unrealized_pnl"]) if latest else Decimal("0")
    available = Decimal(latest["available_balance"]) if latest else Decimal("0")
    actual_leverage = int(latest["leverage"]) if latest else config.leverage
    margin_used = (
        abs(quantity) * mark_price / Decimal(actual_leverage)
        if actual_leverage > 0
        else Decimal("0")
    )
    total_fees = sum((Decimal(fill["fee"]) for fill in fills), Decimal("0"))
    realized_pnl = sum((Decimal(fill["realized_pnl"] or "0") for fill in fills), Decimal("0"))
    total_funding = sum((Decimal(payment["amount"]) for payment in funding), Decimal("0"))
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
    strategy.update(trader.profit_protection_view())
    strategy.update(trader.continuation_reentry_view())
    normalized_orders = live_futures_orders(store, account_id, 1)
    pending = store.pending_orders(account_id)
    runtime = {
        "id": account_id,
        "symbol": trader.instrument.symbol,
        "display_symbol": "SOXL/USDT PERP LIVE",
        "name": "SOXL Binance USD-M Futures Live Account",
        "venue": "Binance USD-M Futures",
        "asset_type": "live_tradifi_perpetual",
        "reference_symbol": trader.instrument.reference_symbol,
        "paper_model": "futures",
        "market_data_id": trader.instrument.market_id,
        "allow_short": config.allow_short,
        "leverage": config.leverage,
        "margin_mode": config.margin_mode,
        "position_fraction": config.position_fraction,
        "target_exposure": config.position_fraction * config.leverage,
        "fee_bps": 0.0,
        "slippage_bps": 0.0,
        "strategy_config": {
            "algorithm_version": trader.strategy.ALGORITHM_VERSION,
            "bar_minutes": settings.strategy.bar_minutes,
            "atr_period": config.atr_period,
            "atr_multiplier": config.atr_multiplier,
            "trend_efficiency_period": config.trend_efficiency_period,
            "minimum_trend_efficiency": config.minimum_trend_efficiency,
            "reversal_confirmation_atr": config.reversal_confirmation_atr,
            "profit_activation_atr": config.profit_activation_atr,
            "profit_trailing_atr": config.profit_trailing_atr,
            "continuation_reentry_atr": config.continuation_reentry_atr,
            "one_action_per_bar": True,
            "startup_alignment": False,
            "futures_reversal_mode": "close_then_confirm",
            "signal_confirmation": "tick",
            "fill_timing": "binance_actual",
        },
        "feed": "binance_usdm_signed",
        "market_state": market_view.get("market_state", {}),
        "kline_state": market_view.get(
            "kline_state",
            {
                "source": "binance_futures_kline_rest",
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
            has_position=quantity != 0,
            has_pending_order=bool(pending),
            bar_ms=trader.strategy.bar_ms,
            allow_short=config.allow_short,
            is_short=quantity < 0,
            last_order=normalized_orders[0] if normalized_orders else None,
        ),
    }
    stats = _trade_stats(fills, funding)
    market_state = runtime["market_state"]
    return {
        "id": account_id,
        "symbol": trader.instrument.symbol,
        "display_symbol": "SOXL/USDT PERP LIVE",
        "venue": "Binance USD-M Futures",
        "currency": trader.instrument.currency,
        "initial_cash": str(initial_equity),
        "cash": str(Decimal(latest["wallet_balance"]) if latest else Decimal("0")),
        "quantity": str(quantity),
        "average_price": str(entry_price),
        "realized_pnl": str(realized_pnl),
        "total_fees": str(total_fees),
        "total_funding": str(total_funding),
        "equity": str(current_equity),
        "total_pnl": str(total_pnl),
        "total_return": float(total_return),
        "net_cash_flow": str(net_cash_flow),
        "max_drawdown": _max_drawdown(return_points),
        "sharpe_ratio": _sharpe_ratio(return_points, settings.strategy.bar_minutes),
        "win_rate": stats["win_rate"],
        "winning_trades": stats["winning_trades"],
        "losing_trades": stats["losing_trades"],
        "last_price": str(mark_price) if latest else None,
        "last_snapshot_ms": int(latest["timestamp_ms"]) if latest else None,
        "unrealized_pnl": str(unrealized),
        "market_value": str(abs(quantity) * mark_price),
        "mark_price": str(mark_price) if latest else None,
        "index_price": market_state.get("index_price"),
        "funding_rate": market_state.get("funding_rate"),
        "initial_margin": str(margin_used),
        "available_balance": str(available),
        "funding_count": len(funding),
        "fill_count": len(fills),
        "round_trips": stats["round_trips"],
        "runtime": runtime,
    }


def live_futures_equity(
    store: LiveStore,
    account_id: str,
    limit: int = 1000,
    before_ms: int | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "timestamp_ms": int(row["timestamp_ms"]),
            "price": row["mark_price"],
            "cash": row["wallet_balance"],
            "quantity": row["position_quantity"],
            "market_value": str(
                abs(Decimal(row["position_quantity"])) * Decimal(row["mark_price"])
            ),
            "equity": row["margin_balance"],
            "unrealized_pnl": row["unrealized_pnl"],
            "realized_pnl": "0",
            "mark_price": row["mark_price"],
            "index_price": None,
            "funding_rate": None,
            "initial_margin": str(
                abs(Decimal(row["position_quantity"]))
                * Decimal(row["mark_price"])
                / Decimal(int(row["leverage"]))
                if int(row["leverage"])
                else Decimal("0")
            ),
            "available_balance": row["available_balance"],
            "total_funding": "0",
            "atr": row.get("atr"),
            "trailing_stop": row.get("trailing_stop"),
            "relation": row.get("relation"),
            "source": row["source"],
        }
        for row in store.futures_snapshots(account_id, limit, before_ms)
    ]


def live_futures_fills(store: LiveStore, account_id: str, limit: int = 200) -> list[dict[str, Any]]:
    rows = sorted(
        store.fills(account_id, limit),
        key=lambda row: (
            int(row["timestamp_ms"]),
            int(row["order_id"]),
            int(row["trade_id"]),
        ),
    )
    order_fills: dict[int, dict[str, Any]] = {}
    for row in rows:
        order_id = int(row["order_id"])
        raw = row.get("raw_json") or {}
        aggregate = order_fills.setdefault(
            order_id,
            {
                "order_id": order_id,
                "client_order_id": row["client_order_id"],
                "side": str(row["side"]),
                "position_side": str(raw.get("positionSide", "BOTH")),
                "timestamp_ms": int(row["timestamp_ms"]),
                "quantity": Decimal("0"),
                "notional": Decimal("0"),
                "fee": Decimal("0"),
                "realized_pnl": Decimal("0"),
            },
        )
        aggregate["timestamp_ms"] = max(int(aggregate["timestamp_ms"]), int(row["timestamp_ms"]))
        aggregate["quantity"] += Decimal(row["quantity"])
        aggregate["notional"] += Decimal(row["quote_quantity"])
        aggregate["fee"] += _fee_in_quote(row)
        aggregate["realized_pnl"] += Decimal(str(raw.get("realizedPnl", "0")))

    position = Decimal("0")
    result = []
    for row in sorted(
        order_fills.values(),
        key=lambda item: (int(item["timestamp_ms"]), int(item["order_id"])),
    ):
        side = str(row["side"])
        position_side = str(row["position_side"])
        quantity = Decimal(row["quantity"])
        before = position
        if position_side == "LONG":
            delta = quantity if side == "BUY" else -quantity
        elif position_side == "SHORT":
            delta = -quantity if side == "SELL" else quantity
        else:
            delta = quantity if side == "BUY" else -quantity
        position += delta
        effect = "OPEN" if abs(position) > abs(before) else "CLOSE"
        fee = Decimal(row["fee"])
        realized = Decimal(row["realized_pnl"]) - fee
        notional = Decimal(row["notional"])
        price = notional / quantity if quantity else Decimal("0")
        result.append(
            {
                "id": f"binance-futures-order-{row['order_id']}",
                "account_id": account_id,
                "side": side,
                "timestamp_ms": int(row["timestamp_ms"]),
                "price": str(price),
                "quantity": str(quantity),
                "notional": str(notional),
                "fee": str(fee),
                "reason": f"binance_actual_{position_side.lower()}",
                "source": "binance_usdm_actual",
                "position_effect": effect,
                "position_before": str(before),
                "position_after": str(position),
                "realized_pnl": str(realized),
            }
        )
    return list(reversed(result))


def live_futures_orders(
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
                "filled_at_ms": (row["updated_at_ms"] if row["status"] == "FILLED" else None),
                "fill_price": str(fill_price) if fill_price is not None else None,
            }
        )
    return result


def live_futures_funding(
    store: LiveStore, account_id: str, limit: int = 1000
) -> list[dict[str, Any]]:
    return [
        {
            "id": f"binance-income-{row['transaction_id']}",
            "account_id": account_id,
            "symbol": row["symbol"],
            "timestamp_ms": int(row["timestamp_ms"]),
            "rate": "0",
            "mark_price": "0",
            "quantity": "0",
            "notional": "0",
            "amount": row["income"],
            "source": "binance_usdm_income",
        }
        for row in store.income(account_id, limit)
    ]


def build_live_futures_return_summary(
    store: LiveStore, account_id: str, timezone_offset_minutes: int
) -> dict[str, Any]:
    first = store.first_futures_snapshot(account_id)
    if first is None:
        raise LookupError(account_id)
    return build_cash_flow_return_summary(
        account_id=account_id,
        initial_equity=str(first["margin_balance"]),
        created_at_ms=int(first["timestamp_ms"]),
        raw_points=live_futures_equity(store, account_id, 1_000_000),
        cash_flows=store.cash_flows(account_id),
        timezone_offset_minutes=timezone_offset_minutes,
    )


def _fee_in_quote(row: dict[str, Any]) -> Decimal:
    commission = Decimal(row["commission"])
    return commission if row["commission_asset"] == "USDT" else Decimal("0")
