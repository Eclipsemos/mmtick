"""Independent persistence for Binance Spot orders, fills, balances, and state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS live_orders (
    client_order_id TEXT PRIMARY KEY,
    exchange_order_id INTEGER,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    signal_price TEXT NOT NULL,
    requested_quantity TEXT,
    requested_quote_quantity TEXT,
    executed_quantity TEXT NOT NULL DEFAULT '0',
    cumulative_quote_quantity TEXT NOT NULL DEFAULT '0',
    signal_at_ms INTEGER NOT NULL,
    submitted_at_ms INTEGER,
    updated_at_ms INTEGER NOT NULL,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_live_orders_status
ON live_orders(account_id, status, updated_at_ms);

CREATE TABLE IF NOT EXISTS live_fills (
    trade_id INTEGER NOT NULL,
    order_id INTEGER NOT NULL,
    client_order_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    quote_quantity TEXT NOT NULL,
    commission TEXT NOT NULL,
    commission_asset TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY(symbol, trade_id)
);

CREATE INDEX IF NOT EXISTS idx_live_fills_account_time
ON live_fills(account_id, timestamp_ms);

CREATE TABLE IF NOT EXISTS live_balance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    base_free TEXT NOT NULL,
    base_locked TEXT NOT NULL,
    quote_free TEXT NOT NULL,
    quote_locked TEXT NOT NULL,
    reference_price TEXT,
    equity_quote TEXT,
    atr TEXT,
    trailing_stop TEXT,
    relation TEXT,
    source TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_live_balances_account_time
ON live_balance_snapshots(account_id, timestamp_ms);

CREATE TABLE IF NOT EXISTS live_strategy_state (
    account_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS live_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    level TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_live_events_account_time
ON live_events(account_id, timestamp_ms);

CREATE TABLE IF NOT EXISTS live_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
"""


class LiveStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            self._ensure_balance_columns(connection)

    @staticmethod
    def _ensure_balance_columns(connection: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(live_balance_snapshots)")
        }
        for name in ("atr", "trailing_stop", "relation"):
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE live_balance_snapshots ADD COLUMN {name} TEXT"
                )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def add_event(
        self,
        account_id: str,
        timestamp_ms: int,
        level: str,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO live_events (
                    account_id, timestamp_ms, level, code, message, details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    timestamp_ms,
                    level,
                    code,
                    message,
                    json.dumps(details, separators=(",", ":")) if details else None,
                ),
            )

    def events(self, account_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM live_events WHERE account_id = ?
                ORDER BY timestamp_ms DESC, id DESC LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        return [_row(row, json_fields=("details_json",)) for row in rows]

    def create_order(
        self,
        *,
        client_order_id: str,
        account_id: str,
        symbol: str,
        side: str,
        reason: str,
        signal_price: str,
        signal_at_ms: int,
        requested_quantity: str | None,
        requested_quote_quantity: str | None,
    ) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO live_orders (
                    client_order_id, account_id, symbol, side, status, reason,
                    signal_price, requested_quantity, requested_quote_quantity,
                    signal_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, 'CREATED', ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_order_id,
                    account_id,
                    symbol,
                    side,
                    reason,
                    signal_price,
                    requested_quantity,
                    requested_quote_quantity,
                    signal_at_ms,
                    signal_at_ms,
                ),
            )
            return cursor.rowcount == 1

    def update_order(
        self,
        client_order_id: str,
        *,
        status: str,
        updated_at_ms: int,
        payload: dict[str, Any] | None = None,
        submitted_at_ms: int | None = None,
    ) -> None:
        payload = payload or {}
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE live_orders SET
                    exchange_order_id = COALESCE(?, exchange_order_id),
                    status = ?,
                    executed_quantity = COALESCE(?, executed_quantity),
                    cumulative_quote_quantity = COALESCE(?, cumulative_quote_quantity),
                    submitted_at_ms = COALESCE(?, submitted_at_ms),
                    updated_at_ms = ?,
                    raw_json = COALESCE(?, raw_json)
                WHERE client_order_id = ?
                """,
                (
                    payload.get("orderId"),
                    status,
                    _optional_text(payload.get("executedQty")),
                    _optional_text(payload.get("cummulativeQuoteQty")),
                    submitted_at_ms,
                    updated_at_ms,
                    json.dumps(payload, separators=(",", ":")) if payload else None,
                    client_order_id,
                ),
            )

    def order(self, client_order_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM live_orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return _row(row, json_fields=("raw_json",)) if row else None

    def orders(self, account_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM live_orders WHERE account_id = ?
                ORDER BY signal_at_ms DESC LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        return [_row(row, json_fields=("raw_json",)) for row in rows]

    def pending_orders(self, account_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM live_orders
                WHERE account_id = ?
                  AND status IN ('CREATED', 'SUBMITTING', 'NEW', 'PARTIALLY_FILLED')
                ORDER BY signal_at_ms
                """,
                (account_id,),
            ).fetchall()
        return [_row(row, json_fields=("raw_json",)) for row in rows]

    def order_count_since(self, account_id: str, timestamp_ms: int) -> int:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM live_orders
                WHERE account_id = ? AND submitted_at_ms >= ?
                """,
                (account_id, timestamp_ms),
            ).fetchone()
        return int(row["count"])

    def fill_count(self, account_id: str) -> int:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM live_fills WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return int(row["count"])

    def upsert_fill(
        self,
        *,
        account_id: str,
        symbol: str,
        side: str,
        client_order_id: str,
        payload: dict[str, Any],
    ) -> bool:
        quantity = str(payload["qty"])
        price = str(payload["price"])
        quote_quantity = str(payload.get("quoteQty") or float(quantity) * float(price))
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO live_fills (
                    trade_id, order_id, client_order_id, account_id, symbol, side,
                    timestamp_ms, price, quantity, quote_quantity, commission,
                    commission_asset, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(payload["id"]),
                    int(payload["orderId"]),
                    client_order_id,
                    account_id,
                    symbol,
                    side,
                    int(payload["time"]),
                    price,
                    quantity,
                    quote_quantity,
                    str(payload.get("commission", "0")),
                    str(payload.get("commissionAsset", "")),
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
            return cursor.rowcount == 1

    def fills(self, account_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM live_fills WHERE account_id = ?
                ORDER BY timestamp_ms DESC, trade_id DESC LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        return [_row(row, json_fields=("raw_json",)) for row in rows]

    def save_balance_snapshot(
        self,
        *,
        account_id: str,
        timestamp_ms: int,
        base_free: str,
        base_locked: str,
        quote_free: str,
        quote_locked: str,
        reference_price: str | None,
        equity_quote: str | None,
        atr: str | None = None,
        trailing_stop: str | None = None,
        relation: str | None = None,
        source: str = "binance_spot_account",
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO live_balance_snapshots (
                    account_id, timestamp_ms, base_free, base_locked, quote_free,
                    quote_locked, reference_price, equity_quote, atr,
                    trailing_stop, relation, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    timestamp_ms,
                    base_free,
                    base_locked,
                    quote_free,
                    quote_locked,
                    reference_price,
                    equity_quote,
                    atr,
                    trailing_stop,
                    relation,
                    source,
                ),
            )

    def latest_balance(self, account_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM live_balance_snapshots WHERE account_id = ?
                ORDER BY timestamp_ms DESC, id DESC LIMIT 1
                """,
                (account_id,),
            ).fetchone()
        return dict(row) if row else None

    def balance_snapshots(
        self,
        account_id: str,
        limit: int = 1000,
        before_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        before_clause = "AND timestamp_ms < ?" if before_ms is not None else ""
        params = (
            (account_id, before_ms, limit)
            if before_ms is not None
            else (account_id, limit)
        )
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM (
                    SELECT * FROM live_balance_snapshots
                    WHERE account_id = ? {before_clause}
                    ORDER BY timestamp_ms DESC, id DESC LIMIT ?
                ) ORDER BY timestamp_ms, id
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def first_balance(self, account_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM live_balance_snapshots WHERE account_id = ?
                ORDER BY timestamp_ms, id LIMIT 1
                """,
                (account_id,),
            ).fetchone()
        return dict(row) if row else None

    def balance_at_boundaries(
        self,
        account_id: str,
        boundaries_ms: list[int],
    ) -> dict[int, dict[str, Any] | None]:
        result: dict[int, dict[str, Any] | None] = {}
        with self.connection() as connection:
            for boundary_ms in sorted(set(boundaries_ms)):
                row = connection.execute(
                    """
                    SELECT timestamp_ms, equity_quote AS equity
                    FROM live_balance_snapshots
                    WHERE account_id = ? AND timestamp_ms < ?
                    ORDER BY timestamp_ms DESC, id DESC LIMIT 1
                    """,
                    (account_id, boundary_ms),
                ).fetchone()
                result[boundary_ms] = dict(row) if row else None
        return result

    def day_start_equity(self, account_id: str, timestamp_ms: int) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT equity_quote FROM live_balance_snapshots
                WHERE account_id = ? AND timestamp_ms >= ? AND equity_quote IS NOT NULL
                ORDER BY timestamp_ms, id LIMIT 1
                """,
                (account_id, timestamp_ms),
            ).fetchone()
        return str(row["equity_quote"]) if row else None

    def strategy_state(self, account_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT state_json FROM live_strategy_state WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def save_strategy_state(
        self, account_id: str, state: dict[str, Any], updated_at_ms: int
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO live_strategy_state (account_id, state_json, updated_at_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (account_id, json.dumps(state, separators=(",", ":")), updated_at_ms),
            )

    def metadata(self, key: str) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT value FROM live_metadata WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_metadata(self, key: str, value: str, updated_at_ms: int) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO live_metadata (key, value, updated_at_ms) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at_ms = excluded.updated_at_ms
                """,
                (key, value, updated_at_ms),
            )


def _row(row: sqlite3.Row, *, json_fields: tuple[str, ...]) -> dict[str, Any]:
    value = dict(row)
    for field in json_fields:
        if value.get(field):
            value[field] = json.loads(value[field])
    return value


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)
