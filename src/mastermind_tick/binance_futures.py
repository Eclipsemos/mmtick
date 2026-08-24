"""Minimal signed Binance USD-M Futures REST client."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
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


class BinanceFuturesRateLimitError(BinanceFuturesAPIError):
    """A shared IP limit or ban is active and requests must stop until retry time."""

    def __init__(
        self,
        status_code: int,
        code: int | None,
        message: str,
        retry_after_seconds: float,
    ):
        super().__init__(status_code, code, message)
        self.retry_after_seconds = max(retry_after_seconds, 0.0)


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
        self._time_sync_generation = 0
        self._time_sync_lock = asyncio.Lock()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=20, trust_env=True)
        self._rate_limit_until = 0.0
        self._used_weight_headers: dict[str, int] = {}

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self._api_secret)

    @property
    def rate_limit_cooldown_seconds(self) -> float:
        return max(self._rate_limit_until - time.monotonic(), 0.0)

    def rate_limit_status(self) -> dict[str, Any]:
        return {
            "cooldown_seconds": self.rate_limit_cooldown_seconds,
            "used_weight": dict(self._used_weight_headers),
        }

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def sync_time(self) -> int:
        before = int(time.time() * 1000)
        # A per-request nonce prevents transparent HTTP caches from serving a stale clock.
        payload = await self._request("GET", "/fapi/v1/time", {"nonce": before})
        after = int(time.time() * 1000)
        self.time_offset_ms = int(payload["serverTime"]) - (before + after) // 2
        self._time_sync_generation += 1
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

    async def sign_tradfi_perps_contract(self) -> dict[str, Any]:
        """Accept Binance's TradFi-Perps agreement for this Futures account."""
        return await self._signed_request("POST", "/fapi/v1/stock/contract")

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

    async def transfer_history(self) -> list[dict[str, Any]]:
        payload = await self._signed_request(
            "GET",
            "/fapi/v1/income",
            {"incomeType": "TRANSFER", "limit": 1000},
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
        generation = self._time_sync_generation
        try:
            return await self._signed_request_once(method, path, params, base_url=base_url)
        except BinanceFuturesAPIError as exc:
            if exc.code != -1021:
                raise
        # Binance rejects stale timestamps before evaluating the operation. Re-sync once and
        # retry with a fresh signature; the generation gate prevents a concurrent reconciliation
        # batch from issuing one time request per failed endpoint.
        async with self._time_sync_lock:
            if self._time_sync_generation == generation:
                await self.sync_time()
        return await self._signed_request_once(method, path, params, base_url=base_url)

    async def _signed_request_once(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        base_url: str | None = None,
    ) -> Any:
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
        cooldown = self.rate_limit_cooldown_seconds
        if cooldown > 0:
            raise BinanceFuturesRateLimitError(
                429,
                -1003,
                "Local Binance rate-limit cooldown is active",
                cooldown,
            )
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
        self._record_weight_headers(response)
        if response.is_error:
            code = payload.get("code") if isinstance(payload, dict) else None
            message = (
                str(payload.get("msg", payload)) if isinstance(payload, dict) else str(payload)
            )
            if response.status_code in {418, 429}:
                retry_after = _retry_after_seconds(response, message)
                self._rate_limit_until = max(
                    self._rate_limit_until,
                    time.monotonic() + retry_after,
                )
                raise BinanceFuturesRateLimitError(
                    response.status_code,
                    code,
                    message,
                    retry_after,
                )
            raise BinanceFuturesAPIError(response.status_code, code, message)
        return payload

    def _record_weight_headers(self, response: httpx.Response) -> None:
        for name, value in response.headers.items():
            normalized = name.lower()
            if "used-weight" not in normalized:
                continue
            try:
                self._used_weight_headers[normalized] = int(value)
            except ValueError:
                continue


def _retry_after_seconds(response: httpx.Response, message: str) -> float:
    """Return a conservative cooldown from Retry-After or Binance's ban timestamp."""
    candidates: list[float] = []
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            candidates.append(float(retry_after))
        except ValueError:
            pass
    match = re.search(r"banned until\s+(\d{10,16})", message, flags=re.IGNORECASE)
    if match:
        ban_timestamp = int(match.group(1))
        ban_seconds = ban_timestamp / 1000 if ban_timestamp > 10_000_000_000 else ban_timestamp
        candidates.append(ban_seconds - time.time() + 1.0)
    default = 120.0 if response.status_code == 418 else 30.0
    return max([default, *candidates, 1.0])
