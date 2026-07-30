"""Public live and historical market-data adapters for SOXL and SOXLB."""

from __future__ import annotations

import asyncio
import json
import os
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import httpx
import pandas as pd
import pyarrow.parquet as pq
import websockets
from curl_cffi.requests import AsyncSession

from mastermind_tick.models import Bar, Tick

BINANCE_REST = "https://data-api.binance.vision/api/v3"
BINANCE_WS = "wss://data-stream.binance.vision/ws"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0 Safari/537.36 mastermind-tick/0.1"
)


class MarketFeed(ABC):
    source_name: str

    @abstractmethod
    async def history(self, limit: int) -> list[Bar]:
        raise NotImplementedError

    @abstractmethod
    async def ticks(self) -> AsyncIterator[Tick]:
        raise NotImplementedError


class BinanceFeed(MarketFeed):
    source_name = "binance_public"

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self._last_event_id: str | None = None

    async def history(self, limit: int) -> list[Bar]:
        params = {"symbol": self.symbol, "interval": "15m", "limit": min(limit, 1000)}
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.get(f"{BINANCE_REST}/klines", params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"Binance history error: {payload}")
        now_ms = int(time.time() * 1000)
        return [
            Bar(
                start_ms=int(row[0]),
                end_ms=int(row[6]),
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                volume=Decimal(row[5]),
            )
            for row in payload
            if int(row[6]) < now_ms
        ]

    async def ticks(self) -> AsyncIterator[Tick]:
        uri = f"{BINANCE_WS}/{self.symbol.lower()}@aggTrade"
        async with websockets.connect(
            uri,
            proxy=None,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as websocket:
            async for message in websocket:
                payload = json.loads(message)
                event_id = str(payload["a"])
                if event_id == self._last_event_id:
                    continue
                self._last_event_id = event_id
                yield Tick(
                    event_id=f"binance:{self.symbol}:{event_id}",
                    timestamp_ms=int(payload["T"]),
                    price=Decimal(payload["p"]),
                    quantity=Decimal(payload["q"]),
                    source=self.source_name,
                )


class YahooFeed(MarketFeed):
    source_name = "yahoo_snapshot"

    def __init__(self, symbol: str, alpha_warehouse: Path | None = None):
        self.symbol = symbol.upper()
        self.alpha_warehouse = alpha_warehouse
        self._last_event_id: str | None = None

    async def history(self, limit: int) -> list[Bar]:
        local = await asyncio.to_thread(self._local_history, limit)
        try:
            remote = await self._remote_history(limit)
        except Exception:
            if not local:
                raise
            remote = []
        merged = {bar.start_ms: bar for bar in [*local, *remote]}
        return sorted(merged.values(), key=lambda bar: bar.start_ms)[-limit:]

    async def ticks(self) -> AsyncIterator[Tick]:
        while True:
            payload = await self._chart(interval="1m", range_value="1d")
            result = payload["chart"]["result"][0]
            timestamps = result.get("timestamp") or []
            closes = result["indicators"]["quote"][0].get("close") or []
            for index in range(len(timestamps) - 1, -1, -1):
                if closes[index] is None:
                    continue
                timestamp_ms = int(timestamps[index]) * 1000
                event_id = f"yahoo:{self.symbol}:{timestamp_ms}:{closes[index]}"
                if event_id != self._last_event_id:
                    self._last_event_id = event_id
                    yield Tick(
                        event_id=event_id,
                        timestamp_ms=timestamp_ms,
                        price=Decimal(str(closes[index])),
                        quantity=Decimal("0"),
                        source=self.source_name,
                    )
                break
            await asyncio.sleep(15)

    async def _remote_history(self, limit: int) -> list[Bar]:
        payload = await self._chart(interval="15m", range_value="60d")
        result = payload["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
        now_ms = int(time.time() * 1000)
        bars: list[Bar] = []
        for index, timestamp in enumerate(timestamps):
            values = [quote[name][index] for name in ("open", "high", "low", "close")]
            if any(value is None for value in values):
                continue
            start_ms = int(timestamp) * 1000
            end_ms = start_ms + 15 * 60_000 - 1
            if end_ms >= now_ms:
                continue
            bars.append(
                Bar(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    open=Decimal(str(values[0])),
                    high=Decimal(str(values[1])),
                    low=Decimal(str(values[2])),
                    close=Decimal(str(values[3])),
                    volume=Decimal(str((quote.get("volume") or [0] * len(timestamps))[index] or 0)),
                )
            )
        return bars[-limit:]

    async def _chart(self, *, interval: str, range_value: str) -> dict:
        params = {
            "interval": interval,
            "range": range_value,
            "includePrePost": "false",
            "events": "div,splits",
        }
        headers = {"User-Agent": USER_AGENT}
        async with AsyncSession(impersonate="chrome", headers=headers) as client:
            response = await client.get(
                f"{YAHOO_CHART}/{self.symbol}",
                params=params,
                proxy=_normalized_https_proxy(),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        error = payload.get("chart", {}).get("error")
        if error:
            raise RuntimeError(f"Yahoo history error: {error}")
        return payload

    def _local_history(self, limit: int) -> list[Bar]:
        if not self.alpha_warehouse:
            return []
        root = self.alpha_warehouse / f"symbol={self.symbol}" / "timeframe=5m"
        files = sorted(root.glob("year=*/month=*/data.parquet"))
        if not files:
            return []
        frames = []
        for path in files[-4:]:
            table = pq.read_table(
                path,
                columns=["timestamp", "adj_open", "adj_high", "adj_low", "adj_close", "volume"],
            )
            frames.append(table.to_pandas())
        frame = pd.concat(frames, ignore_index=True).sort_values("timestamp")
        frame = frame.drop_duplicates("timestamp", keep="last").set_index("timestamp")
        aggregated = frame.resample("15min", origin="epoch").agg(
            {
                "adj_open": "first",
                "adj_high": "max",
                "adj_low": "min",
                "adj_close": "last",
                "volume": "sum",
            }
        )
        aggregated = aggregated.dropna(subset=["adj_open", "adj_high", "adj_low", "adj_close"])
        bars = []
        for timestamp, row in aggregated.tail(limit).iterrows():
            start_ms = int(timestamp.timestamp() * 1000)
            bars.append(
                Bar(
                    start_ms=start_ms,
                    end_ms=start_ms + 15 * 60_000 - 1,
                    open=Decimal(str(row["adj_open"])),
                    high=Decimal(str(row["adj_high"])),
                    low=Decimal(str(row["adj_low"])),
                    close=Decimal(str(row["adj_close"])),
                    volume=Decimal(str(row["volume"])),
                )
            )
        return bars


def build_feed(feed: str, symbol: str, alpha_warehouse: Path) -> MarketFeed:
    if feed == "binance":
        return BinanceFeed(symbol)
    if feed == "yahoo":
        return YahooFeed(symbol, alpha_warehouse)
    raise ValueError(f"unknown feed: {feed}")


def _normalized_https_proxy() -> str | None:
    value = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    if value and "://" not in value:
        return f"http://{value}"
    return value
