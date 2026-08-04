"""Read models for the authenticated Binance Spot live-account console."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta, timezone
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
    flows = store.cash_flows(account_id)
    performance_points = _performance_equity_points(points, flows)
    fills = live_fills(store, account_id, 100_000)
    ledger = _spot_ledger(fills)
    initial_equity = Decimal(first["equity_quote"]) if first else Decimal("0")
    current_equity = Decimal(latest["equity_quote"]) if latest else initial_equity
    first_timestamp_ms = int(first["timestamp_ms"]) if first else 0
    net_cash_flow = sum(
        (
            Decimal(flow["amount_quote"])
            for flow in flows
            if int(flow["timestamp_ms"]) > first_timestamp_ms
        ),
        Decimal("0"),
    )
    total_pnl = current_equity - initial_equity - net_cash_flow
    performance_equity = (
        Decimal(performance_points[-1]["equity"])
        if performance_points
        else initial_equity
    )
    total_return = (
        performance_equity / initial_equity - Decimal("1")
        if initial_equity
        else Decimal("0")
    )
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
        "net_cash_flow": str(net_cash_flow),
        "max_drawdown": _max_drawdown(performance_points),
        "sharpe_ratio": _sharpe_ratio(
            performance_points, settings.strategy.bar_minutes
        ),
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
    first = store.first_balance(account_id)
    if first is None:
        raise LookupError(account_id)
    raw_points = live_equity(store, account_id, 1_000_000)
    return build_cash_flow_return_summary(
        account_id=account_id,
        initial_equity=str(first["equity_quote"]),
        created_at_ms=int(first["timestamp_ms"]),
        raw_points=raw_points,
        cash_flows=store.cash_flows(account_id),
        timezone_offset_minutes=timezone_offset_minutes,
    )


def build_cash_flow_return_summary(
    *,
    account_id: str,
    initial_equity: str,
    created_at_ms: int,
    raw_points: list[dict[str, Any]],
    cash_flows: list[dict[str, Any]],
    timezone_offset_minutes: int,
) -> dict[str, Any]:
    performance_points = _performance_equity_points(
        raw_points, cash_flows
    )
    initial = Decimal(initial_equity)
    latest_raw = raw_points[-1] if raw_points else None
    latest_performance = performance_points[-1] if performance_points else None
    as_of_ms = int(latest_raw["timestamp_ms"]) if latest_raw else created_at_ms
    current_equity = Decimal(latest_raw["equity"]) if latest_raw else initial
    current_performance = (
        Decimal(latest_performance["equity"]) if latest_performance else initial
    )
    local_timezone = timezone(timedelta(minutes=timezone_offset_minutes))
    as_of_date = datetime.fromtimestamp(as_of_ms / 1000, local_timezone).date()

    daily_dates = [as_of_date - timedelta(days=offset) for offset in range(29, -1, -1)]
    current_week = as_of_date - timedelta(days=as_of_date.weekday())
    weekly_dates = [current_week - timedelta(weeks=offset) for offset in range(11, -1, -1)]
    current_month = as_of_date.replace(day=1)
    monthly_dates = [_shift_month(current_month, -offset) for offset in range(11, -1, -1)]

    daily = [
        _live_return_period(
            value,
            value + timedelta(days=1),
            local_timezone,
            created_at_ms,
            as_of_ms,
            initial,
            raw_points,
            performance_points,
            label=value.isoformat(),
        )
        for value in daily_dates
    ]
    weekly = [
        _live_return_period(
            value,
            value + timedelta(days=7),
            local_timezone,
            created_at_ms,
            as_of_ms,
            initial,
            raw_points,
            performance_points,
            label=f"{value.isocalendar().year} W{value.isocalendar().week:02d}",
        )
        for value in weekly_dates
    ]
    monthly = [
        _live_return_period(
            value,
            _shift_month(value, 1),
            local_timezone,
            created_at_ms,
            as_of_ms,
            initial,
            raw_points,
            performance_points,
            label=value.strftime("%Y-%m"),
        )
        for value in monthly_dates
    ]

    elapsed_days = max(0.0, (as_of_ms - created_at_ms) / 86_400_000)
    total_return = (
        current_performance / initial - Decimal("1") if initial else Decimal("0")
    )
    thirty_day_start_ms = _date_start_ms(daily_dates[0], local_timezone)
    thirty_day_start = _point_before(performance_points, thirty_day_start_ms)
    thirty_day_start_equity = (
        Decimal(thirty_day_start["equity"]) if thirty_day_start else initial
    )
    return_30d = (
        float(current_performance / thirty_day_start_equity - Decimal("1"))
        if thirty_day_start_equity
        else None
    )
    annualized_return = None
    if elapsed_days >= 1 and initial > 0 and current_performance > 0:
        annualized_return = float(
            (current_performance / initial)
            ** (Decimal("365.2425") / Decimal(str(elapsed_days)))
            - Decimal("1")
        )
    return {
        "account_id": account_id,
        "generated_at_ms": int(datetime.now(UTC).timestamp() * 1000),
        "as_of_ms": as_of_ms,
        "timezone_offset_minutes": timezone_offset_minutes,
        "initial_equity": str(initial),
        "current_equity": str(current_equity),
        "total_return": float(total_return),
        "annualized_return": annualized_return,
        "elapsed_days": elapsed_days,
        "return_30d": return_30d,
        "current_week_return": weekly[-1]["return"],
        "current_month_return": monthly[-1]["return"],
        "daily": daily,
        "weekly": [period for period in weekly if period["return"] is not None],
        "monthly": [period for period in monthly if period["return"] is not None],
    }


def _performance_equity_points(
    raw_points: list[dict[str, Any]],
    cash_flows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not raw_points:
        return []
    ordered = sorted(raw_points, key=lambda point: int(point["timestamp_ms"]))
    first_equity = Decimal(ordered[0]["equity"])
    performance_equity = first_equity
    previous_raw = first_equity
    previous_timestamp = int(ordered[0]["timestamp_ms"])
    flow_index = 0
    flows = sorted(cash_flows, key=lambda flow: int(flow["timestamp_ms"]))
    while flow_index < len(flows) and int(flows[flow_index]["timestamp_ms"]) <= previous_timestamp:
        flow_index += 1
    result = [{**ordered[0], "equity": str(performance_equity)}]
    for point in ordered[1:]:
        timestamp_ms = int(point["timestamp_ms"])
        interval_flow = Decimal("0")
        while flow_index < len(flows) and int(flows[flow_index]["timestamp_ms"]) <= timestamp_ms:
            interval_flow += Decimal(flows[flow_index]["amount_quote"])
            flow_index += 1
        current_raw = Decimal(point["equity"])
        if previous_raw:
            performance_equity *= (current_raw - interval_flow) / previous_raw
        previous_raw = current_raw
        previous_timestamp = timestamp_ms
        result.append({**point, "equity": str(performance_equity)})
    return result


def _live_return_period(
    start: date,
    end: date,
    local_timezone: timezone,
    created_at_ms: int,
    as_of_ms: int,
    initial: Decimal,
    raw_points: list[dict[str, Any]],
    performance_points: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    start_ms = _date_start_ms(start, local_timezone)
    end_ms = _date_start_ms(end, local_timezone)
    effective_end_ms = min(end_ms, as_of_ms + 1)
    if created_at_ms >= effective_end_ms:
        period_return = None
        closing_equity = None
    else:
        start_point = _point_before(performance_points, start_ms)
        end_point = _point_before(performance_points, effective_end_ms)
        raw_end_point = _point_before(raw_points, effective_end_ms)
        start_equity = Decimal(start_point["equity"]) if start_point else initial
        end_equity = Decimal(end_point["equity"]) if end_point else initial
        closing_equity = Decimal(raw_end_point["equity"]) if raw_end_point else initial
        period_return = (
            float(end_equity / start_equity - Decimal("1")) if start_equity else None
        )
    return {
        "key": label,
        "label": label,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "equity": str(closing_equity) if closing_equity is not None else None,
        "return": period_return,
    }


def _point_before(
    points: list[dict[str, Any]], boundary_ms: int
) -> dict[str, Any] | None:
    return next(
        (point for point in reversed(points) if int(point["timestamp_ms"]) < boundary_ms),
        None,
    )


def _date_start_ms(value: date, local_timezone: timezone) -> int:
    return int(datetime.combine(value, time.min, local_timezone).timestamp() * 1000)


def _shift_month(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


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
