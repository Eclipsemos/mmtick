"""Binance public live and historical market-data adapter."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import websockets

from mastermind_tick.models import Bar, Tick

BINANCE_REST = "https://data-api.binance.vision/api/v3"
BINANCE_WS = "wss://data-stream.binance.vision/ws"


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
                trade_count=int(row[8]),
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
                    aggregate_trade_id=int(payload["a"]),
                    first_trade_id=int(payload["f"]),
                    last_trade_id=int(payload["l"]),
                    buyer_is_maker=bool(payload["m"]),
                    event_time_ms=int(payload["E"]),
                )


def build_feed(feed: str, symbol: str) -> MarketFeed:
    if feed == "binance":
        return BinanceFeed(symbol)
    raise ValueError(f"unknown feed: {feed}")
