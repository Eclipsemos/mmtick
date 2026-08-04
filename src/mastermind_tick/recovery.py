"""Recover missing Binance market data and isolated chart artifacts."""

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

import httpx

from mastermind_tick.backtest import ReplayATRTickStrategy, _load_warmup_bars
from mastermind_tick.config import InstrumentSettings, Settings, load_settings
from mastermind_tick.feeds import BINANCE_FUTURES_REST, BINANCE_REST, FUTURES_TICK_BUCKET_MS
from mastermind_tick.models import Side, StrategySignal, Tick
from mastermind_tick.rebuild import backup_database
from mastermind_tick.store import PaperStore

FUTURES_BACKFILL_SOURCE = "binance_futures_aggtrade_rest_backfill"
SPOT_BACKFILL_SOURCE = "binance_public_aggtrade_rest_backfill"
BACKFILL_SOURCE = FUTURES_BACKFILL_SOURCE
BACKFILL_SOURCES = (FUTURES_BACKFILL_SOURCE, SPOT_BACKFILL_SOURCE)
RECONSTRUCTED_SOURCE = "reconstructed_aggtrade_rest"
DEFAULT_GAP_MS = 60_000
REST_MAX_ATTEMPTS = 6


@dataclass(frozen=True)
class TradeGap:
    previous_timestamp_ms: int
    next_timestamp_ms: int
    first_missing_trade_id: int
    last_missing_trade_id: int


@dataclass(frozen=True)
class RecoveryReport:
    account_id: str
    requested_start_ms: int
    requested_end_ms: int
    replay_start_ms: int
    gaps: list[dict[str, int]]
    fetched_agg_trades: int
    inserted_tick_buckets: int
    reconstructed_snapshots: int
    reconstructed_signals: int
    candidate_path: str


def detect_trade_gaps(
    connection: sqlite3.Connection,
    instrument_id: str,
    start_ms: int,
    end_ms: int,
    minimum_gap_ms: int = DEFAULT_GAP_MS,
) -> list[TradeGap]:
    before = connection.execute(
        """
        SELECT timestamp_ms, first_trade_id, last_trade_id FROM agg_trades
        WHERE instrument_id = ? AND timestamp_ms < ?
        ORDER BY timestamp_ms DESC, received_at_ms DESC LIMIT 1
        """,
        (instrument_id, start_ms),
    ).fetchone()
    middle = connection.execute(
        """
        SELECT timestamp_ms, first_trade_id, last_trade_id FROM agg_trades
        WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
        ORDER BY timestamp_ms, received_at_ms, event_id
        """,
        (instrument_id, start_ms, end_ms),
    ).fetchall()
    after = connection.execute(
        """
        SELECT timestamp_ms, first_trade_id, last_trade_id FROM agg_trades
        WHERE instrument_id = ? AND timestamp_ms > ?
        ORDER BY timestamp_ms, received_at_ms LIMIT 1
        """,
        (instrument_id, end_ms),
    ).fetchone()
    rows = [row for row in (before, *middle, after) if row is not None]
    gaps: list[TradeGap] = []
    for left, right in zip(rows, rows[1:], strict=False):
        if int(right["timestamp_ms"]) - int(left["timestamp_ms"]) <= minimum_gap_ms:
            continue
        if left["last_trade_id"] is None or right["first_trade_id"] is None:
            raise ValueError("cannot recover a gap without raw trade IDs")
        first_missing = int(left["last_trade_id"]) + 1
        last_missing = int(right["first_trade_id"]) - 1
        if first_missing <= last_missing:
            gaps.append(
                TradeGap(
                    previous_timestamp_ms=int(left["timestamp_ms"]),
                    next_timestamp_ms=int(right["timestamp_ms"]),
                    first_missing_trade_id=first_missing,
                    last_missing_trade_id=last_missing,
                )
            )
    return gaps


