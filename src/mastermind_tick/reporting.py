"""Read models for the live paper console."""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from mastermind_tick.engine import PaperEngine
from mastermind_tick.store import PaperStore


def build_overview(engine: PaperEngine, store: PaperStore) -> dict[str, Any]:
    runtime_status = engine.status()
    runtime_by_id = {item["id"]: item for item in runtime_status["instruments"]}
    active_ids = set(runtime_by_id) or {item.id for item in engine.settings.instruments}
    accounts = []
    for account in store.accounts():
        account_id = account["id"]
        if account_id not in active_ids:
            continue
        points = store.equity(account_id, 100_000)
        latest = points[-1] if points else None
        initial = Decimal(account["initial_cash"])
        equity = Decimal(latest["equity"]) if latest else Decimal(account["cash"])
        total_pnl = equity - initial
        total_return = total_pnl / initial if initial else Decimal("0")
        max_drawdown = _max_drawdown(points)
        sharpe_ratio = _sharpe_ratio(points, engine.settings.strategy.bar_minutes)
        fills = store.fills(account_id, 100_000)
        funding_payments = store.funding_payments(account_id, 100_000)
        trade_stats = _trade_stats(fills, funding_payments)
        runtime = runtime_by_id.get(account_id, {})
        accounts.append(
            {
                **account,
                "equity": str(equity),
                "total_pnl": str(total_pnl),
                "total_return": float(total_return),
                "max_drawdown": max_drawdown,
                "sharpe_ratio": sharpe_ratio,
                "last_price": latest["price"] if latest else None,
                "last_snapshot_ms": latest["timestamp_ms"] if latest else None,
                "unrealized_pnl": latest["unrealized_pnl"] if latest else "0",
                "market_value": latest["market_value"] if latest else "0",
                "mark_price": latest["mark_price"] if latest else None,
                "index_price": latest["index_price"] if latest else None,
                "funding_rate": latest["funding_rate"] if latest else None,
                "initial_margin": latest["initial_margin"] if latest else "0",
                "available_balance": (
                    account["cash"]
                    if account["paper_model"] == "spot"
                    else latest["available_balance"]
                    if latest
                    else account["cash"]
                ),
                "funding_count": len(funding_payments),
                "fill_count": len(fills),
                "round_trips": trade_stats["round_trips"],
                "winning_trades": trade_stats["winning_trades"],
                "losing_trades": trade_stats["losing_trades"],
                "win_rate": trade_stats["win_rate"],
                "runtime": runtime,
            }
        )

    return {
        **runtime_status,
        "accounts": accounts,
        "strategy_config": {
            "name": engine.settings.strategy.name,
            "bar_minutes": engine.settings.strategy.bar_minutes,
            "atr_period": engine.settings.strategy.atr_period,
            "atr_multiplier": engine.settings.strategy.atr_multiplier,
            "trend_efficiency_period": engine.settings.strategy.trend_efficiency_period,
            "minimum_trend_efficiency": engine.settings.strategy.minimum_trend_efficiency,
            "reversal_confirmation_atr": engine.settings.strategy.reversal_confirmation_atr,
            "one_action_per_bar": True,
            "startup_alignment": True,
            "futures_reversal_mode": "close_then_confirm",
            "signal_confirmation": "tick",
            "fill_timing": "next_tick",
            "position_fraction": engine.settings.strategy.position_fraction,
            "fee_bps": engine.settings.execution.fee_bps,
            "slippage_bps": engine.settings.execution.slippage_bps,
        },
    }


