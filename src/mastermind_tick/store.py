"""Durable SQLite paper account, orders, fills, and equity ledger."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.config import ExecutionSettings, InstrumentSettings
from mastermind_tick.models import Bar, FundingRate, Side, StrategySignal, Tick

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
    total_funding TEXT NOT NULL DEFAULT '0',
    paper_model TEXT NOT NULL DEFAULT 'spot',
    leverage INTEGER NOT NULL DEFAULT 1,
    margin_mode TEXT NOT NULL DEFAULT 'cash',
    position_fraction TEXT NOT NULL DEFAULT '1',
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
    atr TEXT,
    trailing_stop TEXT,
    relation TEXT,
    mark_price TEXT,
    index_price TEXT,
    funding_rate TEXT,
    initial_margin TEXT NOT NULL DEFAULT '0',
    available_balance TEXT NOT NULL DEFAULT '0',
    total_funding TEXT NOT NULL DEFAULT '0',
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

CREATE TABLE IF NOT EXISTS funding_payments (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    symbol TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    rate TEXT NOT NULL,
    mark_price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    notional TEXT NOT NULL,
    amount TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    UNIQUE(account_id, timestamp_ms)
);

CREATE TABLE IF NOT EXISTS agg_trades (
    event_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    aggregate_trade_id INTEGER,
    first_trade_id INTEGER,
    last_trade_id INTEGER,
    event_time_ms INTEGER,
    timestamp_ms INTEGER NOT NULL,
    price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    notional TEXT NOT NULL,
    buyer_is_maker INTEGER,
    source TEXT NOT NULL,
    received_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ohlcv_bars (
    instrument_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume TEXT NOT NULL,
    trade_count INTEGER NOT NULL DEFAULT 0,
    is_closed INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, interval_minutes, start_ms)
);

CREATE INDEX IF NOT EXISTS idx_orders_account_status ON orders(account_id, status);
CREATE INDEX IF NOT EXISTS idx_fills_account_time ON fills(account_id, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_equity_account_time ON equity_snapshots(account_id, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_events_account_time ON events(account_id, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_funding_account_time
    ON funding_payments(account_id, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_agg_trades_instrument_time
    ON agg_trades(instrument_id, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_instrument_time
    ON ohlcv_bars(instrument_id, interval_minutes, start_ms DESC);
"""

WAREHOUSE_TABLES = (
    "accounts",
    "orders",
    "fills",
    "equity_snapshots",
    "events",
    "strategy_states",
    "funding_payments",
    "ohlcv_bars",
    "agg_trades",
)


class PaperStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        snapshot_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(equity_snapshots)").fetchall()
        }
        snapshot_text_columns = (
            "atr",
            "trailing_stop",
            "relation",
            "mark_price",
            "index_price",
            "funding_rate",
        )
        for name in snapshot_text_columns:
            if name not in snapshot_columns:
                connection.execute(f"ALTER TABLE equity_snapshots ADD COLUMN {name} TEXT")
        for name in ("initial_margin", "available_balance", "total_funding"):
            if name not in snapshot_columns:
                connection.execute(
                    f"ALTER TABLE equity_snapshots ADD COLUMN {name} TEXT NOT NULL DEFAULT '0'"
                )

        account_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(accounts)").fetchall()
        }
        account_migrations = {
            "total_funding": "TEXT NOT NULL DEFAULT '0'",
            "paper_model": "TEXT NOT NULL DEFAULT 'spot'",
            "leverage": "INTEGER NOT NULL DEFAULT 1",
            "margin_mode": "TEXT NOT NULL DEFAULT 'cash'",
            "position_fraction": "TEXT NOT NULL DEFAULT '1'",
        }
        for name, definition in account_migrations.items():
            if name not in account_columns:
                connection.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")

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
                    quantity, average_price, realized_pnl, total_fees, total_funding,
                    paper_model, leverage, margin_mode, position_fraction,
                    created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '0', '0', '0', '0', '0', ?, ?, ?, ?, ?, ?)
                """,
                (
                    instrument.id,
                    instrument.symbol,
                    instrument.display_symbol,
                    instrument.venue,
                    instrument.currency,
                    str(initial_cash),
                    str(initial_cash),
                    instrument.paper_model,
                    instrument.leverage,
                    instrument.margin_mode,
                    str(instrument.position_fraction or 1),
                    now_ms,
                    now_ms,
                ),
            )
            connection.execute(
                """
                UPDATE accounts SET symbol = ?, display_symbol = ?, venue = ?, currency = ?,
                    paper_model = ?, leverage = ?, margin_mode = ?, position_fraction = ?
                WHERE id = ?
                """,
                (
                    instrument.symbol,
                    instrument.display_symbol,
                    instrument.venue,
                    instrument.currency,
                    instrument.paper_model,
                    instrument.leverage,
                    instrument.margin_mode,
                    str(instrument.position_fraction or 1),
                    instrument.id,
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

    def upsert_history_bars(
        self,
        instrument: InstrumentSettings,
        interval_minutes: int,
        bars: list[Bar],
        source: str,
    ) -> None:
        if not bars:
            return
        now_ms = int(time.time() * 1000)
        values = [
            (
                instrument.id,
                instrument.symbol,
                interval_minutes,
                bar.start_ms,
                bar.end_ms,
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
                bar.trade_count,
                source,
                now_ms,
            )
            for bar in bars
        ]
        with self.connection() as connection:
            connection.executemany(
                """
                INSERT INTO ohlcv_bars (
                    instrument_id, symbol, interval_minutes, start_ms, end_ms,
                    open, high, low, close, volume, trade_count, is_closed,
                    source, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(instrument_id, interval_minutes, start_ms) DO UPDATE SET
                    symbol = excluded.symbol,
                    end_ms = excluded.end_ms,
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    trade_count = excluded.trade_count,
                    is_closed = 1,
                    source = excluded.source,
                    updated_at_ms = excluded.updated_at_ms
                """,
                values,
            )

    def record_market_tick(
        self,
        instrument: InstrumentSettings,
        interval_minutes: int,
        tick: Tick,
    ) -> bool:
        interval_ms = interval_minutes * 60_000
        bar_start_ms = tick.timestamp_ms // interval_ms * interval_ms
        bar_end_ms = bar_start_ms + interval_ms - 1
        received_at_ms = int(time.time() * 1000)
        raw_trade_count = (
            tick.last_trade_id - tick.first_trade_id + 1
            if tick.first_trade_id is not None and tick.last_trade_id is not None
            else 1
        )
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO agg_trades (
                    event_id, instrument_id, symbol, aggregate_trade_id,
                    first_trade_id, last_trade_id, event_time_ms, timestamp_ms,
                    price, quantity, notional, buyer_is_maker, source, received_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tick.event_id,
                    instrument.id,
                    instrument.symbol,
                    tick.aggregate_trade_id,
                    tick.first_trade_id,
                    tick.last_trade_id,
                    tick.event_time_ms,
                    tick.timestamp_ms,
                    str(tick.price),
                    str(tick.quantity),
                    str(tick.notional if tick.notional is not None else tick.price * tick.quantity),
                    None if tick.buyer_is_maker is None else int(tick.buyer_is_maker),
                    tick.source,
                    received_at_ms,
                ),
            ).rowcount
            if not inserted:
                return False

            connection.execute(
                """
                UPDATE ohlcv_bars SET is_closed = 1, updated_at_ms = ?
                WHERE instrument_id = ? AND interval_minutes = ?
                  AND start_ms < ? AND is_closed = 0
                """,
                (received_at_ms, instrument.id, interval_minutes, bar_start_ms),
            )
            row = connection.execute(
                """
                SELECT high, low, volume, trade_count FROM ohlcv_bars
                WHERE instrument_id = ? AND interval_minutes = ? AND start_ms = ?
                """,
                (instrument.id, interval_minutes, bar_start_ms),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO ohlcv_bars (
                        instrument_id, symbol, interval_minutes, start_ms, end_ms,
                        open, high, low, close, volume, trade_count, is_closed,
                        source, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        instrument.id,
                        instrument.symbol,
                        interval_minutes,
                        bar_start_ms,
                        bar_end_ms,
                        str(tick.open_price or tick.price),
                        str(tick.high_price or tick.price),
                        str(tick.low_price or tick.price),
                        str(tick.price),
                        str(tick.quantity),
                        raw_trade_count,
                        tick.source,
                        received_at_ms,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE ohlcv_bars SET high = ?, low = ?, close = ?, volume = ?,
                        trade_count = ?, source = ?, updated_at_ms = ?
                    WHERE instrument_id = ? AND interval_minutes = ? AND start_ms = ?
                    """,
                    (
                        str(max(Decimal(row["high"]), tick.high_price or tick.price)),
                        str(min(Decimal(row["low"]), tick.low_price or tick.price)),
                        str(tick.price),
                        str(Decimal(row["volume"]) + tick.quantity),
                        int(row["trade_count"]) + raw_trade_count,
                        tick.source,
                        received_at_ms,
                        instrument.id,
                        interval_minutes,
                        bar_start_ms,
                    ),
                )
        return True

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
        *,
        allow_same_tick: bool = False,
    ) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            order = connection.execute(
                """
                SELECT * FROM orders
                WHERE account_id = ? AND status = 'PENDING'
                    AND (? = 1 OR submitted_tick_id <> ?)
                ORDER BY submitted_at_ms, id LIMIT 1
                """,
                (account_id, int(allow_same_tick), tick.event_id),
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
            fee_bps = instrument.fee_bps if instrument.fee_bps is not None else execution.fee_bps
            slippage_bps = (
                instrument.slippage_bps
                if instrument.slippage_bps is not None
                else execution.slippage_bps
            )
            minimum_notional = (
                instrument.minimum_notional
                if instrument.minimum_notional is not None
                else execution.minimum_notional
            )
            fee_rate = Decimal(str(fee_bps)) / Decimal("10000")
            slip_rate = Decimal(str(slippage_bps)) / Decimal("10000")
            cash = Decimal(account["cash"])
            quantity = Decimal(account["quantity"])
            average_price = Decimal(account["average_price"])
            realized_pnl = Decimal(account["realized_pnl"])
            total_fees = Decimal(account["total_fees"])
            step = Decimal(str(instrument.quantity_step))
            is_futures = account["paper_model"] == "futures"
            leverage = Decimal(int(account["leverage"]))

            if side is Side.BUY:
                fill_price = market_price * (Decimal("1") + slip_rate)
                budget = cash * Decimal(str(position_fraction))
                raw_quantity = (
                    budget / fill_price
                    if is_futures
                    else budget / (fill_price * (Decimal("1") + fee_rate))
                )
                fill_quantity = _floor_step(raw_quantity, step)
                notional = fill_price * fill_quantity
                fee = notional * fee_rate
                required_balance = notional / leverage + fee if is_futures else notional + fee
                if (
                    notional < Decimal(str(minimum_notional))
                    or fill_quantity <= 0
                    or required_balance > cash
                ):
                    connection.execute(
                        "UPDATE orders SET status = 'REJECTED' WHERE id = ?", (order["id"],)
                    )
                    return {"status": "REJECTED", "reason": "insufficient_margin"}
                cash -= fee if is_futures else notional + fee
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
                cash += (
                    (fill_price - average_price) * fill_quantity - fee
                    if is_futures
                    else notional - fee
                )
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

    def apply_funding(
        self,
        account_id: str,
        funding: FundingRate,
        *,
        source: str = "binance_futures_funding",
    ) -> dict[str, str | int] | None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if account is None:
                raise LookupError(account_id)
            if account["paper_model"] != "futures":
                return None

            quantity = Decimal(account["quantity"])
            if quantity <= 0:
                return None
            notional = quantity * funding.mark_price
            amount = -(notional * funding.rate)
            payment_id = f"{account_id}:{funding.timestamp_ms}"
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO funding_payments (
                    id, account_id, symbol, timestamp_ms, rate, mark_price,
                    quantity, notional, amount, source, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment_id,
                    account_id,
                    account["symbol"],
                    funding.timestamp_ms,
                    str(funding.rate),
                    str(funding.mark_price),
                    str(quantity),
                    str(notional),
                    str(amount),
                    source,
                    int(time.time() * 1000),
                ),
            ).rowcount
            if not inserted:
                return None

            connection.execute(
                """
                UPDATE accounts SET cash = ?, realized_pnl = ?, total_funding = ?,
                    updated_at_ms = ? WHERE id = ?
                """,
                (
                    str(Decimal(account["cash"]) + amount),
                    str(Decimal(account["realized_pnl"]) + amount),
                    str(Decimal(account["total_funding"]) + amount),
                    funding.timestamp_ms,
                    account_id,
                ),
            )

        result: dict[str, str | int] = {
            "timestamp_ms": funding.timestamp_ms,
            "rate": str(funding.rate),
            "mark_price": str(funding.mark_price),
            "quantity": str(quantity),
            "notional": str(notional),
            "amount": str(amount),
        }
        direction = "received" if amount >= 0 else "paid"
        self.add_event(
            account_id,
            funding.timestamp_ms,
            "INFO",
            "FUNDING",
            f"Funding {direction}: {amount:.4f} USDT at {funding.rate}",
            result,
        )
        return result

    def latest_funding_time(self, account_id: str) -> int | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT MAX(timestamp_ms) AS timestamp_ms
                FROM funding_payments WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
        return int(row["timestamp_ms"]) if row and row["timestamp_ms"] is not None else None

    def funding_payments(
        self, account_id: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        query = """
            SELECT id, account_id, symbol, timestamp_ms, rate, mark_price,
                   quantity, notional, amount, source, created_at_ms
            FROM funding_payments
        """
        params: tuple[Any, ...]
        if account_id:
            query += " WHERE account_id = ?"
            params = (account_id, limit)
        else:
            params = (limit,)
        query += " ORDER BY timestamp_ms DESC, id DESC LIMIT ?"
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def snapshot(
        self,
        account_id: str,
        tick: Tick,
        strategy: dict[str, Any] | None = None,
    ) -> dict[str, str | int]:
        account = self.account(account_id)
        cash = Decimal(account["cash"])
        quantity = Decimal(account["quantity"])
        average_price = Decimal(account["average_price"])
        realized_pnl = Decimal(account["realized_pnl"])
        total_funding = Decimal(account["total_funding"])
        is_futures = account["paper_model"] == "futures"
        mark_price = tick.mark_price or tick.price
        market_value = quantity * (mark_price if is_futures else tick.price)
        unrealized = quantity * (mark_price - average_price) if quantity else Decimal("0")
        if is_futures:
            equity = cash + unrealized
            initial_margin = market_value / Decimal(int(account["leverage"]))
            available_balance = equity - initial_margin
        else:
            equity = cash + market_value
            initial_margin = Decimal("0")
            available_balance = cash
        strategy = strategy or {}
        atr = strategy.get("atr")
        trailing_stop = strategy.get("trailing_stop")
        relation = strategy.get("relation")
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO equity_snapshots (
                    account_id, timestamp_ms, price, cash, quantity, market_value,
                    equity, unrealized_pnl, realized_pnl, atr, trailing_stop, relation,
                    mark_price, index_price, funding_rate, initial_margin,
                    available_balance, total_funding, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    str(atr) if atr is not None else None,
                    str(trailing_stop) if trailing_stop is not None else None,
                    relation,
                    str(mark_price) if is_futures else None,
                    str(tick.index_price) if tick.index_price is not None else None,
                    str(tick.funding_rate) if tick.funding_rate is not None else None,
                    str(initial_margin),
                    str(available_balance),
                    str(total_funding),
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
            "mark_price": str(mark_price) if is_futures else None,
            "index_price": str(tick.index_price) if tick.index_price is not None else None,
            "funding_rate": str(tick.funding_rate) if tick.funding_rate is not None else None,
            "initial_margin": str(initial_margin),
            "available_balance": str(available_balance),
            "total_funding": str(total_funding),
        }

    def equity(self, account_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT timestamp_ms, price, cash, quantity, market_value, equity,
                           unrealized_pnl, realized_pnl, atr, trailing_stop, relation,
                           mark_price, index_price, funding_rate, initial_margin,
                           available_balance, total_funding, source
                    FROM equity_snapshots WHERE account_id = ?
                    ORDER BY timestamp_ms DESC, id DESC LIMIT ?
                ) ORDER BY timestamp_ms
                """,
                (account_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def agg_trades(self, instrument_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT event_id, instrument_id, symbol, aggregate_trade_id,
                       first_trade_id, last_trade_id, event_time_ms, timestamp_ms,
                       price, quantity, notional, buyer_is_maker, source, received_at_ms
                FROM agg_trades WHERE instrument_id = ?
                ORDER BY timestamp_ms DESC, event_id DESC LIMIT ?
                """,
                (instrument_id, limit),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            if item["buyer_is_maker"] is not None:
                item["buyer_is_maker"] = bool(item["buyer_is_maker"])
        return result

    def ohlcv_bars(
        self,
        instrument_id: str,
        interval_minutes: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT instrument_id, symbol, interval_minutes, start_ms, end_ms,
                       open, high, low, close, volume, trade_count, is_closed,
                       source, updated_at_ms
                FROM ohlcv_bars
                WHERE instrument_id = ? AND interval_minutes = ?
                ORDER BY start_ms DESC LIMIT ?
                """,
                (instrument_id, interval_minutes, limit),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["is_closed"] = bool(item["is_closed"])
        return result

    def warehouse_summary(
        self,
        instruments: tuple[InstrumentSettings, ...],
        interval_minutes: int,
    ) -> dict[str, Any]:
        with self.connection() as connection:
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            try:
                object_sizes = {
                    row["name"]: int(row["size_bytes"] or 0)
                    for row in connection.execute(
                        "SELECT name, SUM(pgsize) AS size_bytes FROM dbstat GROUP BY name"
                    ).fetchall()
                }
            except sqlite3.OperationalError:
                object_sizes = {}

            tables = []
            for table in WAREHOUSE_TABLES:
                row_count = int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                index_names = [
                    row["name"]
                    for row in connection.execute(f"PRAGMA index_list({table})").fetchall()
                ]
                size_bytes = object_sizes.get(table, 0) + sum(
                    object_sizes.get(name, 0) for name in index_names
                )
                tables.append(
                    {
                        "name": table,
                        "row_count": row_count,
                        "size_bytes": size_bytes,
                        "average_row_bytes": size_bytes / row_count if row_count else 0,
                    }
                )

            ingestion = []
            for instrument in instruments:
                trades = connection.execute(
                    """
                    SELECT COUNT(*) AS row_count,
                           MIN(timestamp_ms) AS first_timestamp_ms,
                           MAX(timestamp_ms) AS last_timestamp_ms,
                           COALESCE(SUM(CAST(quantity AS REAL)), 0) AS total_quantity,
                           COALESCE(SUM(CAST(notional AS REAL)), 0) AS total_notional,
                           COALESCE(SUM(
                               CASE
                                   WHEN first_trade_id IS NOT NULL AND last_trade_id IS NOT NULL
                                   THEN last_trade_id - first_trade_id + 1
                                   ELSE 1
                               END
                           ), 0) AS raw_trade_count
                    FROM agg_trades WHERE instrument_id = ?
                    """,
                    (instrument.id,),
                ).fetchone()
                bars = connection.execute(
                    """
                    SELECT COUNT(*) AS row_count,
                           COALESCE(SUM(is_closed), 0) AS closed_count,
                           MIN(start_ms) AS first_start_ms,
                           MAX(start_ms) AS last_start_ms,
                           MAX(updated_at_ms) AS last_updated_at_ms
                    FROM ohlcv_bars
                    WHERE instrument_id = ? AND interval_minutes = ?
                    """,
                    (instrument.id, interval_minutes),
                ).fetchone()
                ingestion.append(
                    {
                        "instrument_id": instrument.id,
                        "symbol": instrument.symbol,
                        "agg_trades": dict(trades),
                        "ohlcv": {
                            **dict(bars),
                            "interval_minutes": interval_minutes,
                            "open_count": int(bars["row_count"] or 0)
                            - int(bars["closed_count"] or 0),
                        },
                    }
                )

        main_bytes = _file_size(self.path)
        wal_bytes = _file_size(Path(f"{self.path}-wal"))
        shm_bytes = _file_size(Path(f"{self.path}-shm"))
        return {
            "generated_at_ms": int(time.time() * 1000),
            "database": {
                "path": str(self.path),
                "main_bytes": main_bytes,
                "wal_bytes": wal_bytes,
                "shm_bytes": shm_bytes,
                "total_bytes": main_bytes + wal_bytes + shm_bytes,
                "page_size": page_size,
            },
            "tables": tables,
            "instruments": ingestion,
        }

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


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0
