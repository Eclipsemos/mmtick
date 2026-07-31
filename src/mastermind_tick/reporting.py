"""Read models for the live paper console."""

from __future__ import annotations

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
        points = store.equity(account_id, 2000)
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
            "position_fraction": engine.settings.strategy.position_fraction,
            "fee_bps": engine.settings.execution.fee_bps,
            "slippage_bps": engine.settings.execution.slippage_bps,
        },
    }


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
    ordered = sorted(fills, key=lambda fill: int(fill["timestamp_ms"]))
    funding = sorted(
        funding_payments or [],
        key=lambda payment: int(payment["timestamp_ms"]),
    )
    entry: dict[str, Any] | None = None
    wins = 0
    completed = 0
    for fill in ordered:
        quantity = Decimal(fill["quantity"])
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
        net_pnl += sum(
            (
                Decimal(payment["amount"])
                for payment in funding
                if int(entry["timestamp_ms"])
                <= int(payment["timestamp_ms"])
                <= int(fill["timestamp_ms"])
            ),
            Decimal("0"),
        )
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
