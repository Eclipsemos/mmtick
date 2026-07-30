"""Durable SQLite paper account, orders, fills, and equity ledger."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.config import ExecutionSettings, InstrumentSettings
from mastermind_tick.models import Side, StrategySignal, Tick

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    display_symbol TEXT NOT NULL,
    venue TEXT NOT NULL,
    currency TEXT NOT NULL,
    initial_cash TEXT NOT NULL,
    cash TEXT NOT NULL,
    quantity TEXT NOT NULL DEFAULT '0',
    average_price TEXT NOT NULL DEFAULT '0',
    realized_pnl TEXT NOT NULL DEFAULT '0',
    total_fees TEXT NOT NULL DEFAULT '0',
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    signal_price TEXT NOT NULL,
    trailing_stop TEXT NOT NULL,
    atr TEXT NOT NULL,
    bar_start_ms INTEGER NOT NULL,
    submitted_tick_id TEXT NOT NULL,
    submitted_at_ms INTEGER NOT NULL,
    filled_tick_id TEXT,
    filled_at_ms INTEGER,
    fill_price TEXT,
    fill_quantity TEXT,
    fee TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    side TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    notional TEXT NOT NULL,
    fee TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    timestamp_ms INTEGER NOT NULL,
    price TEXT NOT NULL,
    cash TEXT NOT NULL,
    quantity TEXT NOT NULL,
    market_value TEXT NOT NULL,
    equity TEXT NOT NULL,
    unrealized_pnl TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    timestamp_ms INTEGER NOT NULL,
    level TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS strategy_states (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id),
    state_json TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_account_status ON orders(account_id, status);
CREATE INDEX IF NOT EXISTS idx_fills_account_time ON fills(account_id, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_equity_account_time ON equity_snapshots(account_id, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_events_account_time ON events(account_id, timestamp_ms DESC);
"""


class PaperStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def ensure_account(
        self, instrument: InstrumentSettings, initial_cash: float, now_ms: int
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO accounts (
                    id, symbol, display_symbol, venue, currency, initial_cash, cash,
                    quantity, average_price, realized_pnl, total_fees, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '0', '0', '0', '0', ?, ?)
                """,
                (
                    instrument.id,
                    instrument.symbol,
                    instrument.display_symbol,
                    instrument.venue,
                    instrument.currency,
                    str(initial_cash),
                    str(initial_cash),
                    now_ms,
                    now_ms,
                ),
            )

    def account(self, account_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
        if row is None:
            raise LookupError(f"unknown account: {account_id}")
        return dict(row)

    def accounts(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def has_pending_order(self, account_id: str) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM orders WHERE account_id = ? AND status = 'PENDING' LIMIT 1",
                (account_id,),
            ).fetchone()
        return row is not None

    def cancel_pending(self, account_id: str, now_ms: int, reason: str = "operator_pause") -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE orders SET status = 'CANCELED', filled_at_ms = ?
                WHERE account_id = ? AND status = 'PENDING'
                """,
                (now_ms, account_id),
            )
        if cursor.rowcount:
            self.add_event(
                account_id,
                now_ms,
                "WARN",
                "ORDER_CANCELED",
                f"Canceled {cursor.rowcount} pending order(s)",
                {"reason": reason},
            )
        return cursor.rowcount

    def submit_order(self, account_id: str, signal: StrategySignal, now_ms: int) -> str:
        order_id = uuid.uuid4().hex
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO orders (
                    id, account_id, side, status, reason, signal_price, trailing_stop, atr,
                    bar_start_ms, submitted_tick_id, submitted_at_ms
                ) VALUES (?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    account_id,
                    signal.side.value,
                    signal.reason,
                    str(signal.signal_price),
                    str(signal.trailing_stop),
                    str(signal.atr),
                    signal.bar_start_ms,
                    signal.tick_id,
                    now_ms,
                ),
            )
        self.add_event(
            account_id,
            now_ms,
            "INFO",
            "SIGNAL",
            f"{signal.side.value} signal at {signal.signal_price}",
            {"order_id": order_id, "reason": signal.reason},
        )
        return order_id

    def fill_pending(
        self,
        account_id: str,
        tick: Tick,
        instrument: InstrumentSettings,
        execution: ExecutionSettings,
        position_fraction: float,
    ) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            order = connection.execute(
                """
                SELECT * FROM orders
                WHERE account_id = ? AND status = 'PENDING' AND submitted_tick_id <> ?
                ORDER BY submitted_at_ms, id LIMIT 1
                """,
                (account_id, tick.event_id),
            ).fetchone()
            if order is None:
                return None
            account = connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if account is None:
                raise LookupError(account_id)

            side = Side(order["side"])
            market_price = tick.price
            fee_rate = Decimal(str(execution.fee_bps)) / Decimal("10000")
            slip_rate = Decimal(str(execution.slippage_bps)) / Decimal("10000")
            cash = Decimal(account["cash"])
            quantity = Decimal(account["quantity"])
            average_price = Decimal(account["average_price"])
            realized_pnl = Decimal(account["realized_pnl"])
            total_fees = Decimal(account["total_fees"])
            step = Decimal(str(instrument.quantity_step))

            if side is Side.BUY:
                fill_price = market_price * (Decimal("1") + slip_rate)
                budget = cash * Decimal(str(position_fraction))
                raw_quantity = budget / (fill_price * (Decimal("1") + fee_rate))
                fill_quantity = _floor_step(raw_quantity, step)
                notional = fill_price * fill_quantity
                fee = notional * fee_rate
                if notional < Decimal(str(execution.minimum_notional)) or fill_quantity <= 0:
                    connection.execute(
                        "UPDATE orders SET status = 'REJECTED' WHERE id = ?", (order["id"],)
                    )
                    return {"status": "REJECTED", "reason": "minimum_notional"}
                cash -= notional + fee
                quantity += fill_quantity
                average_price = fill_price
                realized_pnl -= fee
            else:
                fill_price = market_price * (Decimal("1") - slip_rate)
                fill_quantity = quantity
                notional = fill_price * fill_quantity
                fee = notional * fee_rate
                if fill_quantity <= 0:
                    connection.execute(
                        "UPDATE orders SET status = 'REJECTED' WHERE id = ?", (order["id"],)
                    )
                    return {"status": "REJECTED", "reason": "no_position"}
                cash += notional - fee
                realized_pnl += (fill_price - average_price) * fill_quantity - fee
                quantity = Decimal("0")
                average_price = Decimal("0")

            total_fees += fee
            fill_id = uuid.uuid4().hex
            connection.execute(
                """
                UPDATE accounts SET cash = ?, quantity = ?, average_price = ?, realized_pnl = ?,
                    total_fees = ?, updated_at_ms = ? WHERE id = ?
                """,
                (
                    str(cash),
                    str(quantity),
                    str(average_price),
                    str(realized_pnl),
                    str(total_fees),
                    tick.timestamp_ms,
                    account_id,
                ),
            )
            connection.execute(
                """
                UPDATE orders SET status = 'FILLED', filled_tick_id = ?, filled_at_ms = ?,
                    fill_price = ?, fill_quantity = ?, fee = ? WHERE id = ?
                """,
                (
                    tick.event_id,
                    tick.timestamp_ms,
                    str(fill_price),
                    str(fill_quantity),
                    str(fee),
                    order["id"],
                ),
            )
            connection.execute(
                """
                INSERT INTO fills (
                    id, order_id, account_id, side, timestamp_ms, price, quantity,
                    notional, fee, reason, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill_id,
                    order["id"],
                    account_id,
                    side.value,
                    tick.timestamp_ms,
                    str(fill_price),
                    str(fill_quantity),
                    str(notional),
                    str(fee),
                    order["reason"],
                    tick.source,
                ),
            )

        result = {
            "status": "FILLED",
            "fill_id": fill_id,
            "order_id": order["id"],
            "side": side.value,
            "price": str(fill_price),
            "quantity": str(fill_quantity),
            "fee": str(fee),
        }
        self.add_event(
            account_id,
            tick.timestamp_ms,
            "INFO",
            "FILL",
            f"{side.value} {fill_quantity} @ {fill_price:.4f}",
            result,
        )
        return result

    def snapshot(self, account_id: str, tick: Tick) -> dict[str, str | int]:
        account = self.account(account_id)
        cash = Decimal(account["cash"])
        quantity = Decimal(account["quantity"])
        average_price = Decimal(account["average_price"])
        realized_pnl = Decimal(account["realized_pnl"])
        market_value = quantity * tick.price
        equity = cash + market_value
        unrealized = quantity * (tick.price - average_price) if quantity else Decimal("0")
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO equity_snapshots (
                    account_id, timestamp_ms, price, cash, quantity, market_value,
                    equity, unrealized_pnl, realized_pnl, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    tick.timestamp_ms,
                    str(tick.price),
                    str(cash),
                    str(quantity),
                    str(market_value),
                    str(equity),
                    str(unrealized),
                    str(realized_pnl),
                    tick.source,
                ),
            )
        return {
            "timestamp_ms": tick.timestamp_ms,
            "price": str(tick.price),
            "cash": str(cash),
            "quantity": str(quantity),
            "market_value": str(market_value),
            "equity": str(equity),
            "unrealized_pnl": str(unrealized),
            "realized_pnl": str(realized_pnl),
        }

    def equity(self, account_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT timestamp_ms, price, cash, quantity, market_value, equity,
                           unrealized_pnl, realized_pnl, source
                    FROM equity_snapshots WHERE account_id = ?
                    ORDER BY timestamp_ms DESC, id DESC LIMIT ?
                ) ORDER BY timestamp_ms
                """,
                (account_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def fills(self, account_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM fills"
        params: tuple[Any, ...]
        if account_id:
            query += " WHERE account_id = ?"
            params = (account_id, limit)
        else:
            params = (limit,)
        query += " ORDER BY timestamp_ms DESC LIMIT ?"
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def orders(self, account_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM orders"
        params: tuple[Any, ...]
        if account_id:
            query += " WHERE account_id = ?"
            params = (account_id, limit)
        else:
            params = (limit,)
        query += " ORDER BY submitted_at_ms DESC LIMIT ?"
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def events(self, account_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM events"
        params: tuple[Any, ...]
        if account_id:
            query += " WHERE account_id = ?"
            params = (account_id, limit)
        else:
            params = (limit,)
        query += " ORDER BY timestamp_ms DESC, id DESC LIMIT ?"
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def add_event(
        self,
        account_id: str,
        timestamp_ms: int,
        level: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO events (
                    account_id, timestamp_ms, level, event_type, message, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    timestamp_ms,
                    level,
                    event_type,
                    message,
                    json.dumps(payload or {}, ensure_ascii=True),
                ),
            )

    def save_strategy_state(self, account_id: str, state: dict[str, Any], now_ms: int) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO strategy_states (account_id, state_json, updated_at_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (account_id, json.dumps(state, ensure_ascii=True), now_ms),
            )

    def strategy_state(self, account_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT state_json FROM strategy_states WHERE account_id = ?", (account_id,)
            ).fetchone()
        return json.loads(row["state_json"]) if row else None


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step