def fetch_gap_agg_trades(
    client: httpx.Client,
    symbol: str,
    gap: TradeGap,
    *,
    rest_base_url: str = BINANCE_FUTURES_REST,
) -> list[dict[str, Any]]:
    url = f"{rest_base_url}/aggTrades"
    payload: list[dict[str, Any]] = []
    next_aggregate_id: int | None = None
    while True:
        params: dict[str, int | str] = {"symbol": symbol, "limit": 1000}
        if next_aggregate_id is None:
            params.update(
                {
                    "startTime": gap.previous_timestamp_ms,
                    "endTime": gap.next_timestamp_ms,
                }
            )
        else:
            params["fromId"] = next_aggregate_id
        response = _get_with_retry(client, url, params)
        page = response.json()
        if not isinstance(page, list):
            raise RuntimeError(f"Binance aggTrade error: {page}")
        if not page:
            break
        payload.extend(page)
        if int(page[-1]["T"]) >= gap.next_timestamp_ms or len(page) < 1000:
            break
        candidate = int(page[-1]["a"]) + 1
        if next_aggregate_id is not None and candidate <= next_aggregate_id:
            raise RuntimeError("Binance aggTrade pagination did not advance")
        next_aggregate_id = candidate
        time.sleep(0.05)

    by_id = {int(item["a"]): item for item in payload}
    selected = sorted(
        (
            item
            for item in by_id.values()
            if int(item["l"]) >= gap.first_missing_trade_id
            and int(item["f"]) <= gap.last_missing_trade_id
        ),
        key=lambda item: int(item["a"]),
    )
    _validate_raw_trade_coverage(selected, gap)
    return selected


