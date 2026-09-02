#!/usr/bin/env python3
"""Download Binance USD-M kline archives and a completed-bar current-day CSV."""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
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
    for year, month in months(args.start, args.end):
        month_start = date(year, month, 1)
        if month_start >= current_month:
            continue
        name = f"{symbol}-{args.interval}-{year:04d}-{month:02d}.zip"
        download(
            f"{ARCHIVE_BASE}/monthly/klines/{symbol}/{args.interval}/{name}",
            args.output_dir / name,
        )

    day = max(args.start, current_month)
    while day < args.end:
        name = f"{symbol}-{args.interval}-{day.isoformat()}.zip"
        download(
            f"{ARCHIVE_BASE}/daily/klines/{symbol}/{args.interval}/{name}",
            args.output_dir / name,
            allow_missing=True,
        )
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


def download(url: str, path: Path, *, allow_missing: bool = False) -> None:
    if path.exists():
        return
    request = urllib.request.Request(url, headers={"User-Agent": "mmtick-kline-research/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            path.write_bytes(response.read())
        print(path, flush=True)
    except urllib.error.HTTPError as exc:
        if allow_missing and exc.code == 404:
            return
        raise


def write_current_completed(symbol: str, interval: str, day: date, output_dir: Path) -> None:
    start_ms = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    now_ms = int(time.time() * 1000)
    rows = []
    cursor = start_ms
    while cursor < now_ms:
        query = urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "startTime": cursor, "limit": 1500}
        )
        request = urllib.request.Request(
            f"{REST_BASE}?{query}", headers={"User-Agent": "mmtick-kline-research/1.0"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read())
        completed = [row[:12] for row in payload if int(row[6]) < now_ms]
        rows.extend(completed)
        if not payload:
            break
        next_cursor = int(payload[-1][6]) + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(payload) < 1500:
            break
    path = output_dir / f"{symbol}-{interval}-current.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    print(f"{path} rows={len(rows)}", flush=True)


if __name__ == "__main__":
    main()
