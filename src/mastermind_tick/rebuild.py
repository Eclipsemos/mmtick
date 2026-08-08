"""Rebuild paper account ledgers from the persisted market warehouse."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.backtest import ReplayATRTickStrategy, _load_funding_rates, _load_warmup_bars
from mastermind_tick.config import (
    InstrumentSettings,
    Settings,
    instrument_strategy,
    load_settings,
)
from mastermind_tick.models import Tick
from mastermind_tick.store import PaperStore
from mastermind_tick.strategy import ATRProfitProtection, atr_profit_protection_signal

DERIVED_TABLES = (
    "fills",
    "orders",
    "equity_snapshots",
    "events",
    "funding_payments",
    "strategy_states",
)
REPLAY_EVENT_TYPES = ("SIGNAL", "FILL", "FUNDING")


@dataclass(frozen=True)
class AccountRebuildResult:
    account_id: str
    first_tick_ms: int
    last_tick_ms: int
    tick_count: int
    warmup_bars: int
    funding_rates: int
    orders: int
    fills: int
    snapshots: int
    pending_orders: int
    ending_position: str
    final_equity: str
    net_return: str
    total_fees: str
    total_funding: str


def backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    for suffix in ("-wal", "-shm"):
        Path(f"{destination}{suffix}").unlink(missing_ok=True)
    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(destination) as destination_connection,
    ):
        source_connection.backup(destination_connection)


def rebuild_candidate(
    settings: Settings,
    candidate_path: Path,
    account_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Create and fully replay a candidate DB without modifying the source DB."""
    backup_database(settings.database_path, candidate_path)
    with sqlite3.connect(candidate_path) as connection:
        connection.execute("PRAGMA journal_mode=OFF")
    store = PaperStore(candidate_path, durable=False)
    before_market = _market_counts(candidate_path)
    selected = _selected_instruments(settings, account_ids)
    selected_strategy = instrument_strategy(settings, selected[0])
    results = [_rebuild_account(settings, store, instrument) for instrument in selected]
    after_market = _market_counts(candidate_path)
    if after_market != before_market:
        raise RuntimeError("market warehouse changed while rebuilding candidate")
    return {
        "candidate_path": str(candidate_path),
        "strategy": {
            "algorithm_version": ReplayATRTickStrategy.ALGORITHM_VERSION,
            "name": selected_strategy.name,
            "period": selected_strategy.atr_period,
            "multiplier": selected_strategy.atr_multiplier,
            "bar_ms": selected_strategy.bar_minutes * 60_000,
            "trend_efficiency_period": selected_strategy.trend_efficiency_period,
            "minimum_trend_efficiency": selected_strategy.minimum_trend_efficiency,
            "reversal_confirmation_atr": selected_strategy.reversal_confirmation_atr,
            "profit_activation_atr": selected[0].profit_activation_atr,
            "profit_trailing_atr": selected[0].profit_trailing_atr,
        },
        "market_counts": after_market,
        "accounts": [asdict(result) for result in results],
    }


