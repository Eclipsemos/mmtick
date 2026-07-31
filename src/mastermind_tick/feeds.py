"""Binance Spot and USD-M Futures public market-data adapters."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import websockets

from mastermind_tick.models import Bar, FundingRate, Tick

BINANCE_REST = "https://data-api.binance.vision/api/v3"
BINANCE_WS = "wss://data-stream.binance.vision/ws"
BINANCE_FUTURES_REST = "https://fapi.binance.com/fapi/v1"
BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws"
FUTURES_TICK_BUCKET_MS = 250


class MarketFeed(ABC):
    source_name: str

    @property
    def kline_source_name(self) -> str:
        return f"{self.source_name}_kline_rest"

    @property
    def warmup_current_bar(self) -> Bar | None:
        return None

    @property
    def market_state(self) -> dict[str, str | int | None]:
        return {}

    async def funding_rates(self, start_ms: int, end_ms: int) -> list[FundingRate]:
        return []

    async def closed_bars(self) -> AsyncIterator[Bar]:
        if False:
            yield Bar(0, 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))

    async def official_bars(self, start_ms: int, end_ms: int) -> list[Bar]:
        raise NotImplementedError

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
        self._warmup_current_bar: Bar | None = None
        self._last_closed_kline_start: int | None = None

    @property
    def warmup_current_bar(self) -> Bar | None:
        return self._warmup_current_bar

    async def history(self, limit: int) -> list[Bar]:
        params = {"symbol": self.symbol, "interval": "15m", "limit": min(limit, 1000)}
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.get(f"{BINANCE_REST}/klines", params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"Binance history error: {payload}")
        bars = [
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
        ]
        now_ms = int(time.time() * 1000)
        self._warmup_current_bar = next(
            (bar for bar in reversed(bars) if bar.start_ms <= now_ms <= bar.end_ms),
            None,
        )
        return [bar for bar in bars if bar.end_ms < now_ms]

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

    async def closed_bars(self) -> AsyncIterator[Bar]:
        uri = f"{BINANCE_WS}/{self.symbol.lower()}@kline_15m"
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
                kline = payload.get("k", {})
                if not kline.get("x"):
                    continue
                bar = _bar_from_stream_kline(kline)
                if bar.start_ms == self._last_closed_kline_start:
                    continue
                self._last_closed_kline_start = bar.start_ms
                yield bar

    async def official_bars(self, start_ms: int, end_ms: int) -> list[Bar]:
        return await _fetch_klines(
            f"{BINANCE_REST}/klines",
            self.symbol,
            start_ms,
            end_ms,
            trust_env=False,
        )


class BinanceFuturesFeed(MarketFeed):
    source_name = "binance_futures"

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self._last_event_id: str | None = None
        self._warmup_current_bar: Bar | None = None
        self._last_closed_kline_start: int | None = None
        self._market_state: dict[str, str | int | None] = {
            "mark_price": None,
            "index_price": None,
            "funding_rate": None,
            "next_funding_time_ms": None,
            "updated_at_ms": None,
        }

    @property
    def warmup_current_bar(self) -> Bar | None:
        return self._warmup_current_bar

    @property
    def market_state(self) -> dict[str, str | int | None]:
        return dict(self._market_state)

    async def history(self, limit: int) -> list[Bar]:
        params = {"symbol": self.symbol, "interval": "15m", "limit": min(limit, 1000)}
        async with httpx.AsyncClient(timeout=20, trust_env=True) as client:
            bars_response, premium_response = await asyncio.gather(
                client.get(f"{BINANCE_FUTURES_REST}/klines", params=params),
                client.get(
                    f"{BINANCE_FUTURES_REST}/premiumIndex",
                    params={"symbol": self.symbol},
                ),
            )
            bars_response.raise_for_status()
            premium_response.raise_for_status()
            payload = bars_response.json()
            self._update_market_state(premium_response.json())
        if not isinstance(payload, list):
            raise RuntimeError(f"Binance Futures history error: {payload}")
        bars = [
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
        ]
        now_ms = int(time.time() * 1000)
        self._warmup_current_bar = next(
            (bar for bar in reversed(bars) if bar.start_ms <= now_ms <= bar.end_ms),
            None,
        )
        return [bar for bar in bars if bar.end_ms < now_ms]

    async def ticks(self) -> AsyncIterator[Tick]:
        uri = f"{BINANCE_FUTURES_WS}/{self.symbol.lower()}@trade"
        premium_task = asyncio.create_task(self._poll_premium_index())
        bucket: dict[str, object] | None = None
        try:
            async with websockets.connect(
                uri,
                proxy=_websocket_proxy(),
                open_timeout=15,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
            ) as websocket:
                async for message in websocket:
                    payload = json.loads(message)
                    event_id = str(payload["t"])
                    if event_id == self._last_event_id:
                        continue
                    self._last_event_id = event_id
                    if Decimal(payload["p"]) <= 0 or Decimal(payload["q"]) <= 0:
                        continue
                    trade_time = int(payload["T"])
                    bucket_id = trade_time // FUTURES_TICK_BUCKET_MS
                    if bucket is not None and bucket["bucket_id"] != bucket_id:
                        yield self._bucket_tick(bucket)
                        bucket = None
                    bucket = _append_trade(bucket, bucket_id, payload)
        finally:
            premium_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await premium_task

    async def closed_bars(self) -> AsyncIterator[Bar]:
        uri = f"{BINANCE_FUTURES_WS}/{self.symbol.lower()}@kline_15m"
        async with websockets.connect(
            uri,
            proxy=_websocket_proxy(),
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as websocket:
            async for message in websocket:
                payload = json.loads(message)
                kline = payload.get("k", {})
                if not kline.get("x"):
                    continue
                bar = _bar_from_stream_kline(kline)
                if bar.start_ms == self._last_closed_kline_start:
                    continue
                self._last_closed_kline_start = bar.start_ms
                yield bar

    async def official_bars(self, start_ms: int, end_ms: int) -> list[Bar]:
        return await _fetch_klines(
            f"{BINANCE_FUTURES_REST}/klines",
            self.symbol,
            start_ms,
            end_ms,
            trust_env=True,
        )

    async def funding_rates(self, start_ms: int, end_ms: int) -> list[FundingRate]:
        if end_ms <= start_ms:
            return []
        params = {
            "symbol": self.symbol,
            "startTime": start_ms + 1,
            "endTime": end_ms,
            "limit": 1000,
        }
        async with httpx.AsyncClient(timeout=20, trust_env=True) as client:
            response = await client.get(f"{BINANCE_FUTURES_REST}/fundingRate", params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"Binance Futures funding error: {payload}")
        return [
            FundingRate(
                timestamp_ms=int(item["fundingTime"]),
                rate=Decimal(item["fundingRate"]),
                mark_price=Decimal(item["markPrice"]),
            )
            for item in payload
        ]

    async def _poll_premium_index(self) -> None:
        async with httpx.AsyncClient(timeout=10, trust_env=True) as client:
            while True:
                try:
                    response = await client.get(
                        f"{BINANCE_FUTURES_REST}/premiumIndex",
                        params={"symbol": self.symbol},
                    )
                    response.raise_for_status()
                    self._update_market_state(response.json())
                except (httpx.HTTPError, ValueError, TypeError):
                    pass
                await asyncio.sleep(5)

    def _update_market_state(self, payload: dict) -> None:
        self._market_state = {
            "mark_price": payload.get("markPrice"),
            "index_price": payload.get("indexPrice"),
            "funding_rate": payload.get("lastFundingRate"),
            "next_funding_time_ms": payload.get("nextFundingTime"),
            "updated_at_ms": payload.get("time"),
        }

    def _bucket_tick(self, bucket: dict[str, object]) -> Tick:
        return Tick(
            event_id=(
                f"binance-futures:{self.symbol}:"
                f"{bucket['first_trade_id']}-{bucket['last_trade_id']}"
            ),
            timestamp_ms=int(bucket["timestamp_ms"]),
            price=Decimal(str(bucket["price"])),
            quantity=Decimal(str(bucket["quantity"])),
            source=self.source_name,
            first_trade_id=int(bucket["first_trade_id"]),
            last_trade_id=int(bucket["last_trade_id"]),
            buyer_is_maker=bucket["buyer_is_maker"],
            event_time_ms=int(bucket["event_time_ms"]),
            mark_price=_decimal_or_none(self._market_state["mark_price"]),
            index_price=_decimal_or_none(self._market_state["index_price"]),
            funding_rate=_decimal_or_none(self._market_state["funding_rate"]),
            next_funding_time_ms=_int_or_none(self._market_state["next_funding_time_ms"]),
            open_price=Decimal(str(bucket["open_price"])),
            high_price=Decimal(str(bucket["high_price"])),
            low_price=Decimal(str(bucket["low_price"])),
            notional=Decimal(str(bucket["notional"])),
        )


def build_feed(feed: str, symbol: str) -> MarketFeed:
    if feed == "binance":
        return BinanceFeed(symbol)
    if feed == "binance_futures":
        return BinanceFuturesFeed(symbol)
    raise ValueError(f"unknown feed: {feed}")


def _decimal_or_none(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _int_or_none(value: object) -> int | None:
    return int(value) if value is not None else None


def _websocket_proxy() -> str | None:
    value = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    if not value:
        return None
    return value if "://" in value else f"http://{value}"


def _append_trade(
    bucket: dict[str, object] | None,
    bucket_id: int,
    payload: dict,
) -> dict[str, object]:
    price = Decimal(payload["p"])
    quantity = Decimal(payload["q"])
    maker = bool(payload["m"])
    trade_id = int(payload["t"])
    if bucket is None:
        return {
            "bucket_id": bucket_id,
            "timestamp_ms": int(payload["T"]),
            "event_time_ms": int(payload["E"]),
            "price": price,
            "open_price": price,
            "high_price": price,
            "low_price": price,
            "quantity": quantity,
            "notional": price * quantity,
            "first_trade_id": trade_id,
            "last_trade_id": trade_id,
            "buyer_is_maker": maker,
        }
    bucket["timestamp_ms"] = int(payload["T"])
    bucket["event_time_ms"] = int(payload["E"])
    bucket["price"] = price
    bucket["high_price"] = max(Decimal(str(bucket["high_price"])), price)
    bucket["low_price"] = min(Decimal(str(bucket["low_price"])), price)
    bucket["quantity"] = Decimal(str(bucket["quantity"])) + quantity
    bucket["notional"] = Decimal(str(bucket["notional"])) + price * quantity
    bucket["last_trade_id"] = trade_id
    if bucket["buyer_is_maker"] != maker:
        bucket["buyer_is_maker"] = None
    return bucket


def _bar_from_stream_kline(value: dict) -> Bar:
    return Bar(
        start_ms=int(value["t"]),
        end_ms=int(value["T"]),
        open=Decimal(value["o"]),
        high=Decimal(value["h"]),
        low=Decimal(value["l"]),
        close=Decimal(value["c"]),
        volume=Decimal(value["v"]),
        trade_count=int(value["n"]),
    )


async def _fetch_klines(
    url: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
    *,
    trust_env: bool,
) -> list[Bar]:
    if end_ms < start_ms:
        return []
    params = {
        "symbol": symbol,
        "interval": "15m",
        "startTime": start_ms,
        "endTime": end_ms + 15 * 60_000 - 1,
        "limit": 1000,
    }
    async with httpx.AsyncClient(timeout=20, trust_env=trust_env) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Binance kline verification error: {payload}")
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
        if start_ms <= int(row[0]) <= end_ms
    ]
