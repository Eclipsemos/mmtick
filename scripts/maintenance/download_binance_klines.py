#!/usr/bin/env python3
"""Download Binance USD-M kline archives and a completed-bar current-day CSV."""

from __future__ import annotations

import argparse
import csv
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ARCHIVE_BASE = "https://data.binance.vision/data/futures/um"
REST_BASE = "https://fapi.binance.com/fapi/v1/klines"
HEADER = (
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=datetime.now(UTC).date())
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    symbol = args.symbol.upper()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    current_month = date(args.end.year, args.end.month, 1)
    downloaded_months: set[date] = set()
    for year, month in months(args.start, args.end):
        month_start = date(year, month, 1)
        if month_start >= current_month:
            continue
        name = f"{symbol}-{args.interval}-{year:04d}-{month:02d}.zip"
        if download(
            f"{ARCHIVE_BASE}/monthly/klines/{symbol}/{args.interval}/{name}",
            args.output_dir / name,
            allow_missing=True,
        ):
            downloaded_months.add(month_start)

    day = args.start
    while day < args.end:
        if date(day.year, day.month, 1) in downloaded_months:
            day += timedelta(days=1)
            continue
        name = f"{symbol}-{args.interval}-{day.isoformat()}.zip"
        downloaded = download(
            f"{ARCHIVE_BASE}/daily/klines/{symbol}/{args.interval}/{name}",
            args.output_dir / name,
            allow_missing=True,
        )
        if not downloaded:
            write_rest_archive_day(symbol, args.interval, day, args.output_dir)
        day += timedelta(days=1)
    write_current_completed(symbol, args.interval, args.end, args.output_dir)


def months(start: date, end: date) -> list[tuple[int, int]]:
    value = date(start.year, start.month, 1)
    result = []
    while value <= end:
        result.append((value.year, value.month))
        value = date(
            value.year + (value.month == 12),
            1 if value.month == 12 else value.month + 1,
            1,
        )
    return result


def download(url: str, path: Path, *, allow_missing: bool = False) -> bool:
    if path.exists():
        return True
    request = urllib.request.Request(url, headers={"User-Agent": "mmtick-kline-research/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            path.write_bytes(response.read())
        print(path, flush=True)
        return True
    except urllib.error.HTTPError as exc:
        if allow_missing and exc.code == 404:
            return False
        raise


def write_current_completed(symbol: str, interval: str, day: date, output_dir: Path) -> None:
    start_ms = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    end_ms = min(int(time.time() * 1000), start_ms + 86_400_000)
    rows = fetch_completed_rows(symbol, interval, start_ms, end_ms)
    path = output_dir / f"{symbol}-{interval}-current.csv"
    if not rows and path.exists():
        print(f"{path} rows=0 preserved_existing", flush=True)
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    print(f"{path} rows={len(rows)}", flush=True)


def write_rest_archive_day(symbol: str, interval: str, day: date, output_dir: Path) -> bool:
    start_ms = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    end_ms = min(int(time.time() * 1000), start_ms + 86_400_000)
    if end_ms <= start_ms:
        return False
    rows = fetch_completed_rows(symbol, interval, start_ms, end_ms)
    if not rows:
        return False
    name = f"{symbol}-{interval}-{day.isoformat()}"
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(HEADER)
    writer.writerows(rows)
    path = output_dir / f"{name}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{name}.csv", buffer.getvalue())
    print(f"{path} rows={len(rows)} source=REST", flush=True)
    return True


def fetch_completed_rows(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    now_ms = int(time.time() * 1000)
    effective_end_ms = min(end_ms, now_ms)
    rows = []
    cursor = start_ms
    while cursor < effective_end_ms:
        query = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": effective_end_ms - 1,
                "limit": 1500,
            }
        )
        request = urllib.request.Request(
            f"{REST_BASE}?{query}", headers={"User-Agent": "mmtick-kline-research/1.0"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read())
        completed = [
            row[:12]
            for row in payload
            if start_ms <= int(row[0]) and int(row[6]) < effective_end_ms
        ]
        rows.extend(completed)
        if not payload:
            break
        next_cursor = int(payload[-1][6]) + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(payload) < 1500:
            break
    return rows


if __name__ == "__main__":
    main()
