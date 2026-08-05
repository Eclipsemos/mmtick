import asyncio
import hashlib
import hmac
from urllib.parse import parse_qs

import httpx

from mastermind_tick.binance_futures import BinanceFuturesClient


def test_tradfi_contract_uses_signed_stock_contract_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"msg": "SUCCESS"})

    monkeypatch.setattr(
        "mastermind_tick.binance_futures.time.time", lambda: 1_700_000_000.0
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceFuturesClient(
        "https://fapi.binance.test", "api-key", "secret", client=http
    )

    result = asyncio.run(client.sign_tradfi_perps_contract())

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "POST"
    assert request.url.path == "/fapi/v1/stock/contract"
    assert request.headers["X-MBX-APIKEY"] == "api-key"
    query = parse_qs(request.url.query.decode())
    signature = query.pop("signature")[0]
    unsigned = "recvWindow=5000&timestamp=1700000000000"
    assert signature == hmac.new(b"secret", unsigned.encode(), hashlib.sha256).hexdigest()
    assert result == {"msg": "SUCCESS"}
    asyncio.run(http.aclose())


def test_transfer_history_reads_futures_income_transfers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json=[
                {
                    "incomeType": "TRANSFER",
                    "income": "1614.00000000",
                    "asset": "USDT",
                    "time": 1_700_000_000_000,
                    "tranId": 397312444870,
                }
            ],
        )

    monkeypatch.setattr(
        "mastermind_tick.binance_futures.time.time", lambda: 1_700_000_000.0
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceFuturesClient(
        "https://fapi.binance.test", "api-key", "secret", client=http
    )

    result = asyncio.run(client.transfer_history())

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.url.path == "/fapi/v1/income"
    query = parse_qs(request.url.query.decode())
    assert query["incomeType"] == ["TRANSFER"]
    assert query["limit"] == ["1000"]
    assert result[0]["income"] == "1614.00000000"
    asyncio.run(http.aclose())