def _get_with_retry(
    client: httpx.Client,
    url: str,
    params: dict[str, int | str],
) -> httpx.Response:
    for attempt in range(REST_MAX_ATTEMPTS):
        try:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            if attempt == REST_MAX_ATTEMPTS - 1:
                raise
            retry_after = (
                exc.response.headers.get("Retry-After")
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            delay = float(retry_after) if retry_after else min(0.5 * 2**attempt, 5.0)
            time.sleep(delay)
    raise RuntimeError("unreachable Binance REST retry state")


def _validate_raw_trade_coverage(
    trades: list[dict[str, Any]],
    gap: TradeGap,
) -> None:
    if not trades:
        raise RuntimeError(f"Binance returned no aggTrades for gap {gap}")
    ordered = sorted(trades, key=lambda item: int(item["a"]))
    for left, right in zip(ordered, ordered[1:], strict=False):
        expected_aggregate_id = int(left["a"]) + 1
        if int(right["a"]) != expected_aggregate_id:
            raise RuntimeError(
                "Binance aggregate trade ID gap remains: "
                f"expected {expected_aggregate_id}, got {right['a']}"
            )
        if int(right["f"]) <= int(left["l"]):
            raise RuntimeError("Binance raw trade ranges overlap or are out of order")

    # Binance raw f/l IDs can legitimately skip values even while aggregate
    # trade IDs are continuous. The official aggregate sequence is therefore
    # the hard completeness check; raw IDs only locate the local outage edges.
    if int(ordered[0]["f"]) > gap.last_missing_trade_id:
        raise RuntimeError("Binance recovery starts after the requested raw trade range")
    if int(ordered[-1]["l"]) < gap.first_missing_trade_id:
        raise RuntimeError("Binance recovery ends before the requested raw trade range")


def bucket_agg_trades(symbol: str, trades: list[dict[str, Any]]) -> list[Tick]:
    buckets: list[list[dict[str, Any]]] = []
    for trade in sorted(trades, key=lambda item: (int(item["T"]), int(item["a"]))):
        bucket_id = int(trade["T"]) // FUTURES_TICK_BUCKET_MS
        if not buckets or int(buckets[-1][0]["T"]) // FUTURES_TICK_BUCKET_MS != bucket_id:
            buckets.append([trade])
        else:
            buckets[-1].append(trade)

    result = []
    for bucket in buckets:
        prices = [Decimal(str(item["p"])) for item in bucket]
        quantities = [Decimal(str(item["q"])) for item in bucket]
        makers = {bool(item["m"]) for item in bucket}
        first_aggregate_id = int(bucket[0]["a"])
        last_aggregate_id = int(bucket[-1]["a"])
        result.append(
            Tick(
                event_id=(
                    f"binance-futures-rest:{symbol}:"
                    f"{first_aggregate_id}-{last_aggregate_id}"
                ),
                timestamp_ms=int(bucket[-1]["T"]),
                price=prices[-1],
                quantity=sum(quantities, Decimal("0")),
                source=FUTURES_BACKFILL_SOURCE,
                aggregate_trade_id=last_aggregate_id,
                first_trade_id=int(bucket[0]["f"]),
                last_trade_id=int(bucket[-1]["l"]),
                buyer_is_maker=next(iter(makers)) if len(makers) == 1 else None,
                event_time_ms=int(bucket[-1]["T"]),
                open_price=prices[0],
                high_price=max(prices),
                low_price=min(prices),
                notional=sum(
                    (price * quantity for price, quantity in zip(prices, quantities, strict=True)),
                    Decimal("0"),
                ),
            )
        )
    return result


def spot_agg_trades(symbol: str, trades: list[dict[str, Any]]) -> list[Tick]:
    return [
        Tick(
            event_id=f"binance-spot-rest:{symbol}:{item['a']}",
            timestamp_ms=int(item["T"]),
            price=Decimal(str(item["p"])),
            quantity=Decimal(str(item["q"])),
            source=SPOT_BACKFILL_SOURCE,
            aggregate_trade_id=int(item["a"]),
            first_trade_id=int(item["f"]),
            last_trade_id=int(item["l"]),
            buyer_is_maker=bool(item["m"]),
            event_time_ms=int(item["T"]),
        )
        for item in sorted(trades, key=lambda item: (int(item["T"]), int(item["a"])))
    ]


def recover_candidate(
    settings: Settings,
    candidate_path: Path,
    account_id: str,
    start_ms: int,
    end_ms: int,
    *,
    client: httpx.Client | None = None,
    minimum_gap_ms: int = DEFAULT_GAP_MS,
) -> RecoveryReport:
    if start_ms >= end_ms:
        raise ValueError("recovery start must be before end")
    instrument = _instrument(settings, account_id)
    market_instrument = _instrument(settings, instrument.market_id)
    if market_instrument.feed not in {"binance", "binance_futures"}:
        raise ValueError("aggTrade recovery requires a Binance Spot or Futures feed")
    backup_database(settings.database_path, candidate_path)
    store = PaperStore(candidate_path)
    with store.connection() as connection:
        gaps = detect_trade_gaps(
            connection,
            market_instrument.id,
            start_ms,
            end_ms,
            minimum_gap_ms,
        )
    if not gaps:
        raise ValueError("no recoverable aggTrade gaps found in requested range")

    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=20,
        trust_env=market_instrument.feed == "binance_futures",
    )
    fetched = 0
    inserted = 0
    try:
        for gap in gaps:
            rest_base_url = (
                BINANCE_FUTURES_REST
                if market_instrument.feed == "binance_futures"
                else BINANCE_REST
            )
            trades = fetch_gap_agg_trades(
                active_client,
                market_instrument.symbol,
                gap,
                rest_base_url=rest_base_url,
            )
            fetched += len(trades)
            ticks = (
                bucket_agg_trades(market_instrument.symbol, trades)
                if market_instrument.feed == "binance_futures"
                else spot_agg_trades(market_instrument.symbol, trades)
            )
            for tick in ticks:
                inserted += int(
                    store.record_market_tick(
                        market_instrument,
                        settings.strategy.bar_minutes,
                        tick,
                    )
                )
    finally:
        if owns_client:
            active_client.close()

    replay_start_ms, snapshots, signals = _reconstruct_gap_artifacts(
        settings,
        store,
        instrument,
        market_instrument.id,
        start_ms,
        end_ms,
        gaps,
    )
    return RecoveryReport(
        account_id=account_id,
        requested_start_ms=start_ms,
        requested_end_ms=end_ms,
        replay_start_ms=replay_start_ms,
        gaps=[asdict(gap) for gap in gaps],
        fetched_agg_trades=fetched,
        inserted_tick_buckets=inserted,
        reconstructed_snapshots=snapshots,
        reconstructed_signals=signals,
        candidate_path=str(candidate_path),
    )


