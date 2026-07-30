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
        fills = store.fills(account_id, 1000)
        runtime = runtime_by_id.get(account_id, {})
        accounts.append(
            {
                **account,
                "equity": str(equity),
                "total_pnl": str(total_pnl),
                "total_return": float(total_return),
                "max_drawdown": max_drawdown,
                "last_price": latest["price"] if latest else None,
                "last_snapshot_ms": latest["timestamp_ms"] if latest else None,
                "unrealized_pnl": latest["unrealized_pnl"] if latest else "0",
                "market_value": latest["market_value"] if latest else "0",
                "fill_count": len(fills),
                "round_trips": sum(1 for fill in fills if fill["side"] == "SELL"),
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
