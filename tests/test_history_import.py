import importlib.util
import io
import json
import sqlite3
from datetime import date
from pathlib import Path

from mastermind_tick.store import PaperStore


def _history_import_module():
    path = Path(__file__).parents[1] / "scripts" / "import_soxl_history.py"
    spec = importlib.util.spec_from_file_location("mmtick_history_import", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_history_rows_use_selected_research_instrument(tmp_path) -> None:
    history = _history_import_module()
    database = tmp_path / "paper.db"
    PaperStore(database)
    connection = sqlite3.connect(database)
    trade_rows = [
        {
            "agg_trade_id": "7",
            "price": "3000.25",
            "quantity": "0.5",
            "first_trade_id": "11",
            "last_trade_id": "12",
            "transact_time": "1000",
            "is_buyer_maker": "false",
        }
    ]
    bar_rows = [
        {
            "open_time": "0",
            "open": "3000",
            "high": "3010",
            "low": "2990",
            "close": "3005",
            "volume": "10",
            "close_time": "899999",
            "count": "20",
        }
    ]

    history._insert_ticks(
        connection,
        history._bucket_rows(trade_rows, "eth_perp", "ETHUSDT", history.SOURCE),
    )
    history._import_bars(connection, bar_rows, "eth_perp", "ETHUSDT")

    assert connection.execute("SELECT instrument_id, symbol FROM agg_trades").fetchone() == (
        "eth_perp",
        "ETHUSDT",
    )
    assert connection.execute("SELECT instrument_id, symbol FROM ohlcv_bars").fetchone() == (
        "eth_perp",
        "ETHUSDT",
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM agg_trades WHERE instrument_id = 'soxl_perp'"
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_month_range_starts_at_requested_incremental_month() -> None:
    history = _history_import_module()

    assert history._months(date(2026, 8, 10), date(2026, 8, 11)) == [(2026, 8)]


def test_kline_rows_parse_headerless_and_headered_archives() -> None:
    history = _history_import_module()
    row = "0,100,102,99,101,10,899999,1000,20,5,500,0\n"
    header = ",".join(history.KLINE_FIELDS) + "\n"

    headerless = history._kline_rows(io.BytesIO(row.encode()))
    headered = history._kline_rows(io.BytesIO((header + row).encode()))

    assert headerless == headered
    assert headerless[0]["open_time"] == "0"
    assert headerless[0]["close"] == "101"


def test_rest_import_skips_a_range_older_than_existing_ticks(tmp_path) -> None:
    history = _history_import_module()
    database = tmp_path / "paper.db"
    PaperStore(database)
    connection = sqlite3.connect(database)
    rows = [
        {
            "agg_trade_id": "7",
            "price": "100",
            "quantity": "1",
            "first_trade_id": "11",
            "last_trade_id": "12",
            "transact_time": "2000",
            "is_buyer_maker": "false",
        }
    ]
    history._insert_ticks(
        connection,
        history._bucket_rows(rows, "soxl_perp", "SOXLUSDT", history.SOURCE),
    )

    def fail_get(*_args, **_kwargs):
        raise AssertionError("REST must not be called")

    history._get = fail_get

    imported = history._fetch_current_agg_trades(
        connection,
        "soxl_perp",
        "SOXLUSDT",
        start_ms=0,
        end_ms=2500,
    )

    assert imported == 0
    connection.close()


def test_funding_import_paginates_until_the_end() -> None:
    history = _history_import_module()
    page_size = 1000
    interval_ms = 8 * 60 * 60 * 1000
    calls = []

    def fake_get(url: str, **_kwargs) -> bytes:
        calls.append(url)
        offset = 0 if len(calls) == 1 else page_size
        count = page_size if len(calls) == 1 else 2
        payload = [
            {
                "fundingTime": (offset + index) * interval_ms,
                "fundingRate": "0.0001",
                "markPrice": "50000",
            }
            for index in range(count)
        ]
        return json.dumps(payload).encode()

    history._get = fake_get
    history.time.sleep = lambda _seconds: None

    rows = history._fetch_funding(
        "btc_perp",
        "BTCUSDT",
        start_ms=0,
        end_ms=(page_size + 2) * interval_ms,
    )

    assert len(rows) == page_size + 2
    assert len(calls) == 2
    assert f"startTime={(page_size - 1) * interval_ms + 1}" in calls[1]


def test_kline_import_includes_the_inclusive_end_boundary(tmp_path) -> None:
    history = _history_import_module()
    database = tmp_path / "paper.db"
    PaperStore(database)
    connection = sqlite3.connect(database)
    end_ms = 899_999
    payload = [
        [0, "100", "102", "99", "101", "10", end_ms, "0", 20],
    ]
    history._get = lambda *_args, **_kwargs: json.dumps(payload).encode()

    imported = history._fetch_current_klines(
        connection,
        "soxl_perp",
        "SOXLUSDT",
        start_ms=0,
        end_ms=end_ms,
    )

    assert imported > 0
    assert connection.execute("SELECT MAX(end_ms) FROM ohlcv_bars").fetchone()[0] == end_ms
    connection.close()
