"""Minimal signed Binance USD-M Futures REST client."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any
from urllib.parse import urlencode

import httpx


class BinanceFuturesAPIError(RuntimeError):
    def __init__(self, status_code: int, code: int | None, message: str):
        super().__init__(f"Binance Futures API {status_code}/{code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FuturesSymbolRules:
    symbol: str
    status: str
    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    market_order_allowed: bool

    @classmethod
    def from_exchange_info(
        cls, payload: dict[str, Any], symbol: str
    ) -> FuturesSymbolRules:
        row = next(
            (item for item in payload.get("symbols", []) if item.get("symbol") == symbol),
            None,
        )
        if row is None:
            raise LookupError(f"Binance USD-M Futures does not list {symbol}")
        filters = {item["filterType"]: item for item in row.get("filters", [])}
        lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE") or {}
        notional = filters.get("MIN_NOTIONAL") or {}
        return cls(
            symbol=symbol,
            status=str(row.get("status", "UNKNOWN")),
            quantity_step=Decimal(str(lot.get("stepSize", "0"))),
            minimum_quantity=Decimal(str(lot.get("minQty", "0"))),
            minimum_notional=Decimal(str(notional.get("notional", "0"))),
            market_order_allowed="MARKET" in row.get("orderTypes", []),
        )

    def floor_quantity(self, value: Decimal) -> Decimal:
        if self.quantity_step <= 0:
            return value
        units = (value / self.quantity_step).to_integral_value(rounding=ROUND_DOWN)
        return units * self.quantity_step


class BinanceFuturesClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        spot_api_base_url: str = "https://api.binance.com",
        recv_window_ms: int = 5000,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.spot_api_base_url = spot_api_base_url.rstrip("/")
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

    async def sync_time(self) -> int:
        before = int(time.time() * 1000)
        payload = await self._request("GET", "/fapi/v1/time")
        after = int(time.time() * 1000)
        self.time_offset_ms = int(payload["serverTime"]) - (before + after) // 2
        return self.time_offset_ms

    async def symbol_rules(self, symbol: str) -> FuturesSymbolRules:
        payload = await self._request(
            "GET", "/fapi/v1/exchangeInfo", {"symbol": symbol}
        )
        return FuturesSymbolRules.from_exchange_info(payload, symbol)

    async def book_ticker(self, symbol: str) -> dict[str, Any]:
        return await self._request(
            "GET", "/fapi/v1/ticker/bookTicker", {"symbol": symbol}
        )

    async def account(self) -> dict[str, Any]:
        return await self._signed_request("GET", "/fapi/v2/account")

    async def position_risk(self, symbol: str) -> list[dict[str, Any]]:
        payload = await self._signed_request(
            "GET", "/fapi/v2/positionRisk", {"symbol": symbol}
        )
        return list(payload)

    async def position_mode(self) -> dict[str, Any]:
        return await self._signed_request("GET", "/fapi/v1/positionSide/dual")

    async def multi_assets_mode(self) -> dict[str, Any]:
        return await self._signed_request("GET", "/fapi/v1/multiAssetsMargin")

    async def api_restrictions(self) -> dict[str, Any]:
        return await self._signed_request(
            "GET", "/sapi/v1/account/apiRestrictions", base_url=self.spot_api_base_url
        )

    async def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        payload = await self._signed_request("GET", "/fapi/v1/openOrders", params)
        return list(payload)

    async def query_order(
        self, symbol: str, *, client_order_id: str
    ) -> dict[str, Any]:
        return await self._signed_request(
            "GET",
            "/fapi/v1/order",
            {"symbol": symbol, "origClientOrderId": client_order_id},
        )

    async def user_trades(
        self, symbol: str, *, order_id: int | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol, "limit": 1000}
        if order_id is not None:
            params["orderId"] = order_id
        payload = await self._signed_request("GET", "/fapi/v1/userTrades", params)
        return list(payload)

    async def income_history(self, symbol: str) -> list[dict[str, Any]]:
        payload = await self._signed_request(
            "GET",
            "/fapi/v1/income",
            {"symbol": symbol, "incomeType": "FUNDING_FEE", "limit": 1000},
        )
        return list(payload)

    async def market_order(
        self,
        *,
        symbol: str,
        side: str,
        position_side: str,
        quantity: Decimal,
        client_order_id: str,
        test: bool = False,
    ) -> dict[str, Any]:
        path = "/fapi/v1/order/test" if test else "/fapi/v1/order"
        return await self._signed_request(
            "POST",
            path,
            {
                "symbol": symbol,
                "side": side,
                "positionSide": position_side,
                "type": "MARKET",
                "quantity": format(quantity, "f"),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "RESULT",
            },
        )

    async def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return await self._signed_request(
            "DELETE",
            "/fapi/v1/order",
            {"symbol": symbol, "origClientOrderId": client_order_id},
        )

    async def _signed_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        base_url: str | None = None,
    ) -> Any:
        if not self.has_credentials:
            raise RuntimeError("Binance Futures API credentials are not configured")
        signed = dict(params or {})
        signed["recvWindow"] = self.recv_window_ms
        signed["timestamp"] = int(time.time() * 1000) + self.time_offset_ms
        query = urlencode([(key, str(value)) for key, value in signed.items()])
        signature = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return await self._request(
            method,
            f"{path}?{query}&signature={signature}",
            api_key_required=True,
            base_url=base_url,
        )

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        api_key_required: bool = False,
        base_url: str | None = None,
    ) -> Any:
        headers = {
            "X-MBX-APIKEY": self.api_key
        } if api_key_required and self.api_key else None
        response = await self._client.request(
            method,
            f"{base_url or self.base_url}{path}",
            params=params,
            headers=headers,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"msg": response.text}
        if response.is_error:
            raise BinanceFuturesAPIError(
                response.status_code,
                payload.get("code") if isinstance(payload, dict) else None,
                str(payload.get("msg", payload)) if isinstance(payload, dict) else str(payload),
            )
        return payload
