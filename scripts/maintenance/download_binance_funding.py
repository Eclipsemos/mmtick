#!/usr/bin/env python3
"""Download Binance USD-M funding events for reproducible research."""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

ENDPOINT = "https://fapi.binance.com/fapi/v1/fundingRate"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=datetime.now(UTC).date())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start_ms = int(datetime.combine(args.start, datetime.min.time(), UTC).timestamp() * 1000)
    end_ms = int(datetime.combine(args.end, datetime.max.time(), UTC).timestamp() * 1000)
    cursor = start_ms
    events: dict[int, tuple[str, str]] = {}
    while cursor <= end_ms:
        query = urllib.parse.urlencode(
            {
                "symbol": args.symbol.upper(),
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        request = urllib.request.Request(
            f"{ENDPOINT}?{query}", headers={"User-Agent": "mmtick-funding-research/1.0"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read())
        if not payload:
            break
        for item in payload:
            timestamp_ms = int(item["fundingTime"])
            if start_ms <= timestamp_ms <= end_ms:
                events[timestamp_ms] = (item["fundingRate"], item.get("markPrice", ""))
        last_time = max(int(item["fundingTime"]) for item in payload)
        if len(payload) < 1000 or last_time >= end_ms:
            break
        if last_time < cursor:
            raise RuntimeError("funding endpoint returned non-advancing pages")
        cursor = last_time + 1
        time.sleep(0.05)
        print(f"{args.symbol.upper()} funding events={len(events)}", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp_ms", "rate", "mark_price"))
        for timestamp_ms in sorted(events):
            rate, mark_price = events[timestamp_ms]
            writer.writerow((timestamp_ms, rate, mark_price))
    print(f"{args.output} events={len(events)}", flush=True)


if __name__ == "__main__":
    main()
