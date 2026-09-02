#!/usr/bin/env python3
"""Download and import Binance USD-M Futures history used by tick replay.

Binance Data Vision archives are used for completed months/days.  The current
partial day is fetched from the public REST endpoint and paginated by aggregate
trade ID.  Raw aggregate trades are reduced to the same 250 ms buckets emitted
by ``BinanceFuturesFeed`` before being written to the paper warehouse.
"""

from __future__ import annotations

import argparse
import csv
import io
import itertools
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from mastermind_tick.store import PaperStore

ARCHIVE_BASE = "https://data.binance.vision/data/futures/um"
REST_BASE = "https://fapi.binance.com/fapi/v1"
BUCKET_MS = 250
SOURCE = "binance_futures_archive"
REST_SOURCE = "binance_futures_aggtrade_rest"
KLINE_SOURCE = "binance_futures_kline_archive"
KLINE_REST_SOURCE = "binance_futures_kline_rest"
FUNDING_SOURCE = "binance_futures_funding_rest"
KLINE_FIELDS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)


def _get(url: str, *, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "mmtick-history/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _download_zip(url: str) -> zipfile.ZipFile | None:
    try:
        return zipfile.ZipFile(io.BytesIO(_get(url)))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _archive_rows(url: str) -> list[dict[str, str]]:
    archive = _download_zip(url)
    if archive is None:
        return []
    names = [name for name in archive.namelist() if name.endswith(".csv")]
    if len(names) != 1:
        raise RuntimeError(f"expected one CSV in {url}, found {names}")
    with archive.open(names[0]) as handle:
        return list(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")))


def _kline_rows(handle) -> list[dict[str, str]]:
    """Parse Binance kline archives from both headerless and newer headered CSV files."""
    reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8"))
    first = next(reader, None)
    if first is None:
        return []
    rows = reader if first[0] == "open_time" else itertools.chain((first,), reader)
    return [dict(zip(KLINE_FIELDS, row, strict=True)) for row in rows]


def _months(start: date, end: date) -> list[tuple[int, int]]:
    value = date(start.year, start.month, 1)
    result = []
    while value <= end:
        result.append((value.year, value.month))
        value = date(
            value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1
        )
    return result


def _bucket_rows(rows, instrument_id: str, symbol: str, source: str):
    accumulator = _BucketAccumulator(instrument_id, symbol, source)
    for row in rows:
        yield from accumulator.add(row)
    yield from accumulator.finish()


class _BucketAccumulator:
    def __init__(self, instrument_id: str, symbol: str, source: str):
        self.instrument_id = instrument_id
        self.symbol = symbol
        self.source = source
        self.bucket: dict[str, object] | None = None

    def add(self, row: dict[str, str]):
        timestamp_ms = int(row["transact_time"] if "transact_time" in row else row["T"])
        price = Decimal(row["price"] if "price" in row else row["p"])
        quantity = Decimal(row["quantity"] if "quantity" in row else row["q"])
        aggregate_id = int(row["agg_trade_id"] if "agg_trade_id" in row else row["a"])
        first_id = int(row["first_trade_id"] if "first_trade_id" in row else row["f"])
        last_id = int(row["last_trade_id"] if "last_trade_id" in row else row["l"])
        buyer_is_maker = (
            row["is_buyer_maker"].lower() == "true" if "is_buyer_maker" in row else bool(row["m"])
        )
        bucket_id = timestamp_ms // BUCKET_MS
        if self.bucket is None or self.bucket["bucket_id"] != bucket_id:
            if self.bucket is not None:
                yield _bucket_tuple(self.bucket, self.instrument_id, self.symbol, self.source)
            self.bucket = {
                "bucket_id": bucket_id,
                "timestamp_ms": timestamp_ms,
                "price": price,
                "open_price": price,
                "high_price": price,
                "low_price": price,
                "quantity": quantity,
                "notional": price * quantity,
                "aggregate_trade_id": aggregate_id,
                "first_trade_id": first_id,
                "last_trade_id": last_id,
                "buyer_is_maker": buyer_is_maker,
            }
            return
        self.bucket["timestamp_ms"] = timestamp_ms
        self.bucket["price"] = price
        self.bucket["high_price"] = max(self.bucket["high_price"], price)
        self.bucket["low_price"] = min(self.bucket["low_price"], price)
        self.bucket["quantity"] += quantity
        self.bucket["notional"] += price * quantity
        self.bucket["aggregate_trade_id"] = aggregate_id
        self.bucket["last_trade_id"] = last_id
        if self.bucket["buyer_is_maker"] != buyer_is_maker:
            self.bucket["buyer_is_maker"] = None

    def finish(self):
        if self.bucket is not None:
            yield _bucket_tuple(self.bucket, self.instrument_id, self.symbol, self.source)
            self.bucket = None


def _bucket_tuple(bucket: dict[str, object], instrument_id: str, symbol: str, source: str) -> tuple:
    received_at_ms = int(time.time() * 1000)
    return (
        f"binance-futures-rest:{symbol}:{bucket['first_trade_id']}-{bucket['last_trade_id']}",
        instrument_id,
        symbol,
        bucket["aggregate_trade_id"],
        bucket["first_trade_id"],
        bucket["last_trade_id"],
        bucket["timestamp_ms"],
        bucket["timestamp_ms"],
        str(bucket["price"]),
        str(bucket["open_price"]),
        str(bucket["high_price"]),
        str(bucket["low_price"]),
        str(bucket["quantity"]),
        str(bucket["notional"]),
        None if bucket["buyer_is_maker"] is None else int(bucket["buyer_is_maker"]),
        source,
        received_at_ms,
    )


def _insert_ticks(connection: sqlite3.Connection, rows) -> int:
    batch: list[tuple] = []
    inserted = 0
    for row in rows:
        batch.append(row)
        if len(batch) >= 10_000:
            inserted += _insert_tick_batch(connection, batch)
            batch.clear()
    if batch:
        inserted += _insert_tick_batch(connection, batch)
    return inserted


def _insert_tick_batch(connection: sqlite3.Connection, rows: list[tuple]) -> int:
    before = connection.total_changes
    connection.executemany(
        """
        INSERT OR IGNORE INTO agg_trades (
            event_id, instrument_id, symbol, aggregate_trade_id,
            first_trade_id, last_trade_id, event_time_ms, timestamp_ms,
            price, open_price, high_price, low_price, quantity, notional,
            buyer_is_maker, source, received_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return connection.total_changes - before


def _import_bars(
    connection: sqlite3.Connection,
    rows: list[dict[str, str]],
    instrument_id: str,
    symbol: str,
) -> int:
    values = []
    now_ms = int(time.time() * 1000)
    for row in rows:
        start_ms = int(row["open_time"])
        values.append(
            (
                instrument_id,
                symbol,
                15,
                start_ms,
                int(row["close_time"]),
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
                int(row["count"]),
                1,
                KLINE_SOURCE,
                now_ms,
            )
        )
    before = connection.total_changes
    connection.executemany(
        """
        INSERT INTO ohlcv_bars (
            instrument_id, symbol, interval_minutes, start_ms, end_ms,
            open, high, low, close, volume, trade_count, is_closed, source, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instrument_id, interval_minutes, start_ms) DO UPDATE SET
            end_ms=excluded.end_ms, open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume, trade_count=excluded.trade_count,
            is_closed=excluded.is_closed,
            source=excluded.source,
            updated_at_ms=excluded.updated_at_ms
        """,
        values,
    )
    return connection.total_changes - before


def _fetch_funding(instrument_id: str, symbol: str, start_ms: int, end_ms: int) -> list[tuple]:
    now_ms = int(time.time() * 1000)
    result: list[tuple] = []
    cursor = start_ms
    while cursor <= end_ms:
        params = urllib.parse.urlencode(
            {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000}
        )
        payload = json.loads(_get(f"{REST_BASE}/fundingRate?{params}"))
        if not payload:
            break
        result.extend(
            (
                instrument_id,
                symbol,
                int(item["fundingTime"]),
                item["fundingRate"],
                item["markPrice"],
                FUNDING_SOURCE,
                now_ms,
            )
            for item in payload
            if start_ms <= int(item["fundingTime"]) <= end_ms
        )
        last_time = int(payload[-1]["fundingTime"])
        if len(payload) < 1000 or last_time >= end_ms:
            break
        next_cursor = last_time + 1
        if next_cursor <= cursor:
            raise RuntimeError("funding endpoint returned a non-advancing page")
        cursor = next_cursor
        time.sleep(0.05)
    return result


def _fetch_current_klines(
    connection: sqlite3.Connection,
    instrument_id: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> int:
    row = connection.execute(
        """
        SELECT MAX(start_ms) FROM ohlcv_bars
        WHERE instrument_id = ? AND interval_minutes = 15
        """,
        (instrument_id,),
    ).fetchone()
    next_start = int(row[0]) + 15 * 60_000 if row and row[0] is not None else start_ms
    inserted = 0
    while next_start < end_ms:
        query = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "interval": "15m",
                "startTime": next_start,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        payload = json.loads(_get(f"{REST_BASE}/klines?{query}"))
        closed = [item for item in payload if int(item[6]) <= end_ms]
        if not closed:
            break
        rows = [
            {
                "open_time": str(item[0]),
                "open": item[1],
                "high": item[2],
                "low": item[3],
                "close": item[4],
                "volume": item[5],
                "close_time": str(item[6]),
                "count": str(item[8]),
            }
            for item in closed
        ]
        before = connection.total_changes
        _import_bars(connection, rows, instrument_id, symbol)
        connection.execute(
            """
            UPDATE ohlcv_bars SET source = ?
            WHERE instrument_id = ? AND interval_minutes = 15
              AND start_ms BETWEEN ? AND ?
            """,
            (KLINE_REST_SOURCE, instrument_id, int(closed[0][0]), int(closed[-1][0])),
        )
        inserted += connection.total_changes - before
        next_start = int(closed[-1][0]) + 15 * 60_000
        if len(payload) < 1000:
            break
    return inserted


def _fetch_current_agg_trades(
    connection: sqlite3.Connection,
    instrument_id: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> int:
    row = connection.execute(
        """
        SELECT aggregate_trade_id, timestamp_ms
        FROM agg_trades
        WHERE instrument_id = ?
        ORDER BY timestamp_ms DESC
        LIMIT 1
        """,
        (instrument_id,),
    ).fetchone()
    # Completed archive days already contain the final bucket; avoid querying the
    # REST endpoint with a fromId that falls just beyond its historical window.
    if row and row[1] is not None and int(row[1]) >= end_ms - 1_000:
        return 0
    next_id = int(row[0]) + 1 if row and row[0] is not None else None
    cursor_start = max(start_ms, int(row[1]) + 1) if row and row[1] is not None else start_ms
    if cursor_start > end_ms:
        return 0
    use_id_cursor = next_id is not None
    accumulator = _BucketAccumulator(instrument_id, symbol, REST_SOURCE)
    total = 0
    while True:
        query = {"symbol": symbol, "limit": 1000, "endTime": end_ms}
        if use_id_cursor and next_id is not None:
            query["fromId"] = next_id
        else:
            query["startTime"] = cursor_start
        try:
            payload = json.loads(_get(f"{REST_BASE}/aggTrades?{urllib.parse.urlencode(query)}"))
        except urllib.error.HTTPError as exc:
            # Binance only exposes a short recent window for ID-based aggTrade lookup.
            if (
                use_id_cursor
                and exc.code == 400
                and any(code in exc.read() for code in (b"-1000", b"-4166"))
            ):
                use_id_cursor = False
                next_id = None
                continue
            raise
        if not payload:
            break
        for item in payload:
            timestamp_ms = int(item["T"])
            if timestamp_ms < start_ms:
                continue
            if timestamp_ms > end_ms:
                total += _insert_ticks(connection, accumulator.finish())
                return total
            for bucket in accumulator.add(item):
                total += _insert_ticks(connection, (bucket,))
        last_id = int(payload[-1]["a"])
        if len(payload) < 1000 or last_id < (next_id or 0):
            break
        if use_id_cursor:
            next_id = last_id + 1
        else:
            cursor_start = int(payload[-1]["T"]) + 1
        if total and total % 100_000 < 10_000:
            connection.commit()
            cursor_label = (
                f"aggregate id {next_id:,}" if next_id is not None else f"timestamp {cursor_start}"
            )
            print(f"current REST ticks imported: {total:,}; next {cursor_label}", flush=True)
        time.sleep(0.05)
    total += _insert_ticks(connection, accumulator.finish())
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/paper.db")
    parser.add_argument("--instrument-id", default="soxl_perp")
    parser.add_argument("--symbol", default="SOXLUSDT")
    parser.add_argument("--start-ms", type=int, default=1778853600000)
    parser.add_argument("--end-ms", type=int, default=None)
    parser.add_argument("--archive-dir", default="data/history_soxl")
    parser.add_argument("--incremental-only", action="store_true")
    parser.add_argument(
        "--bars-only",
        action="store_true",
        help="download/import 15m OHLCV and funding without aggregate trades",
    )
    args = parser.parse_args()
    instrument_id = args.instrument_id
    symbol = args.symbol.upper()
    end_ms = args.end_ms or int(time.time() * 1000)
    start_date = datetime.fromtimestamp(args.start_ms / 1000, UTC).date()
    end_date = datetime.fromtimestamp(end_ms / 1000, UTC).date()
    archive_dir = Path(args.archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    PaperStore(Path(args.database))
    connection = sqlite3.connect(args.database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        total_ticks = total_bars = 0
        for year, month in [] if args.incremental_only else _months(start_date, end_date):
            month_start = date(year, month, 1)
            next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
            monthly_complete = next_month <= end_date
            if monthly_complete:
                if not args.bars_only:
                    name = f"{symbol}-aggTrades-{year:04d}-{month:02d}.zip"
                    url = f"{ARCHIVE_BASE}/monthly/aggTrades/{symbol}/{name}"
                    path = archive_dir / name
                    if not path.exists():
                        path.write_bytes(_get(url))
                    with zipfile.ZipFile(path) as archive:
                        csv_name = next(
                            name for name in archive.namelist() if name.endswith(".csv")
                        )
                        with archive.open(csv_name) as handle:
                            total_ticks += _insert_ticks(
                                connection,
                                _bucket_rows(
                                    csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")),
                                    instrument_id,
                                    symbol,
                                    SOURCE,
                                ),
                            )
                kname = f"{symbol}-15m-{year:04d}-{month:02d}.zip"
                kpath = archive_dir / kname
                if not kpath.exists():
                    kpath.write_bytes(_get(f"{ARCHIVE_BASE}/monthly/klines/{symbol}/15m/{kname}"))
                with zipfile.ZipFile(kpath) as archive:
                    csv_name = next(name for name in archive.namelist() if name.endswith(".csv"))
                    with archive.open(csv_name) as handle:
                        total_bars += _import_bars(
                            connection,
                            _kline_rows(handle),
                            instrument_id,
                            symbol,
                        )
            else:
                day = max(month_start, start_date)
                while day <= end_date:
                    if not args.bars_only:
                        name = f"{symbol}-aggTrades-{day.isoformat()}.zip"
                        url = f"{ARCHIVE_BASE}/daily/aggTrades/{symbol}/{name}"
                        path = archive_dir / name
                        if not path.exists():
                            try:
                                path.write_bytes(_get(url))
                            except urllib.error.HTTPError as exc:
                                if exc.code == 404:
                                    day += timedelta(days=1)
                                    continue
                                raise
                        with zipfile.ZipFile(path) as archive:
                            csv_name = next(
                                name for name in archive.namelist() if name.endswith(".csv")
                            )
                            with archive.open(csv_name) as handle:
                                total_ticks += _insert_ticks(
                                    connection,
                                    _bucket_rows(
                                        csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8")),
                                        instrument_id,
                                        symbol,
                                        SOURCE,
                                    ),
                                )
                    kname = f"{symbol}-15m-{day.isoformat()}.zip"
                    kpath = archive_dir / kname
                    if not kpath.exists():
                        try:
                            kpath.write_bytes(
                                _get(f"{ARCHIVE_BASE}/daily/klines/{symbol}/15m/{kname}")
                            )
                        except urllib.error.HTTPError as exc:
                            if exc.code == 404:
                                day += timedelta(days=1)
                                continue
                            raise
                    with zipfile.ZipFile(kpath) as archive:
                        csv_name = next(
                            name for name in archive.namelist() if name.endswith(".csv")
                        )
                        with archive.open(csv_name) as handle:
                            total_bars += _import_bars(
                                connection,
                                _kline_rows(handle),
                                instrument_id,
                                symbol,
                            )
                    day += timedelta(days=1)
            connection.commit()
        current_ticks = (
            0
            if args.bars_only
            else _fetch_current_agg_trades(connection, instrument_id, symbol, args.start_ms, end_ms)
        )
        total_bars += _fetch_current_klines(
            connection, instrument_id, symbol, args.start_ms, end_ms
        )
        connection.commit()
        funding = _fetch_funding(instrument_id, symbol, args.start_ms, end_ms)
        connection.executemany(
            """
            INSERT OR REPLACE INTO funding_rates (
                instrument_id, symbol, timestamp_ms, rate, mark_price, source, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            funding,
        )
        connection.commit()
        print(
            f"imported_ticks={total_ticks + current_ticks} "
            f"(archive={total_ticks}, current_rest={current_ticks}) "
            f"imported_bars={total_bars} funding_rates={len(funding)}"
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
