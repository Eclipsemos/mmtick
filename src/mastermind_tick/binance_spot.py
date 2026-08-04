"""Minimal signed Binance Spot REST client for the live SOXLB execution path."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any
from urllib.parse import urlencode

import httpx


class BinanceSpotAPIError(RuntimeError):
    def __init__(self, status_code: int, code: int | None, message: str):
        super().__init__(f"Binance Spot API {status_code}/{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SpotSymbolRules:
    symbol: str
    status: str
    base_asset: str
    quote_asset: str
    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    maximum_notional: Decimal | None
    market_order_allowed: bool
    quote_order_quantity_allowed: bool

    @classmethod
    def from_exchange_info(cls, payload: dict[str, Any], symbol: str) -> SpotSymbolRules:
        row = next(
            (item for item in payload.get("symbols", []) if item.get("symbol") == symbol),
            None,
        )
        if row is None:
            raise LookupError(f"Binance Spot does not list {symbol}")
        filters = {item["filterType"]: item for item in row.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        maximum = notional.get("maxNotional")
        return cls(
            symbol=symbol,
            status=str(row.get("status", "UNKNOWN")),
            base_asset=str(row["baseAsset"]),
            quote_asset=str(row["quoteAsset"]),
            quantity_step=Decimal(str(lot.get("stepSize", "0"))),
            minimum_quantity=Decimal(str(lot.get("minQty", "0"))),
            minimum_notional=Decimal(str(notional.get("minNotional", "0"))),
            maximum_notional=Decimal(str(maximum)) if maximum is not None else None,
            market_order_allowed="MARKET" in row.get("orderTypes", []),
            quote_order_quantity_allowed=bool(row.get("quoteOrderQtyMarketAllowed", False)),
        )

    def floor_quantity(self, value: Decimal) -> Decimal:
        if self.quantity_step <= 0:
            return value
        units = (value / self.quantity_step).to_integral_value(rounding=ROUND_DOWN)
        return units * self.quantity_step


class BinanceSpotClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        recv_window_ms: int = 5000,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._api_secret = api_secret
        self.recv_window_ms = recv_window_ms
        self.time_offset_ms = 0
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=20, trust_env=True)

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self._api_secret)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def server_time(self) -> int:
        payload = await self._request("GET", "/api/v3/time")
        return int(payload["serverTime"])

    async def sync_time(self) -> int:
        before = int(time.time() * 1000)
        server_ms = await self.server_time()
        after = int(time.time() * 1000)
        self.time_offset_ms = server_ms - (before + after) // 2
        return self.time_offset_ms

    async def exchange_info(self, symbol: str) -> dict[str, Any]:
        return await self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})

    async def symbol_rules(self, symbol: str) -> SpotSymbolRules:
        return SpotSymbolRules.from_exchange_info(await self.exchange_info(symbol), symbol)

    async def book_ticker(self, symbol: str) -> dict[str, Any]:
        return await self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": symbol})

    async def account(self) -> dict[str, Any]:
        return await self._signed_request("GET", "/api/v3/account", {"omitZeroBalances": "true"})

    async def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        payload = await self._signed_request("GET", "/api/v3/openOrders", {"symbol": symbol})
        return list(payload)

    async def query_order(
        self,
        symbol: str,
        *,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._signed_request(
            "GET",
            "/api/v3/order",
            {"symbol": symbol, "origClientOrderId": client_order_id},
        )

    async def my_trades(
        self,
        symbol: str,
        *,
        order_id: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol, "limit": 1000}
        if order_id is not None:
            params["orderId"] = order_id
        payload = await self._signed_request("GET", "/api/v3/myTrades", params)
        return list(payload)

    async def market_buy(
        self,
        symbol: str,
        quote_order_quantity: Decimal,
        client_order_id: str,
        *,
        test: bool = False,
    ) -> dict[str, Any]:
        return await self._order(
            {
                "symbol": symbol,
                "side": "BUY",
                "type": "MARKET",
                "quoteOrderQty": _decimal_text(quote_order_quantity),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "FULL",
            },
            test=test,
        )

    async def market_sell(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str,
        *,
        test: bool = False,
    ) -> dict[str, Any]:
        return await self._order(
            {
                "symbol": symbol,
                "side": "SELL",
                "type": "MARKET",
                "quantity": _decimal_text(quantity),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "FULL",
            },
            test=test,
        )

    async def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return await self._signed_request(
            "DELETE",
            "/api/v3/order",
            {"symbol": symbol, "origClientOrderId": client_order_id},
        )

    async def _order(self, params: dict[str, Any], *, test: bool) -> dict[str, Any]:
        path = "/api/v3/order/test" if test else "/api/v3/order"
        return await self._signed_request("POST", path, params)

    async def _signed_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not self.has_credentials:
            raise RuntimeError("Binance Spot API credentials are not configured")
        signed = dict(params or {})
        signed["recvWindow"] = self.recv_window_ms
        signed["timestamp"] = int(time.time() * 1000) + self.time_offset_ms
        query = urlencode([(key, str(value)) for key, value in signed.items()])
        signature = hmac.new(
            self._api_secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()
        return await self._request(
            method,
            f"{path}?{query}&signature={signature}",
            api_key_required=True,
        )

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        api_key_required: bool = False,
    ) -> Any:
        headers = {"X-MBX-APIKEY": self.api_key} if api_key_required and self.api_key else None
        response = await self._client.request(
            method,
            f"{self.base_url}{path}",
            params=params,
            headers=headers,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"msg": response.text}
        if response.is_error:
            raise BinanceSpotAPIError(
                response.status_code,
                payload.get("code") if isinstance(payload, dict) else None,
                str(payload.get("msg", payload)) if isinstance(payload, dict) else str(payload),
            )
        return payload


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")