def _rebuild_account(
    settings: Settings, store: PaperStore, instrument: InstrumentSettings
) -> AccountRebuildResult:
    market_id = instrument.market_id
    strategy_settings = instrument_strategy(settings, instrument)
    with store.connection() as connection:
        bounds = connection.execute(
            """
            SELECT MIN(timestamp_ms) AS first_ms, MAX(timestamp_ms) AS last_ms,
                   COUNT(*) AS tick_count
            FROM agg_trades WHERE instrument_id = ?
            """,
            (market_id,),
        ).fetchone()
        if bounds is None or bounds["first_ms"] is None:
            raise ValueError(f"no persisted aggTrade data for {instrument.id}")
        first_ms = int(bounds["first_ms"])
        last_ms = int(bounds["last_ms"])
        tick_count = int(bounds["tick_count"])
        warmup = _load_warmup_bars(connection, market_id, first_ms, settings.warmup_bars)
        funding_rates = _load_funding_rates(connection, market_id, first_ms, last_ms)

    if len(warmup) < strategy_settings.atr_period:
        raise ValueError(
            f"insufficient warmup for {instrument.id}: "
            f"{len(warmup)} < {strategy_settings.atr_period}"
        )

    _clear_account_ledger(store, instrument, settings.initial_cash, first_ms)
    strategy = ReplayATRTickStrategy(
        strategy_settings.atr_period,
        strategy_settings.atr_multiplier,
        strategy_settings.bar_minutes,
        strategy_settings.trend_efficiency_period,
        strategy_settings.minimum_trend_efficiency,
        strategy_settings.reversal_confirmation_atr,
    )
    strategy.bootstrap(warmup)
    profit_protection = _paper_profit_protection(instrument)
    position_fraction = strategy_settings.position_fraction
    funding_index = 0
    last_snapshot_ms = 0
    last_tick: Tick | None = None
    position_quantity = Decimal("0")
    average_price = Decimal("0")
    has_pending = False

    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT event_id, timestamp_ms, price, open_price, high_price, low_price,
                   quantity, source,
                   aggregate_trade_id, first_trade_id, last_trade_id,
                   buyer_is_maker, event_time_ms, notional
            FROM agg_trades WHERE instrument_id = ?
            ORDER BY timestamp_ms, received_at_ms, event_id
            """,
            (market_id,),
        )
        for row in rows:
            tick = Tick(
                event_id=row["event_id"],
                timestamp_ms=int(row["timestamp_ms"]),
                price=Decimal(row["price"]),
                quantity=Decimal(row["quantity"]),
                source=row["source"],
                aggregate_trade_id=row["aggregate_trade_id"],
                first_trade_id=row["first_trade_id"],
                last_trade_id=row["last_trade_id"],
                buyer_is_maker=(
                    bool(row["buyer_is_maker"]) if row["buyer_is_maker"] is not None else None
                ),
                event_time_ms=row["event_time_ms"],
                open_price=(
                    Decimal(row["open_price"]) if row["open_price"] is not None else None
                ),
                high_price=(
                    Decimal(row["high_price"]) if row["high_price"] is not None else None
                ),
                low_price=(
                    Decimal(row["low_price"]) if row["low_price"] is not None else None
                ),
                notional=Decimal(row["notional"]),
            )
            funding_applied = False
            while (
                funding_index < len(funding_rates)
                and funding_rates[funding_index].timestamp_ms <= tick.timestamp_ms
            ):
                payment = store.apply_funding(
                    instrument.id,
                    funding_rates[funding_index],
                    market_data_id=market_id,
                )
                funding_applied = payment is not None or funding_applied
                funding_index += 1

            fill = None
            if has_pending:
                fill = store.fill_pending(
                    instrument.id,
                    tick,
                    instrument,
                    settings.execution,
                    position_fraction,
                )
                if fill is not None:
                    strategy.on_fill(
                        tick.timestamp_ms,
                        filled=fill.get("status") == "FILLED",
                    )
                has_pending = False
                account = store.account(instrument.id)
                position_quantity = Decimal(account["quantity"])
                average_price = Decimal(account["average_price"])
            signal = strategy.on_tick(
                tick,
                has_position=position_quantity != 0,
                has_pending_order=has_pending,
                allow_short=instrument.short_enabled,
                is_short=position_quantity < 0,
            )
            if signal is None:
                signal = atr_profit_protection_signal(
                    profit_protection,
                    strategy,
                    tick,
                    position_quantity=position_quantity,
                    entry_price=average_price,
                    has_pending_order=has_pending,
                    emit_signals=True,
                )
            if signal is not None:
                store.submit_order(instrument.id, signal, tick.timestamp_ms)
                has_pending = True

            snapshot_due = (
                tick.timestamp_ms - last_snapshot_ms >= settings.equity_snapshot_seconds * 1000
            )
            if snapshot_due or fill or signal or funding_applied:
                store.snapshot(
                    instrument.id,
                    tick,
                    _strategy_view(strategy, profit_protection),
                )
                last_snapshot_ms = tick.timestamp_ms
            last_tick = tick

    if last_tick is None:
        raise RuntimeError(f"replay unexpectedly produced no ticks for {instrument.id}")
    store.save_strategy_state(
        instrument.id,
        _strategy_state(strategy, profit_protection),
        last_tick.timestamp_ms,
    )
    account = store.account(instrument.id)
    final_snapshot = store.snapshot(
        instrument.id,
        last_tick,
        _strategy_view(strategy, profit_protection),
    )
    initial_cash = Decimal(account["initial_cash"])
    final_equity = Decimal(str(final_snapshot["equity"]))
    counts = _ledger_counts(store, instrument.id)
    quantity = Decimal(account["quantity"])
    ending_position = "SHORT" if quantity < 0 else "LONG" if quantity > 0 else "FLAT"
    return AccountRebuildResult(
        account_id=instrument.id,
        first_tick_ms=first_ms,
        last_tick_ms=last_ms,
        tick_count=tick_count,
        warmup_bars=len(warmup),
        funding_rates=len(funding_rates),
        orders=counts["orders"],
        fills=counts["fills"],
        snapshots=counts["equity_snapshots"],
        pending_orders=counts["pending_orders"],
        ending_position=ending_position,
        final_equity=str(final_equity),
        net_return=str(final_equity / initial_cash - Decimal("1")),
        total_fees=account["total_fees"],
        total_funding=account["total_funding"],
    )


def _clear_account_ledger(
    store: PaperStore,
    instrument: InstrumentSettings,
    initial_cash: float,
    first_tick_ms: int,
) -> None:
    store.ensure_account(instrument, initial_cash, first_tick_ms)
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for table in DERIVED_TABLES:
            connection.execute(f"DELETE FROM {table} WHERE account_id = ?", (instrument.id,))
        connection.execute(
            """
            UPDATE accounts SET initial_cash = ?, cash = ?, quantity = '0',
                average_price = '0', realized_pnl = '0', total_fees = '0',
                total_funding = '0', created_at_ms = ?, updated_at_ms = ?
            WHERE id = ?
            """,
            (str(initial_cash), str(initial_cash), first_tick_ms, first_tick_ms, instrument.id),
        )


def apply_candidate(
    production_path: Path,
    candidate_path: Path,
    account_ids: tuple[str, ...],
) -> None:
    """Atomically replace ledgers while allowing verified append-only market growth."""
    with sqlite3.connect(production_path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("ATTACH DATABASE ? AS candidate", (str(candidate_path),))
        try:
            connection.execute("BEGIN IMMEDIATE")
            for table in ("agg_trades", "ohlcv_bars", "funding_rates"):
                production_count = int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                candidate_count = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM candidate.{table}"
                    ).fetchone()[0]
                )
                if production_count < candidate_count:
                    raise RuntimeError(
                        f"production {table} lost rows during replay: "
                        f"{production_count} < {candidate_count}"
                    )
                missing_or_changed = _market_rows_missing_or_changed(connection, table)
                if missing_or_changed:
                    raise RuntimeError(
                        f"production {table} is not an append-only extension of the candidate: "
                        f"{missing_or_changed} candidate rows are missing or changed"
                    )
            for account_id in account_ids:
                for table in DERIVED_TABLES:
                    if table == "events":
                        continue
                    connection.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
                placeholders = ", ".join("?" for _ in REPLAY_EVENT_TYPES)
                connection.execute(
                    f"DELETE FROM events WHERE account_id = ? AND event_type IN ({placeholders})",
                    (account_id, *REPLAY_EVENT_TYPES),
                )
                account = connection.execute(
                    "SELECT * FROM candidate.accounts WHERE id = ?", (account_id,)
                ).fetchone()
                if account is None:
                    raise LookupError(f"candidate is missing account {account_id}")
                connection.execute(
                    "INSERT OR IGNORE INTO accounts SELECT * FROM candidate.accounts WHERE id = ?",
                    (account_id,),
                )
                connection.execute(
                    """
                    UPDATE accounts SET initial_cash = ?, cash = ?, quantity = ?,
                        average_price = ?, realized_pnl = ?, total_fees = ?, total_funding = ?,
                        paper_model = ?, leverage = ?, margin_mode = ?, position_fraction = ?,
                        created_at_ms = ?, updated_at_ms = ? WHERE id = ?
                    """,
                    (
                        account["initial_cash"],
                        account["cash"],
                        account["quantity"],
                        account["average_price"],
                        account["realized_pnl"],
                        account["total_fees"],
                        account["total_funding"],
                        account["paper_model"],
                        account["leverage"],
                        account["margin_mode"],
                        account["position_fraction"],
                        account["created_at_ms"],
                        account["updated_at_ms"],
                        account_id,
                    ),
                )
                for table in ("orders", "fills", "funding_payments", "strategy_states"):
                    _copy_account_rows(connection, table, account_id)
                _copy_account_rows(connection, "equity_snapshots", account_id, omit_id=True)
                _copy_account_rows(connection, "events", account_id, omit_id=True)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("DETACH DATABASE candidate")


def replay_candidate_tail(
    settings: Settings,
    candidate_path: Path,
    account_ids: tuple[str, ...],
) -> dict[str, int]:
    """Replay production trades appended after the candidate backup into selected accounts."""
    instruments = _selected_instruments(settings, account_ids)
    results: dict[str, int] = {}
    for instrument in instruments:
        results[instrument.id] = _replay_account_tail(
            settings,
            PaperStore(settings.database_path),
            candidate_path,
            instrument,
        )
    return results


def _replay_account_tail(
    settings: Settings,
    store: PaperStore,
    candidate_path: Path,
    instrument: InstrumentSettings,
) -> int:
    market_id = instrument.market_id
    strategy_settings = instrument_strategy(settings, instrument)
    database_uri = f"file:{settings.database_path}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("ATTACH DATABASE ? AS candidate", (str(candidate_path),))
        bounds = connection.execute(
            """
            SELECT MIN(p.timestamp_ms) AS first_ms, MAX(p.timestamp_ms) AS last_ms,
                   COUNT(*) AS tick_count
            FROM agg_trades AS p
            LEFT JOIN candidate.agg_trades AS c ON c.event_id = p.event_id
            WHERE p.instrument_id = ? AND c.event_id IS NULL
            """,
            (market_id,),
        ).fetchone()
        tick_count = int(bounds["tick_count"] or 0)
        if not tick_count:
            return 0
        first_ms = int(bounds["first_ms"])
        last_ms = int(bounds["last_ms"])
        warmup = _load_warmup_bars(connection, market_id, first_ms, settings.warmup_bars)
        funding_rates = _load_funding_rates(connection, market_id, first_ms, last_ms)
        rows = connection.execute(
            """
            SELECT p.event_id, p.timestamp_ms, p.price, p.open_price, p.high_price,
                   p.low_price, p.quantity, p.source, p.aggregate_trade_id,
                   p.first_trade_id, p.last_trade_id, p.buyer_is_maker,
                   p.event_time_ms, p.notional
            FROM agg_trades AS p
            LEFT JOIN candidate.agg_trades AS c ON c.event_id = p.event_id
            WHERE p.instrument_id = ? AND c.event_id IS NULL
            ORDER BY p.timestamp_ms, p.received_at_ms, p.event_id
            """,
            (market_id,),
        )

        strategy = ReplayATRTickStrategy(
            strategy_settings.atr_period,
            strategy_settings.atr_multiplier,
            strategy_settings.bar_minutes,
            strategy_settings.trend_efficiency_period,
            strategy_settings.minimum_trend_efficiency,
            strategy_settings.reversal_confirmation_atr,
        )
        strategy.bootstrap(warmup)
        saved_state = store.strategy_state(instrument.id)
        strategy.restore_runtime(saved_state)
        profit_protection = _paper_profit_protection(instrument)
        if profit_protection is not None and saved_state is not None:
            profit_protection.restore_runtime(saved_state.get("profit_protection"))
        position_fraction = strategy_settings.position_fraction
        account = store.account(instrument.id)
        position_quantity = Decimal(account["quantity"])
        average_price = Decimal(account["average_price"])
        has_pending = store.has_pending_order(instrument.id)
        with store.connection() as ledger_connection:
            latest_snapshot = ledger_connection.execute(
                "SELECT MAX(timestamp_ms) FROM equity_snapshots WHERE account_id = ?",
                (instrument.id,),
            ).fetchone()[0]
        last_snapshot_ms = int(latest_snapshot or 0)
        funding_index = 0
        last_tick: Tick | None = None

        for row in rows:
            tick = _tick_from_row(row)
            funding_applied = False
            while (
                funding_index < len(funding_rates)
                and funding_rates[funding_index].timestamp_ms <= tick.timestamp_ms
            ):
                payment = store.apply_funding(
                    instrument.id,
                    funding_rates[funding_index],
                    market_data_id=market_id,
                )
                funding_applied = payment is not None or funding_applied
                funding_index += 1
            fill = None
            if has_pending:
                fill = store.fill_pending(
                    instrument.id,
                    tick,
                    instrument,
                    settings.execution,
                    position_fraction,
                )
                if fill is not None:
                    strategy.on_fill(
                        tick.timestamp_ms,
                        filled=fill.get("status") == "FILLED",
                    )
                has_pending = False
                account = store.account(instrument.id)
                position_quantity = Decimal(account["quantity"])
                average_price = Decimal(account["average_price"])
            signal = strategy.on_tick(
                tick,
                has_position=position_quantity != 0,
                has_pending_order=has_pending,
                allow_short=instrument.short_enabled,
                is_short=position_quantity < 0,
            )
            if signal is None:
                signal = atr_profit_protection_signal(
                    profit_protection,
                    strategy,
                    tick,
                    position_quantity=position_quantity,
                    entry_price=average_price,
                    has_pending_order=has_pending,
                    emit_signals=True,
                )
            if signal is not None:
                store.submit_order(instrument.id, signal, tick.timestamp_ms)
                has_pending = True
            snapshot_due = (
                tick.timestamp_ms - last_snapshot_ms >= settings.equity_snapshot_seconds * 1000
            )
            if snapshot_due or fill or signal or funding_applied:
                store.snapshot(
                    instrument.id,
                    tick,
                    _strategy_view(strategy, profit_protection),
                )
                last_snapshot_ms = tick.timestamp_ms
            last_tick = tick

    if last_tick is not None:
        store.save_strategy_state(
            instrument.id,
            _strategy_state(strategy, profit_protection),
            last_tick.timestamp_ms,
        )
        store.snapshot(
            instrument.id,
            last_tick,
            _strategy_view(strategy, profit_protection),
        )
    return tick_count


def _market_rows_missing_or_changed(
    connection: sqlite3.Connection,
    table: str,
) -> int:
    columns = [
        str(row["name"])
        for row in connection.execute(f"PRAGMA candidate.table_info({table})")
    ]
    if table == "ohlcv_bars":
        columns = ["instrument_id", "interval_minutes", "start_ms"]
    comparison = " AND ".join(f"p.{column} IS c.{column}" for column in columns)
    return int(
        connection.execute(
            f"""
            SELECT COUNT(*) FROM candidate.{table} AS c
            WHERE NOT EXISTS (SELECT 1 FROM main.{table} AS p WHERE {comparison})
            """
        ).fetchone()[0]
    )


def _copy_account_rows(
    connection: sqlite3.Connection,
    table: str,
    account_id: str,
    *,
    omit_id: bool = False,
) -> None:
    columns = [
        str(row["name"])
        for row in connection.execute(f"PRAGMA candidate.table_info({table})")
        if not (omit_id and row["name"] == "id")
    ]
    column_list = ", ".join(columns)
    connection.execute(
        f"""
        INSERT INTO {table} ({column_list})
        SELECT {column_list} FROM candidate.{table} WHERE account_id = ?
        """,
        (account_id,),
    )


def _tick_from_row(row: sqlite3.Row) -> Tick:
    return Tick(
        event_id=row["event_id"],
        timestamp_ms=int(row["timestamp_ms"]),
        price=Decimal(row["price"]),
        quantity=Decimal(row["quantity"]),
        source=row["source"],
        aggregate_trade_id=row["aggregate_trade_id"],
        first_trade_id=row["first_trade_id"],
        last_trade_id=row["last_trade_id"],
        buyer_is_maker=(
            bool(row["buyer_is_maker"]) if row["buyer_is_maker"] is not None else None
        ),
        event_time_ms=row["event_time_ms"],
        open_price=Decimal(row["open_price"]) if row["open_price"] is not None else None,
        high_price=Decimal(row["high_price"]) if row["high_price"] is not None else None,
        low_price=Decimal(row["low_price"]) if row["low_price"] is not None else None,
        notional=Decimal(row["notional"]),
    )


def _paper_profit_protection(instrument: InstrumentSettings) -> ATRProfitProtection | None:
    if (
        instrument.paper_model != "futures"
        or instrument.profit_activation_atr <= 0
        or instrument.profit_trailing_atr <= 0
    ):
        return None
    return ATRProfitProtection(
        instrument.profit_activation_atr,
        instrument.profit_trailing_atr,
    )


def _strategy_state(
    strategy: ReplayATRTickStrategy,
    profit_protection: ATRProfitProtection | None,
) -> dict[str, Any]:
    state = strategy.runtime_state()
    if profit_protection is not None:
        state["profit_protection"] = profit_protection.runtime_state()
    return state


def _strategy_view(
    strategy: ReplayATRTickStrategy,
    profit_protection: ATRProfitProtection | None = None,
) -> dict[str, Any]:
    view = asdict(strategy.view())
    result = {
        key: str(value) if isinstance(value, Decimal) else value
        for key, value in view.items()
    }
    result.update(
        {
            "profit_protection_active": bool(
                profit_protection and profit_protection.active
            ),
            "profit_stop": (
                str(profit_protection.stop)
                if profit_protection and profit_protection.stop is not None
                else None
            ),
            "profit_favorable_extreme": (
                str(profit_protection.favorable_extreme)
                if profit_protection and profit_protection.favorable_extreme is not None
                else None
            ),
        }
    )
    return result


def _ledger_counts(store: PaperStore, account_id: str) -> dict[str, int]:
    with store.connection() as connection:
        result = {
            table: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE account_id = ?", (account_id,)
                ).fetchone()[0]
            )
            for table in ("orders", "fills", "equity_snapshots", "events")
        }
        result["pending_orders"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM orders WHERE account_id = ? AND status = 'PENDING'",
                (account_id,),
            ).fetchone()[0]
        )
    return result


def _market_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("agg_trades", "ohlcv_bars", "funding_rates")
        }


def _selected_instruments(
    settings: Settings,
    account_ids: tuple[str, ...] | None,
) -> tuple[InstrumentSettings, ...]:
    if account_ids is None:
        return settings.instruments
    requested = set(account_ids)
    selected = tuple(item for item in settings.instruments if item.id in requested)
    missing = requested - {item.id for item in selected}
    if missing:
        raise ValueError(f"unknown accounts: {', '.join(sorted(missing))}")
    return selected


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--account-id", action="append", dest="account_ids")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="replace production derived ledgers after creating a recoverable backup",
    )
    args = parser.parse_args()
    settings = load_settings(args.config)
    slug = _timestamp_slug()
    candidate = args.candidate or settings.project_root / "data" / f"rebuild-{slug}.db"
    selected_ids = tuple(args.account_ids) if args.account_ids else None
    report = rebuild_candidate(settings, candidate, selected_ids)
    if args.apply:
        PaperStore(settings.database_path)
        backup_path = settings.project_root / "data" / "backups" / f"paper-{slug}.db"
        backup_database(settings.database_path, backup_path)
        apply_candidate(
            settings.database_path,
            candidate,
            tuple(item["account_id"] for item in report["accounts"]),
        )
        report["tail_ticks"] = replay_candidate_tail(
            settings,
            candidate,
            tuple(item["account_id"] for item in report["accounts"]),
        )
        report["applied"] = True
        report["backup_path"] = str(backup_path)
    else:
        report["applied"] = False
    report["completed_at_ms"] = int(time.time() * 1000)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
