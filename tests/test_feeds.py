from decimal import Decimal

from mastermind_tick.feeds import BinanceFuturesFeed, _append_trade


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
