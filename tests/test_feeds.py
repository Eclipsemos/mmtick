import asyncio
from decimal import Decimal

import httpx

from mastermind_tick import feeds
from mastermind_tick.feeds import (
    BinanceFuturesFeed,
    _append_trade,
    _bar_from_stream_kline,
    _fetch_klines,
)


def test_futures_raw_trades_are_bucketed_without_losing_volume_or_range() -> None:
    first = {
        "E": 1_001,
        "T": 1_000,
        "t": 10,
        "p": "100",
        "q": "2",
        "m": True,
    }
    second = {
        "E": 1_102,
        "T": 1_100,
        "t": 11,
        "p": "101",
        "q": "3",
        "m": False,
    }

    bucket = _append_trade(None, 4, first)
    bucket = _append_trade(bucket, 4, second)
    tick = BinanceFuturesFeed("SOXLUSDT")._bucket_tick(bucket)

    assert tick.first_trade_id == 10
    assert tick.last_trade_id == 11
    assert tick.price == Decimal("101")
    assert tick.open_price == Decimal("100")
    assert tick.high_price == Decimal("101")
    assert tick.low_price == Decimal("100")
    assert tick.quantity == Decimal("5")
    assert tick.notional == Decimal("503")
    assert tick.buyer_is_maker is None


def test_futures_tick_discards_stale_mark_price() -> None:
    feed = BinanceFuturesFeed("SOXLUSDT")
    feed._update_market_state(
        {
            "markPrice": "99",
            "indexPrice": "98.9",
            "lastFundingRate": "0.001",
            "nextFundingTime": 20_000,
            "time": 1_000,
        }
    )
    payload = {"E": 17_000, "T": 17_000, "t": 10, "p": "100", "q": "2", "m": True}
    stale_tick = feed._bucket_tick(_append_trade(None, 68, payload))

    assert stale_tick.mark_price is None
    assert stale_tick.index_price is None
    assert stale_tick.funding_rate == Decimal("0.001")

    feed._update_market_state(
        {
            "markPrice": "99",
            "indexPrice": "98.9",
            "lastFundingRate": "0.001",
            "nextFundingTime": 20_000,
            "time": 16_000,
        }
    )
    fresh_tick = feed._bucket_tick(_append_trade(None, 68, payload))

    assert fresh_tick.mark_price == Decimal("99")
    assert fresh_tick.index_price == Decimal("98.9")


def test_closed_kline_message_uses_binance_final_ohlcv_fields() -> None:
    bar = _bar_from_stream_kline(
        {
            "t": 900_000,
            "T": 1_799_999,
            "o": "100.25",
            "h": "105.5",
            "l": "99.75",
            "c": "104.0",
            "v": "1234.5",
            "n": 456,
        }
    )

    assert bar.start_ms == 900_000
    assert bar.end_ms == 1_799_999
    assert bar.open == Decimal("100.25")
    assert bar.high == Decimal("105.5")
    assert bar.low == Decimal("99.75")
    assert bar.close == Decimal("104.0")
    assert bar.volume == Decimal("1234.5")
    assert bar.trade_count == 456


def test_rest_kline_verification_filters_to_requested_bar_starts(monkeypatch) -> None:
    rows = [
        [0, "90", "91", "89", "90.5", "5", 899_999, "0", 2],
        [900_000, "100", "105", "98", "104", "50", 1_799_999, "0", 25],
        [1_800_000, "104", "106", "103", "105", "60", 2_699_999, "0", 30],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["interval"] == "15m"
        assert request.url.params["startTime"] == "900000"
        assert request.url.params["endTime"] == "2699999"
        return httpx.Response(200, json=rows)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        feeds.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, timeout=kwargs.get("timeout")),
    )

    bars = asyncio.run(
        _fetch_klines(
            "https://example.test/klines",
            "SOXLUSDT",
            900_000,
            1_800_000,
            trust_env=False,
        )
    )

    assert [bar.start_ms for bar in bars] == [900_000, 1_800_000]
    assert bars[0].close == Decimal("104")
    assert bars[0].volume == Decimal("50")
    assert bars[0].trade_count == 25
