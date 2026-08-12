import importlib.util
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
