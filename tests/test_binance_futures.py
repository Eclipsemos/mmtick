import asyncio
import hashlib
import hmac
from urllib.parse import parse_qs

import httpx
import pytest

from mastermind_tick.binance_futures import (
    BinanceFuturesClient,
    BinanceFuturesRateLimitError,
)


def test_time_sync_uses_cache_busting_nonce(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"serverTime": 1_700_000_000_125})

    monkeypatch.setattr(
        "mastermind_tick.binance_futures.time.time", lambda: 1_700_000_000.0
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceFuturesClient("https://fapi.binance.test", client=http)

    offset = asyncio.run(client.sync_time())

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert parse_qs(request.url.query.decode()) == {"nonce": ["1700000000000"]}
    assert offset == 125
    asyncio.run(http.aclose())


def test_signed_request_resyncs_and_retries_once_after_timestamp_rejection(monkeypatch) -> None:
    account_calls = 0
    time_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal account_calls, time_calls
        if request.url.path == "/fapi/v1/time":
            time_calls += 1
            return httpx.Response(200, json={"serverTime": 1_700_000_000_125})
        account_calls += 1
        if account_calls == 1:
            return httpx.Response(
                400,
                json={"code": -1021, "msg": "Timestamp outside recvWindow"},
            )
        query = parse_qs(request.url.query.decode())
        assert query["timestamp"] == ["1700000000125"]
        return httpx.Response(200, json={"canTrade": True})

    monkeypatch.setattr(
        "mastermind_tick.binance_futures.time.time", lambda: 1_700_000_000.0
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceFuturesClient(
        "https://fapi.binance.test", "api-key", "secret", client=http
    )

    result = asyncio.run(client.account())

    assert result == {"canTrade": True}
    assert account_calls == 2
    assert time_calls == 1
    asyncio.run(http.aclose())


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


def test_rate_limit_sets_shared_cooldown_and_blocks_requests_locally(monkeypatch) -> None:
    request_count = 0
    monotonic = 100.0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "12", "X-MBX-USED-WEIGHT-1M": "2399"},
                json={"code": -1003, "msg": "Too many requests"},
            )
        return httpx.Response(200, json={"symbol": "SOXLUSDT", "bidPrice": "100"})

    monkeypatch.setattr(
        "mastermind_tick.binance_futures.time.monotonic", lambda: monotonic
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceFuturesClient("https://fapi.binance.test", client=http)

    with pytest.raises(BinanceFuturesRateLimitError) as first:
        asyncio.run(client.book_ticker("SOXLUSDT"))
    assert first.value.retry_after_seconds == 30
    assert request_count == 1
    assert client.rate_limit_status()["used_weight"]["x-mbx-used-weight-1m"] == 2399

    with pytest.raises(BinanceFuturesRateLimitError) as local:
        asyncio.run(client.book_ticker("SOXLUSDT"))
    assert local.value.message == "Local Binance rate-limit cooldown is active"
    assert request_count == 1

    monotonic = 131.0
    result = asyncio.run(client.book_ticker("SOXLUSDT"))
    assert result["bidPrice"] == "100"
    assert request_count == 2
    asyncio.run(http.aclose())


def test_ip_ban_timestamp_extends_418_cooldown(monkeypatch) -> None:
    monkeypatch.setattr("mastermind_tick.binance_futures.time.time", lambda: 1_700_000_000.0)
    monkeypatch.setattr("mastermind_tick.binance_futures.time.monotonic", lambda: 50.0)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            418,
            json={
                "code": -1003,
                "msg": "IP auto-banned until 1700000300000. Please use websocket streams.",
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BinanceFuturesClient("https://fapi.binance.test", client=http)

    with pytest.raises(BinanceFuturesRateLimitError) as error:
        asyncio.run(client.book_ticker("SOXLUSDT"))

    assert error.value.status_code == 418
    assert error.value.retry_after_seconds == 301
    asyncio.run(http.aclose())
