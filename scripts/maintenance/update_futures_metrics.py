#!/usr/bin/env python3
"""Download missing daily Binance USD-M futures market-metric archives."""

from __future__ import annotations

import argparse
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

BASE_URL = "https://data.binance.vision/data/futures/um/daily/metrics"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2021, 1, 1))
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=datetime.now(UTC).date() - timedelta(days=1),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    if args.start > args.end or args.workers < 1:
        parser.error("invalid date range or worker count")
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    tasks = [
        (symbol, day, args.output_dir / symbol / f"{symbol}-metrics-{day.isoformat()}.zip")
        for symbol in symbols
        for day in _dates(args.start, args.end)
    ]
    missing = [(symbol, day, path) for symbol, day, path in tasks if not path.is_file()]
    print(f"metric archives: {len(tasks) - len(missing):,} cached, {len(missing):,} pending")
    downloaded = unavailable = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_download, symbol, day, path): (symbol, day)
            for symbol, day, path in missing
        }
        for index, future in enumerate(as_completed(futures), start=1):
            status = future.result()
            downloaded += status == "downloaded"
            unavailable += status == "unavailable"
            if index % 250 == 0 or index == len(futures):
                print(
                    f"processed {index:,}/{len(futures):,}: "
                    f"{downloaded:,} downloaded, {unavailable:,} unavailable",
                    flush=True,
                )


def _download(symbol: str, day: date, path: Path) -> str:
    name = f"{symbol}-metrics-{day.isoformat()}.zip"
    url = f"{BASE_URL}/{symbol}/{name}"
    request = urllib.request.Request(url, headers={"User-Agent": "mmtick-research/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return "unavailable"
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".zip.part")
    temporary.write_bytes(payload)
    temporary.replace(path)
    return "downloaded"


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


if __name__ == "__main__":
    main()