def build_return_summary(
    store: PaperStore,
    account_id: str,
    timezone_offset_minutes: int = 0,
) -> dict[str, Any]:
    account = store.account(account_id)
    latest_points = store.equity(account_id, 1)
    latest = latest_points[-1] if latest_points else None
    initial = Decimal(account["initial_cash"])
    created_at_ms = int(account["created_at_ms"])
    as_of_ms = int(latest["timestamp_ms"]) if latest else created_at_ms
    current_equity = Decimal(latest["equity"]) if latest else initial
    local_timezone = timezone(timedelta(minutes=timezone_offset_minutes))
    as_of_date = datetime.fromtimestamp(as_of_ms / 1000, local_timezone).date()

    daily_dates = [as_of_date - timedelta(days=offset) for offset in range(29, -1, -1)]
    current_week = as_of_date - timedelta(days=as_of_date.weekday())
    weekly_dates = [current_week - timedelta(weeks=offset) for offset in range(11, -1, -1)]
    current_month = as_of_date.replace(day=1)
    monthly_dates = [_shift_month(current_month, -offset) for offset in range(11, -1, -1)]

    daily_ranges = [(value, value + timedelta(days=1)) for value in daily_dates]
    weekly_ranges = [(value, value + timedelta(days=7)) for value in weekly_dates]
    monthly_ranges = [
        (value, _shift_month(value, 1))
        for value in monthly_dates
    ]
    all_ranges = [*daily_ranges, *weekly_ranges, *monthly_ranges]
    boundaries = {
        _date_start_ms(value, local_timezone)
        for period_range in all_ranges
        for value in period_range
    }
    boundaries.add(as_of_ms + 1)
    closing_points = store.equity_at_boundaries(account_id, list(boundaries))

    daily = [
        _return_period(
            start,
            end,
            local_timezone,
            created_at_ms,
            as_of_ms,
            initial,
            closing_points,
            label=start.isoformat(),
        )
        for start, end in daily_ranges
    ]
    weekly = [
        _return_period(
            start,
            end,
            local_timezone,
            created_at_ms,
            as_of_ms,
            initial,
            closing_points,
            label=f"{start.isocalendar().year} W{start.isocalendar().week:02d}",
        )
        for start, end in weekly_ranges
    ]
    monthly = [
        _return_period(
            start,
            end,
            local_timezone,
            created_at_ms,
            as_of_ms,
            initial,
            closing_points,
            label=start.strftime("%Y-%m"),
        )
        for start, end in monthly_ranges
    ]

    elapsed_days = max(0.0, (as_of_ms - created_at_ms) / 86_400_000)
    total_return = current_equity / initial - Decimal("1") if initial else Decimal("0")
    first_daily_start_ms = _date_start_ms(daily_dates[0], local_timezone)
    first_daily_point = closing_points.get(first_daily_start_ms)
    thirty_day_start_equity = (
        Decimal(first_daily_point["equity"]) if first_daily_point else initial
    )
    return_30d = (
        float(current_equity / thirty_day_start_equity - Decimal("1"))
        if thirty_day_start_equity
        else None
    )
    annualized_return = None
    if elapsed_days >= 1 and initial > 0 and current_equity > 0:
        annualized_return = float(
            (current_equity / initial)
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


def _return_period(
    start: date,
    end: date,
    local_timezone: timezone,
    created_at_ms: int,
    as_of_ms: int,
    initial: Decimal,
    closing_points: dict[int, dict[str, Any] | None],
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
        start_point = closing_points.get(start_ms)
        end_point = closing_points.get(effective_end_ms)
        start_equity = Decimal(start_point["equity"]) if start_point else initial
        closing_equity = Decimal(end_point["equity"]) if end_point else initial
        period_return = (
            float(closing_equity / start_equity - Decimal("1")) if start_equity else None
        )
    return {
        "key": label,
        "label": label,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "equity": str(closing_equity) if closing_equity is not None else None,
        "return": period_return,
    }


def _date_start_ms(value: date, local_timezone: timezone) -> int:
    return int(datetime.combine(value, time.min, local_timezone).timestamp() * 1000)


def _shift_month(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _max_drawdown(points: list[dict[str, Any]]) -> float:
    peak: Decimal | None = None
    worst = Decimal("0")
    for point in points:
        value = Decimal(point["equity"])
        peak = value if peak is None else max(peak, value)
        if peak:
            worst = min(worst, value / peak - Decimal("1"))
    return float(worst)


def _sharpe_ratio(points: list[dict[str, Any]], bar_minutes: int) -> float | None:
    interval_ms = bar_minutes * 60_000
    bucket_equity: dict[int, Decimal] = {}
    for point in points:
        bucket = int(point["timestamp_ms"]) // interval_ms
        bucket_equity[bucket] = Decimal(point["equity"])

    values = list(bucket_equity.values())
    returns = [
        current / previous - Decimal("1")
        for previous, current in zip(values, values[1:], strict=False)
        if previous
    ]
    if len(returns) < 2:
        return None

    count = Decimal(len(returns))
    mean = sum(returns, Decimal("0")) / count
    variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns) - 1)
    if variance <= 0:
        return None

    periods_per_year = Decimal(365 * 24 * 60) / Decimal(bar_minutes)
    return float(mean / variance.sqrt() * periods_per_year.sqrt())


def _trade_stats(
    fills: list[dict[str, Any]],
    funding_payments: list[dict[str, Any]] | None = None,
) -> dict[str, int | float | None]:
    ordered = sorted(
        fills,
        key=lambda fill: (
            int(fill["timestamp_ms"]),
            0 if fill.get("position_effect") == "CLOSE" else 1,
        ),
    )
    funding = sorted(
        funding_payments or [],
        key=lambda payment: int(payment["timestamp_ms"]),
    )
    entry: dict[str, Any] | None = None
    wins = 0
    completed = 0
    for fill in ordered:
        quantity = Decimal(fill["quantity"])
        effect = fill.get("position_effect")
        if effect == "OPEN" and quantity > 0:
            entry = fill
            continue
        if effect == "CLOSE" and quantity > 0 and entry is not None:
            net_pnl = Decimal(fill.get("realized_pnl") or "0") - Decimal(entry["fee"])
            net_pnl += _funding_between(funding, entry, fill)
            completed += 1
            if net_pnl > 0:
                wins += 1
            entry = None
            continue

        if fill["side"] == "BUY" and quantity > 0:
            entry = fill
            continue
        if fill["side"] != "SELL" or quantity <= 0 or entry is None:
            continue

        entry_quantity = Decimal(entry["quantity"])
        if entry_quantity <= 0:
            continue
        matched_quantity = min(entry_quantity, quantity)
        entry_unit_cost = (Decimal(entry["notional"]) + Decimal(entry["fee"])) / entry_quantity
        exit_unit_proceeds = (Decimal(fill["notional"]) - Decimal(fill["fee"])) / quantity
        net_pnl = (exit_unit_proceeds - entry_unit_cost) * matched_quantity
        net_pnl += _funding_between(funding, entry, fill)
        completed += 1
        if net_pnl > 0:
            wins += 1
        entry = None

    return {
        "round_trips": completed,
        "winning_trades": wins,
        "losing_trades": completed - wins,
        "win_rate": wins / completed if completed else None,
    }


def _funding_between(
    funding: list[dict[str, Any]],
    entry: dict[str, Any],
    exit_fill: dict[str, Any],
) -> Decimal:
    return sum(
        (
            Decimal(payment["amount"])
            for payment in funding
            if int(entry["timestamp_ms"])
            <= int(payment["timestamp_ms"])
            <= int(exit_fill["timestamp_ms"])
        ),
        Decimal("0"),
    )