def _reconstruct_gap_artifacts(
    settings: Settings,
    store: PaperStore,
    instrument: InstrumentSettings,
    market_id: str,
    start_ms: int,
    end_ms: int,
    gaps: list[TradeGap],
) -> tuple[int, int, int]:
    bar_ms = settings.strategy.bar_minutes * 60_000
    replay_start_ms = min(gap.previous_timestamp_ms for gap in gaps) // bar_ms * bar_ms
    with store.connection() as connection:
        fills = connection.execute(
            """
            SELECT COUNT(*) FROM fills WHERE account_id = ? AND timestamp_ms BETWEEN ? AND ?
            """,
            (instrument.id, replay_start_ms, end_ms),
        ).fetchone()[0]
        funding = connection.execute(
            """
            SELECT COUNT(*) FROM funding_payments
            WHERE account_id = ? AND timestamp_ms BETWEEN ? AND ?
            """,
            (instrument.id, replay_start_ms, end_ms),
        ).fetchone()[0]
        if fills or funding:
            raise RuntimeError(
                "recovery range contains actual fills or funding; segmented account replay required"
            )
        checkpoint = connection.execute(
            """
            SELECT * FROM equity_snapshots WHERE account_id = ? AND timestamp_ms < ?
            ORDER BY timestamp_ms DESC, id DESC LIMIT 1
            """,
            (instrument.id, replay_start_ms),
        ).fetchone()
        if checkpoint is None:
            raise RuntimeError("no equity checkpoint before recovery range")
        warmup = _load_warmup_bars(
            connection,
            market_id,
            replay_start_ms,
            settings.warmup_bars,
        )
        rows = connection.execute(
            """
            SELECT event_id, timestamp_ms, price, open_price, high_price, low_price,
                   quantity, source, aggregate_trade_id, first_trade_id, last_trade_id,
                   buyer_is_maker, event_time_ms, notional
            FROM agg_trades WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
            ORDER BY timestamp_ms, received_at_ms, event_id
            """,
            (market_id, replay_start_ms, end_ms),
        ).fetchall()
        average_price = _position_average_price(
            connection,
            instrument.id,
            Decimal(checkpoint["quantity"]),
            replay_start_ms,
        )

    if len(warmup) < settings.strategy.atr_period:
        raise RuntimeError("insufficient official OHLCV warm-up for recovery replay")
    strategy = ReplayATRTickStrategy(
        settings.strategy.atr_period,
        settings.strategy.atr_multiplier,
        settings.strategy.bar_minutes,
        settings.strategy.trend_efficiency_period,
        settings.strategy.minimum_trend_efficiency,
        settings.strategy.reversal_confirmation_atr,
    )
    strategy.bootstrap(warmup)
    if checkpoint["atr"] is not None:
        strategy.last_atr = Decimal(checkpoint["atr"])
    if checkpoint["trailing_stop"] is not None:
        strategy.trailing_stop = Decimal(checkpoint["trailing_stop"])
    strategy.previous_price = Decimal(checkpoint["price"])
    strategy.startup_alignment_checked = True

    cash = Decimal(checkpoint["cash"])
    quantity = Decimal(checkpoint["quantity"])
    realized_pnl = Decimal(checkpoint["realized_pnl"])
    total_funding = Decimal(checkpoint["total_funding"])
    snapshot_interval_ms = settings.equity_snapshot_seconds * 1000
    last_snapshot_ms = start_ms - snapshot_interval_ms
    snapshots: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    shadow_quantity = quantity
    pending_signal: StrategySignal | None = None

    for row in rows:
        tick = _tick_from_row(row)
        if pending_signal is not None and tick.event_id != pending_signal.tick_id:
            filled = False
            if pending_signal.reduce_only:
                opposing_position = (
                    shadow_quantity < 0 and pending_signal.side is Side.BUY
                ) or (
                    shadow_quantity > 0 and pending_signal.side is Side.SELL
                )
                if opposing_position:
                    shadow_quantity = Decimal("0")
                    filled = True
            elif shadow_quantity == 0:
                shadow_quantity = (
                    Decimal("1") if pending_signal.side is Side.BUY else Decimal("-1")
                )
                filled = True
            strategy.on_fill(tick.timestamp_ms, filled=filled)
            pending_signal = None
        signal = strategy.on_tick(
            tick,
            has_position=shadow_quantity != 0,
            has_pending_order=pending_signal is not None,
            allow_short=instrument.short_enabled,
            is_short=shadow_quantity < 0,
        )
        in_reconstruction_range = start_ms <= tick.timestamp_ms <= end_ms
        if signal is not None and in_reconstruction_range:
            action = _signal_action(signal.side, shadow_quantity)
            signals.append(
                {
                    "id": f"reconstructed:{instrument.id}:{signal.tick_id}:{signal.side.value}",
                    "account_id": instrument.id,
                    "timestamp_ms": tick.timestamp_ms,
                    "side": signal.side.value,
                    "action": action,
                    "price": str(signal.signal_price),
                    "atr": str(signal.atr),
                    "trailing_stop": str(signal.trailing_stop),
                    "reason": signal.reason,
                    "source": RECONSTRUCTED_SOURCE,
                    "replay_start_ms": replay_start_ms,
                    "replay_end_ms": end_ms,
                    "created_at_ms": int(time.time() * 1000),
                }
            )
            pending_signal = signal
        if not in_reconstruction_range:
            continue
        if tick.timestamp_ms - last_snapshot_ms < snapshot_interval_ms:
            continue
        snapshots.append(
            _reconstructed_snapshot(
                instrument,
                tick,
                cash,
                quantity,
                average_price,
                realized_pnl,
                total_funding,
                strategy,
            )
        )
        last_snapshot_ms = tick.timestamp_ms

    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            DELETE FROM equity_snapshots
            WHERE account_id = ? AND timestamp_ms BETWEEN ? AND ?
            """,
            (instrument.id, start_ms, end_ms),
        )
        connection.execute(
            """
            DELETE FROM reconstructed_signals
            WHERE account_id = ? AND source = ? AND timestamp_ms BETWEEN ? AND ?
            """,
            (instrument.id, RECONSTRUCTED_SOURCE, start_ms, end_ms),
        )
        connection.executemany(
            """
            INSERT INTO equity_snapshots (
                account_id, timestamp_ms, price, cash, quantity, market_value,
                equity, unrealized_pnl, realized_pnl, atr, trailing_stop, relation,
                mark_price, index_price, funding_rate, initial_margin,
                available_balance, total_funding, source
            ) VALUES (
                :account_id, :timestamp_ms, :price, :cash, :quantity, :market_value,
                :equity, :unrealized_pnl, :realized_pnl, :atr, :trailing_stop, :relation,
                :mark_price, NULL, NULL, :initial_margin,
                :available_balance, :total_funding, :source
            )
            """,
            snapshots,
        )
        connection.executemany(
            """
            INSERT INTO reconstructed_signals (
                id, account_id, timestamp_ms, side, action, price, atr, trailing_stop,
                reason, source, replay_start_ms, replay_end_ms, created_at_ms
            ) VALUES (
                :id, :account_id, :timestamp_ms, :side, :action, :price, :atr,
                :trailing_stop, :reason, :source, :replay_start_ms, :replay_end_ms,
                :created_at_ms
            )
            """,
            signals,
        )
    return replay_start_ms, len(snapshots), len(signals)


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


def _position_average_price(
    connection: sqlite3.Connection,
    account_id: str,
    quantity: Decimal,
    timestamp_ms: int,
) -> Decimal:
    if quantity == 0:
        return Decimal("0")
    row = connection.execute(
        """
        SELECT price, side FROM fills WHERE account_id = ? AND position_effect = 'OPEN'
          AND timestamp_ms < ? ORDER BY timestamp_ms DESC, id DESC LIMIT 1
        """,
        (account_id, timestamp_ms),
    ).fetchone()
    if row is None:
        raise RuntimeError("cannot determine historical position average price")
    expected_side = "BUY" if quantity > 0 else "SELL"
    if row["side"] != expected_side:
        raise RuntimeError("historical position side does not match latest OPEN fill")
    return Decimal(row["price"])


def _signal_action(side: Side, quantity: Decimal) -> str:
    if quantity < 0 and side is Side.BUY or quantity > 0 and side is Side.SELL:
        return "CLOSE"
    return "LONG" if side is Side.BUY else "SHORT"


def _reconstructed_snapshot(
    instrument: InstrumentSettings,
    tick: Tick,
    cash: Decimal,
    quantity: Decimal,
    average_price: Decimal,
    realized_pnl: Decimal,
    total_funding: Decimal,
    strategy: ReplayATRTickStrategy,
) -> dict[str, Any]:
    is_futures = instrument.paper_model == "futures"
    mark_price = tick.price
    market_value = abs(quantity) * mark_price if is_futures else quantity * tick.price
    unrealized = quantity * (mark_price - average_price) if quantity else Decimal("0")
    if is_futures:
        equity = cash + unrealized
        initial_margin = market_value / Decimal(instrument.leverage)
        available_balance = equity - initial_margin
    else:
        equity = cash + market_value
        initial_margin = Decimal("0")
        available_balance = cash
    view = strategy.view()
    return {
        "account_id": instrument.id,
        "timestamp_ms": tick.timestamp_ms,
        "price": str(tick.price),
        "cash": str(cash),
        "quantity": str(quantity),
        "market_value": str(market_value),
        "equity": str(equity),
        "unrealized_pnl": str(unrealized),
        "realized_pnl": str(realized_pnl),
        "atr": str(view.atr) if view.atr is not None else None,
        "trailing_stop": str(view.trailing_stop) if view.trailing_stop is not None else None,
        "relation": view.relation,
        "mark_price": str(mark_price) if is_futures else None,
        "initial_margin": str(initial_margin),
        "available_balance": str(available_balance),
        "total_funding": str(total_funding),
        "source": RECONSTRUCTED_SOURCE,
    }


def apply_recovery_candidate(
    production_path: Path,
    candidate_path: Path,
    account_id: str,
    start_ms: int,
    end_ms: int,
    *,
    market_start_ms: int | None = None,
    market_id: str | None = None,
) -> dict[str, int]:
    PaperStore(production_path)
    trade_start_ms = market_start_ms if market_start_ms is not None else start_ms
    resolved_market_id = market_id or account_id
    with sqlite3.connect(production_path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("ATTACH DATABASE ? AS candidate", (str(candidate_path),))
        try:
            connection.execute("BEGIN IMMEDIATE")
            before_trades = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO agg_trades (
                    event_id, instrument_id, symbol, aggregate_trade_id,
                    first_trade_id, last_trade_id, event_time_ms, timestamp_ms,
                    price, open_price, high_price, low_price, quantity, notional,
                    buyer_is_maker, source, received_at_ms
                )
                SELECT event_id, instrument_id, symbol, aggregate_trade_id,
                    first_trade_id, last_trade_id, event_time_ms, timestamp_ms,
                    price, open_price, high_price, low_price, quantity, notional,
                    buyer_is_maker, source, received_at_ms
                FROM candidate.agg_trades WHERE instrument_id = ? AND source IN (?, ?)
                  AND timestamp_ms BETWEEN ? AND ?
                """,
                (resolved_market_id, *BACKFILL_SOURCES, trade_start_ms, end_ms),
            )
            inserted_trades = connection.total_changes - before_trades
            connection.execute(
                """
                DELETE FROM equity_snapshots
                WHERE account_id = ? AND timestamp_ms BETWEEN ? AND ?
                """,
                (account_id, start_ms, end_ms),
            )
            before_snapshots = connection.total_changes
            connection.execute(
                """
                INSERT INTO equity_snapshots (
                    account_id, timestamp_ms, price, cash, quantity, market_value,
                    equity, unrealized_pnl, realized_pnl, atr, trailing_stop, relation,
                    mark_price, index_price, funding_rate, initial_margin,
                    available_balance, total_funding, source
                )
                SELECT account_id, timestamp_ms, price, cash, quantity, market_value,
                    equity, unrealized_pnl, realized_pnl, atr, trailing_stop, relation,
                    mark_price, index_price, funding_rate, initial_margin,
                    available_balance, total_funding, source
                FROM candidate.equity_snapshots
                WHERE account_id = ? AND source = ? AND timestamp_ms BETWEEN ? AND ?
                """,
                (account_id, RECONSTRUCTED_SOURCE, start_ms, end_ms),
            )
            inserted_snapshots = connection.total_changes - before_snapshots
            connection.execute(
                """
                DELETE FROM reconstructed_signals
                WHERE account_id = ? AND source = ? AND timestamp_ms BETWEEN ? AND ?
                """,
                (account_id, RECONSTRUCTED_SOURCE, start_ms, end_ms),
            )
            before_signals = connection.total_changes
            connection.execute(
                """
                INSERT INTO reconstructed_signals
                SELECT * FROM candidate.reconstructed_signals
                WHERE account_id = ? AND source = ? AND timestamp_ms BETWEEN ? AND ?
                """,
                (account_id, RECONSTRUCTED_SOURCE, start_ms, end_ms),
            )
            inserted_signals = connection.total_changes - before_signals
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("DETACH DATABASE candidate")
    return {
        "agg_trade_buckets": inserted_trades,
        "snapshots": inserted_snapshots,
        "signals": inserted_signals,
    }


def _instrument(settings: Settings, account_id: str) -> InstrumentSettings:
    instrument = next((item for item in settings.instruments if item.id == account_id), None)
    if instrument is None:
        raise LookupError(account_id)
    return instrument


def _slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--account-id", default="soxl_perp")
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--minimum-gap-ms", type=int, default=DEFAULT_GAP_MS)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = load_settings(args.config)
    slug = _slug()
    candidate = args.candidate or settings.project_root / "data" / f"recovery-{slug}.db"
    report = recover_candidate(
        settings,
        candidate,
        args.account_id,
        args.start_ms,
        args.end_ms,
        minimum_gap_ms=args.minimum_gap_ms,
    )
    output: dict[str, Any] = asdict(report)
    if args.apply:
        backup_path = settings.project_root / "data" / "backups" / f"paper-{slug}.db"
        backup_database(settings.database_path, backup_path)
        output["applied"] = apply_recovery_candidate(
            settings.database_path,
            candidate,
            args.account_id,
            args.start_ms,
            args.end_ms,
            market_start_ms=report.replay_start_ms,
            market_id=_instrument(settings, args.account_id).market_id,
        )
        output["backup_path"] = str(backup_path)
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
