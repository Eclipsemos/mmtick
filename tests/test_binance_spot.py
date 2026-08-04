import asyncio
import hashlib
import hmac
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from mastermind_tick.binance_spot import (
    BinanceSpotAPIError,
    BinanceSpotClient,
    SpotSymbolRules,
)

EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "SOXLBUSDT",
            "status": "TRADING",
            "baseAsset": "SOXLB",
            "quoteAsset": "USDT",
            "orderTypes": ["LIMIT", "MARKET"],
            "quoteOrderQtyMarketAllowed": True,
            "filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "100000"},
            ],
        }
    ]
}


def test_symbol_rules_parse_and_floor_quantity() -> None:
    rules = SpotSymbolRules.from_exchange_info(EXCHANGE_INFO, "SOXLBUSDT")

    assert rules.status == "TRADING"
    assert rules.market_order_allowed
    assert rules.quote_order_quantity_allowed
    assert rules.minimum_notional == Decimal("5")
    assert rules.floor_quantity(Decimal("1.2349")) == Decimal("1.234")


def test_signed_request_has_api_key_and_valid_signature(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        captured["key"] = request.headers.get("X-MBX-APIKEY")
        return httpx.Response(200, json={"canTrade": True})

    monkeypatch.setattr("mastermind_tick.binance_spot.time.time", lambda: 1_700_000_000.0)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceSpotClient(
        "https://api.binance.test", "api-key", "secret", client=http
    )

    asyncio.run(client.account())

    url = captured["url"]
    assert isinstance(url, httpx.URL)
    query = parse_qs(url.query.decode())
    signature = query.pop("signature")[0]
    unsigned = "omitZeroBalances=true&recvWindow=5000&timestamp=1700000000000"
    assert signature == hmac.new(b"secret", unsigned.encode(), hashlib.sha256).hexdigest()
    assert captured["key"] == "api-key"
    asyncio.run(http.aclose())


def test_signed_request_requires_credentials() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    client = BinanceSpotClient("https://api.binance.test", client=http)

    with pytest.raises(RuntimeError, match="credentials"):
        asyncio.run(client.account())
    asyncio.run(http.aclose())


def test_api_error_is_structured() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(400, json={"code": -1013, "msg": "Invalid quantity"})
    )
    http = httpx.AsyncClient(transport=transport)
    client = BinanceSpotClient("https://api.binance.test", client=http)

    with pytest.raises(BinanceSpotAPIError) as raised:
        asyncio.run(client.book_ticker("SOXLBUSDT"))
    assert raised.value.code == -1013
    assert raised.value.status_code == 400
    asyncio.run(http.aclose())


def test_test_orders_use_test_endpoint_and_expected_quantity_field() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceSpotClient(
        "https://api.binance.test", "api-key", "secret", client=http
    )

    asyncio.run(
        client.market_buy("SOXLBUSDT", Decimal("5.1"), "buy-id", test=True)
    )
    asyncio.run(
        client.market_sell("SOXLBUSDT", Decimal("0.123"), "sell-id", test=True)
    )

    assert all(request.url.path == "/api/v3/order/test" for request in requests)
    buy = parse_qs(requests[0].url.query.decode())
    sell = parse_qs(requests[1].url.query.decode())
    assert buy["side"] == ["BUY"]
    assert buy["quoteOrderQty"] == ["5.1"]
    assert sell["side"] == ["SELL"]
    assert sell["quantity"] == ["0.123"]
    asyncio.run(http.aclose())
