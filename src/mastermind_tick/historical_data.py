"""Build a complete SOXLUSDT research warehouse from official Binance archives."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO
from zipfile import ZipFile

import httpx

from mastermind_tick.feeds import BINANCE_FUTURES_REST, FUTURES_TICK_BUCKET_MS
from mastermind_tick.store import PaperStore

ARCHIVE_ROOT = "https://data.binance.vision/data/futures/um"
ARCHIVE_SOURCE = "binance_futures_aggtrade_archive"
RECENT_SOURCE = "binance_futures_aggtrade_rest_history"
KLINE_SOURCE = "binance_futures_kline_rest_history"
SYMBOL = "SOXLUSDT"
MARKET_ID = "soxl_perp"
INTERVAL_MS = 15 * 60_000
INSERT_BATCH_SIZE = 10_000
REST_REQUEST_SPACING_SECONDS = 0.55
REST_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class ArchiveSpec:
    period: str
    url: str
    filename: str


@dataclass
class ImportStats:
    archive: str
    aggregate_trades: int = 0
    inserted_buckets: int = 0
    first_aggregate_id: int | None = None
    last_aggregate_id: int | None = None
    first_timestamp_ms: int | None = None
    last_timestamp_ms: int | None = None


@dataclass(frozen=True)
class ArchiveTrade:
    aggregate_id: int
    price: Decimal
    quantity: Decimal
    first_trade_id: int
    last_trade_id: int
    timestamp_ms: int
    buyer_is_maker: bool


@dataclass(frozen=True)
class BucketRow:
    event_id: str
    aggregate_id: int
    first_trade_id: int
    last_trade_id: int
    timestamp_ms: int
    price: Decimal
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    quantity: Decimal
    notional: Decimal
    buyer_is_maker: bool | None
    source: str


def archive_specs(onboard_ms: int, through_day: date) -> list[ArchiveSpec]:
    """Return complete monthly archives plus complete days in the current month."""
    onboard_day = datetime.fromtimestamp(onboard_ms / 1000, UTC).date()
    specs: list[ArchiveSpec] = []
    month = onboard_day.replace(day=1)
    through_month = through_day.replace(day=1)
    while month < through_month:
        token = month.strftime("%Y-%m")
        filename = f"{SYMBOL}-aggTrades-{token}.zip"
        specs.append(
            ArchiveSpec(
                period=token,
                url=f"{ARCHIVE_ROOT}/monthly/aggTrades/{SYMBOL}/{filename}",
                filename=filename,
            )
        )
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)

    day = max(onboard_day, through_month)
    while day <= through_day:
        token = day.isoformat()
        filename = f"{SYMBOL}-aggTrades-{token}.zip"
        specs.append(
            ArchiveSpec(
                period=token,
                url=f"{ARCHIVE_ROOT}/daily/aggTrades/{SYMBOL}/{filename}",
                filename=filename,
            )
        )
        day += timedelta(days=1)
    return specs


def download_archive(client: httpx.Client, spec: ArchiveSpec, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / spec.filename
    checksum_path = destination.with_suffix(destination.suffix + ".CHECKSUM")
    _download(client, f"{spec.url}.CHECKSUM", checksum_path)
    expected = checksum_path.read_text(encoding="utf-8").split()[0]
    if destination.exists() and _sha256(destination) == expected:
        return destination
    temporary = destination.with_suffix(destination.suffix + ".part")
    _download(client, spec.url, temporary)
    actual = _sha256(temporary)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {spec.filename}: {actual} != {expected}")
    temporary.replace(destination)
    return destination


def _download(client: httpx.Client, url: str, destination: Path) -> None:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                handle.write(chunk)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_trades(path: Path) -> Iterator[ArchiveTrade]:
    with ZipFile(path) as archive:
        members = [item for item in archive.namelist() if item.endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(f"expected one CSV in {path}, found {members}")
        with archive.open(members[0]) as raw:
            yield from _csv_archive_trades(raw)


def _csv_archive_trades(raw: BinaryIO) -> Iterator[ArchiveTrade]:
    lines = (line.decode("utf-8") for line in raw)
    reader = csv.reader(lines)
    first = next(reader, None)
    if first is None:
        return
    if first and first[0] != "agg_trade_id":
        yield _archive_trade(first)
    for row in reader:
        if row:
            yield _archive_trade(row)


def _archive_trade(row: list[str]) -> ArchiveTrade:
    if len(row) < 7:
        raise RuntimeError(f"invalid Binance aggTrade row: {row}")
    return ArchiveTrade(
        aggregate_id=int(row[0]),
        price=Decimal(row[1]),
        quantity=Decimal(row[2]),
        first_trade_id=int(row[3]),
        last_trade_id=int(row[4]),
        timestamp_ms=_milliseconds(int(row[5])),
        buyer_is_maker=row[6].lower() == "true",
    )


def _milliseconds(value: int) -> int:
    # Binance archives may use microseconds for newer datasets. Futures currently
    # remains millisecond based, but normalizing here makes the importer explicit.
    return value // 1000 if value > 10_000_000_000_000 else value


def bucket_trades(trades: Iterator[ArchiveTrade], source: str) -> Iterator[BucketRow]:
    bucket: dict[str, Any] | None = None
    previous_aggregate_id: int | None = None
    for trade in trades:
        if previous_aggregate_id is not None and trade.aggregate_id != previous_aggregate_id + 1:
            raise RuntimeError(
                "Binance aggregate trade sequence is incomplete: "
                f"expected {previous_aggregate_id + 1}, got {trade.aggregate_id}"
            )
        previous_aggregate_id = trade.aggregate_id
        bucket_id = trade.timestamp_ms // FUTURES_TICK_BUCKET_MS
        if bucket is not None and bucket["bucket_id"] != bucket_id:
            yield _bucket_row(bucket, source)
            bucket = None
        if bucket is None:
            bucket = {
                "bucket_id": bucket_id,
                "first_aggregate_id": trade.aggregate_id,
                "last_aggregate_id": trade.aggregate_id,
                "first_trade_id": trade.first_trade_id,
                "last_trade_id": trade.last_trade_id,
                "timestamp_ms": trade.timestamp_ms,
                "price": trade.price,
                "open_price": trade.price,
                "high_price": trade.price,
                "low_price": trade.price,
                "quantity": trade.quantity,
                "notional": trade.price * trade.quantity,
                "buyer_is_maker": trade.buyer_is_maker,
            }
            continue
        bucket["last_aggregate_id"] = trade.aggregate_id
        bucket["last_trade_id"] = trade.last_trade_id
        bucket["timestamp_ms"] = trade.timestamp_ms
        bucket["price"] = trade.price
        bucket["high_price"] = max(bucket["high_price"], trade.price)
        bucket["low_price"] = min(bucket["low_price"], trade.price)
        bucket["quantity"] += trade.quantity
        bucket["notional"] += trade.price * trade.quantity
        if bucket["buyer_is_maker"] != trade.buyer_is_maker:
            bucket["buyer_is_maker"] = None
    if bucket is not None:
        yield _bucket_row(bucket, source)


def _bucket_row(bucket: dict[str, Any], source: str) -> BucketRow:
    return BucketRow(
        event_id=(f"binance-futures:{SYMBOL}:{bucket['first_trade_id']}-{bucket['last_trade_id']}"),
        aggregate_id=int(bucket["last_aggregate_id"]),
        first_trade_id=int(bucket["first_trade_id"]),
        last_trade_id=int(bucket["last_trade_id"]),
        timestamp_ms=int(bucket["timestamp_ms"]),
        price=bucket["price"],
        open_price=bucket["open_price"],
        high_price=bucket["high_price"],
        low_price=bucket["low_price"],
        quantity=bucket["quantity"],
        notional=bucket["notional"],
        buyer_is_maker=bucket["buyer_is_maker"],
        source=source,
    )


def import_archive(connection: sqlite3.Connection, path: Path) -> ImportStats:
    stats = ImportStats(archive=path.name)
    batch: list[tuple[Any, ...]] = []
    previous_aggregate_id: int | None = None

    def counted() -> Iterator[ArchiveTrade]:
        nonlocal previous_aggregate_id
        for trade in archive_trades(path):
            if (
                previous_aggregate_id is not None
                and trade.aggregate_id != previous_aggregate_id + 1
            ):
                raise RuntimeError(
                    f"{path.name}: expected aggregate ID {previous_aggregate_id + 1}, "
                    f"got {trade.aggregate_id}"
                )
            previous_aggregate_id = trade.aggregate_id
            stats.aggregate_trades += 1
            stats.first_aggregate_id = stats.first_aggregate_id or trade.aggregate_id
            stats.last_aggregate_id = trade.aggregate_id
            stats.first_timestamp_ms = stats.first_timestamp_ms or trade.timestamp_ms
            stats.last_timestamp_ms = trade.timestamp_ms
            yield trade

    for row in bucket_trades(counted(), ARCHIVE_SOURCE):
        batch.append(_database_values(row))
        if len(batch) >= INSERT_BATCH_SIZE:
            stats.inserted_buckets += _insert_batch(connection, batch)
            batch.clear()
    if batch:
        stats.inserted_buckets += _insert_batch(connection, batch)
    connection.commit()
    return stats


def fetch_recent_trades(client: httpx.Client, start_ms: int, end_ms: int) -> Iterator[ArchiveTrade]:
    """Stream the non-archived tail; Binance restricts time searches to two days."""
    if end_ms <= start_ms:
        return
    hour_ms = 60 * 60_000
    cursor = start_ms
    while cursor <= end_ms:
        window_end = min(cursor + hour_ms - 1, end_ms)
        next_id: int | None = None
        while True:
            params: dict[str, int | str] = {"symbol": SYMBOL, "limit": 1000}
            if next_id is None:
                params.update({"startTime": cursor, "endTime": window_end})
            else:
                params["fromId"] = next_id
            response = _weighted_get(client, f"{BINANCE_FUTURES_REST}/aggTrades", params=params)
            page = response.json()
            if not isinstance(page, list):
                raise RuntimeError(f"Binance aggTrade error: {page}")
            selected = [item for item in page if cursor <= int(item["T"]) <= window_end]
            for item in selected:
                yield ArchiveTrade(
                    aggregate_id=int(item["a"]),
                    price=Decimal(item["p"]),
                    quantity=Decimal(item["q"]),
                    first_trade_id=int(item["f"]),
                    last_trade_id=int(item["l"]),
                    timestamp_ms=int(item["T"]),
                    buyer_is_maker=bool(item["m"]),
                )
            if len(page) < 1000 or not page or int(page[-1]["T"]) > window_end:
                break
            candidate = int(page[-1]["a"]) + 1
            if next_id is not None and candidate <= next_id:
                raise RuntimeError("Binance recent aggTrade pagination did not advance")
            next_id = candidate
            time.sleep(0.02)
        cursor = window_end + 1


def _weighted_get(
    client: httpx.Client, url: str, *, params: dict[str, int | str]
) -> httpx.Response:
    for attempt in range(REST_MAX_ATTEMPTS):
        response = client.get(url, params=params)
        if response.status_code not in {418, 429}:
            response.raise_for_status()
            time.sleep(REST_REQUEST_SPACING_SECONDS)
            return response
        retry_after = int(response.headers.get("Retry-After", "1"))
        if retry_after > 60:
            raise RuntimeError(
                "Binance REST IP ban is active; retry after "
                f"{retry_after} seconds instead of blocking the trading host"
            )
        if attempt == REST_MAX_ATTEMPTS - 1:
            response.raise_for_status()
        time.sleep(max(retry_after, 1))
    raise RuntimeError("unreachable Binance REST retry state")


def import_recent(
    connection: sqlite3.Connection,
    client: httpx.Client,
    start_ms: int,
    end_ms: int,
) -> ImportStats:
    stats = ImportStats(archive="recent-rest-tail")
    batch: list[tuple[Any, ...]] = []

    def counted() -> Iterator[ArchiveTrade]:
        previous: int | None = None
        for trade in fetch_recent_trades(client, start_ms, end_ms):
            if previous is not None and trade.aggregate_id != previous + 1:
                raise RuntimeError(
                    "recent REST aggregate sequence is incomplete: "
                    f"expected {previous + 1}, got {trade.aggregate_id}"
                )
            previous = trade.aggregate_id
            stats.aggregate_trades += 1
            stats.first_aggregate_id = stats.first_aggregate_id or trade.aggregate_id
            stats.last_aggregate_id = trade.aggregate_id
            stats.first_timestamp_ms = stats.first_timestamp_ms or trade.timestamp_ms
            stats.last_timestamp_ms = trade.timestamp_ms
            yield trade

    for row in bucket_trades(counted(), RECENT_SOURCE):
        batch.append(_database_values(row))
        if len(batch) >= INSERT_BATCH_SIZE:
            stats.inserted_buckets += _insert_batch(connection, batch)
            batch.clear()
    if batch:
        stats.inserted_buckets += _insert_batch(connection, batch)
    connection.commit()
    return stats


def _database_values(row: BucketRow) -> tuple[Any, ...]:
    return (
        row.event_id,
        MARKET_ID,
        SYMBOL,
        row.aggregate_id,
        row.first_trade_id,
        row.last_trade_id,
        row.timestamp_ms,
        row.timestamp_ms,
        str(row.price),
        str(row.open_price),
        str(row.high_price),
        str(row.low_price),
        str(row.quantity),
        str(row.notional),
        None if row.buyer_is_maker is None else int(row.buyer_is_maker),
        row.source,
        int(time.time() * 1000),
    )


def _insert_batch(connection: sqlite3.Connection, batch: list[tuple[Any, ...]]) -> int:
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
        batch,
    )
    return connection.total_changes - before


def fetch_exchange_onboard_ms(client: httpx.Client) -> int:
    response = client.get(f"{BINANCE_FUTURES_REST}/exchangeInfo")
    response.raise_for_status()
    payload = response.json()
    symbol = next((item for item in payload["symbols"] if item["symbol"] == SYMBOL), None)
    if symbol is None:
        raise RuntimeError(f"{SYMBOL} is absent from Binance Futures exchangeInfo")
    return int(symbol["onboardDate"])


def sync_klines(client: httpx.Client, connection: sqlite3.Connection, start_ms: int) -> int:
    cursor = start_ms
    rows: list[tuple[Any, ...]] = []
    now_ms = int(time.time() * 1000)
    while cursor < now_ms:
        response = client.get(
            f"{BINANCE_FUTURES_REST}/klines",
            params={"symbol": SYMBOL, "interval": "15m", "startTime": cursor, "limit": 1500},
        )
        response.raise_for_status()
        page = response.json()
        if not page:
            break
        updated_at = int(time.time() * 1000)
        for item in page:
            if int(item[6]) >= now_ms:
                continue
            rows.append(
                (
                    MARKET_ID,
                    SYMBOL,
                    15,
                    int(item[0]),
                    int(item[6]),
                    item[1],
                    item[2],
                    item[3],
                    item[4],
                    item[5],
                    int(item[8]),
                    1,
                    KLINE_SOURCE,
                    updated_at,
                )
            )
        candidate = int(page[-1][0]) + INTERVAL_MS
        if candidate <= cursor:
            raise RuntimeError("Binance kline pagination did not advance")
        cursor = candidate
        if len(page) < 1500:
            break
    connection.executemany(
        """
        INSERT INTO ohlcv_bars (
            instrument_id, symbol, interval_minutes, start_ms, end_ms,
            open, high, low, close, volume, trade_count, is_closed, source, updated_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(instrument_id, interval_minutes, start_ms) DO UPDATE SET
            end_ms=excluded.end_ms, open=excluded.open, high=excluded.high,
            low=excluded.low, close=excluded.close, volume=excluded.volume,
            trade_count=excluded.trade_count, is_closed=1,
            source=excluded.source, updated_at_ms=excluded.updated_at_ms
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def sync_funding(client: httpx.Client, connection: sqlite3.Connection, start_ms: int) -> int:
    response = client.get(
        f"{BINANCE_FUTURES_REST}/fundingRate",
        params={"symbol": SYMBOL, "startTime": start_ms, "limit": 1000},
    )
    response.raise_for_status()
    payload = response.json()
    connection.executemany(
        """
        INSERT OR REPLACE INTO funding_rates
            (instrument_id, symbol, timestamp_ms, rate, mark_price, source, created_at_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                MARKET_ID,
                SYMBOL,
                int(item["fundingTime"]),
                item["fundingRate"],
                item["markPrice"],
                KLINE_SOURCE,
                int(time.time() * 1000),
            )
            for item in payload
        ],
    )
    connection.commit()
    return len(payload)


def build_history(
    database_path: Path,
    archive_directory: Path,
    *,
    through_day: date | None = None,
) -> dict[str, Any]:
    PaperStore(database_path, durable=False)
    completed_day = through_day or datetime.now(UTC).date() - timedelta(days=1)
    manifest = archive_directory / "SOXLUSDT-history-manifest.json"
    previous_stats = _previous_manifest_stats(manifest)
    with httpx.Client(timeout=60, follow_redirects=True, trust_env=True) as client:
        onboard_ms = fetch_exchange_onboard_ms(client)
        cutoff_ms = int(time.time() * 1000)
        specs = archive_specs(onboard_ms, completed_day)
        paths = [download_archive(client, spec, archive_directory) for spec in specs]
        stats: list[ImportStats] = []
        with sqlite3.connect(database_path, timeout=60) as connection:
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA journal_mode=WAL")
            for path in paths:
                item = previous_stats.get(path.name)
                already_present = (
                    item is not None
                    and item.last_aggregate_id is not None
                    and connection.execute(
                        """
                        SELECT 1 FROM agg_trades
                        WHERE instrument_id = ? AND timestamp_ms = ?
                          AND aggregate_trade_id = ? LIMIT 1
                        """,
                        (MARKET_ID, item.last_timestamp_ms, item.last_aggregate_id),
                    ).fetchone()
                    is not None
                )
                if not already_present:
                    item = import_archive(connection, path)
                    print(
                        f"Imported {path.name}: {item.aggregate_trades:,} aggregate trades / "
                        f"{item.inserted_buckets:,} buckets",
                        flush=True,
                    )
                assert item is not None
                if stats and item.first_aggregate_id != stats[-1].last_aggregate_id + 1:
                    raise RuntimeError(
                        "archive boundary is incomplete: "
                        f"{stats[-1].archive} ended {stats[-1].last_aggregate_id}, "
                        f"{item.archive} started {item.first_aggregate_id}"
                    )
                stats.append(item)
            recent_start_ms = int(
                datetime.combine(
                    completed_day + timedelta(days=1), datetime.min.time(), UTC
                ).timestamp()
                * 1000
            )
            if recent_start_ms < cutoff_ms:
                recent = import_recent(connection, client, recent_start_ms, cutoff_ms)
                if (
                    recent.aggregate_trades
                    and stats
                    and recent.first_aggregate_id != stats[-1].last_aggregate_id + 1
                ):
                    raise RuntimeError(
                        "archive/REST boundary is incomplete: "
                        f"archive ended {stats[-1].last_aggregate_id}, "
                        f"REST started {recent.first_aggregate_id}"
                    )
                stats.append(recent)
            kline_count = sync_klines(client, connection, onboard_ms)
            funding_count = sync_funding(client, connection, onboard_ms)

    with sqlite3.connect(database_path, timeout=60) as connection:
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agg_trades_instrument_aggregate
            ON agg_trades(instrument_id, aggregate_trade_id)
            """
        )
        connection.execute("ANALYZE agg_trades")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": SYMBOL,
        "market_id": MARKET_ID,
        "onboard_ms": onboard_ms,
        "onboard_at": datetime.fromtimestamp(onboard_ms / 1000, UTC).isoformat(),
        "through_day": completed_day.isoformat(),
        "cutoff_ms": cutoff_ms,
        "cutoff_at": datetime.fromtimestamp(cutoff_ms / 1000, UTC).isoformat(),
        "database_path": str(database_path),
        "archives": [asdict(item) for item in stats],
        "official_klines": kline_count,
        "funding_rates": funding_count,
    }
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _previous_manifest_stats(path: Path) -> dict[str, ImportStats]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            item["archive"]: ImportStats(**item)
            for item in payload.get("archives", [])
            if item.get("archive") != "recent-rest-tail"
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/soxlusdt_history.db"))
    parser.add_argument("--archive-dir", type=Path, default=Path("data/binance_archives"))
    parser.add_argument("--through-day", type=date.fromisoformat)
    args = parser.parse_args()
    payload = build_history(args.database, args.archive_dir, through_day=args.through_day)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
