from datetime import UTC, date, datetime
from io import BytesIO

import pytest

from mastermind_tick.historical_data import (
    ARCHIVE_SOURCE,
    _csv_archive_trades,
    archive_specs,
    bucket_trades,
)


def test_archive_plan_uses_complete_months_then_daily_tail() -> None:
    onboard = int(datetime(2026, 5, 15, 6, tzinfo=UTC).timestamp() * 1000)

    specs = archive_specs(onboard, date(2026, 8, 2))

    assert [item.period for item in specs] == [
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08-01",
        "2026-08-02",
    ]


def test_archive_rows_are_normalized_and_bucketed_at_250_ms() -> None:
    payload = BytesIO(
        b"agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,"
        b"is_buyer_maker\n"
        b"10,100,1.5,20,21,1778853609522000,false\n"
        b"11,101,2,22,22,1778853609705000,true\n"
        b"12,99,1,23,24,1778853610000000,true\n"
    )

    rows = list(bucket_trades(_csv_archive_trades(payload), ARCHIVE_SOURCE))

    assert len(rows) == 2
    assert rows[0].timestamp_ms == 1778853609705
    assert rows[0].open_price == 100
    assert rows[0].price == 101
    assert rows[0].high_price == 101
    assert rows[0].low_price == 100
    assert rows[0].quantity == 3.5
    assert rows[0].notional == 352
    assert rows[0].buyer_is_maker is None
    assert rows[0].first_trade_id == 20
    assert rows[0].last_trade_id == 22
    assert rows[0].aggregate_id == 11


def test_archive_bucket_rejects_missing_aggregate_trade() -> None:
    payload = BytesIO(
        b"agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,"
        b"is_buyer_maker\n"
        b"10,100,1,20,20,1000,false\n"
        b"12,100,1,22,22,1100,false\n"
    )

    with pytest.raises(RuntimeError, match="expected 11, got 12"):
        list(bucket_trades(_csv_archive_trades(payload), ARCHIVE_SOURCE))
