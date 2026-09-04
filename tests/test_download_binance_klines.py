import csv
import io
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "maintenance"))

import download_binance_klines as downloader  # noqa: E402


def _row(open_time: int) -> list:
    return [
        open_time,
        "100",
        "101",
        "99",
        "100.5",
        "10",
        open_time + 899_999,
        "1000",
        5,
        "4",
        "400",
        "0",
    ]


def test_rest_fallback_writes_loader_compatible_zip(monkeypatch, tmp_path) -> None:
    rows = [_row(1_567_296_000_000)]
    monkeypatch.setattr(downloader, "fetch_completed_rows", lambda *_args: rows)

    written = downloader.write_rest_archive_day("BTCUSDT", "15m", date(2019, 9, 1), tmp_path)

    assert written is True
    path = tmp_path / "BTCUSDT-15m-2019-09-01.zip"
    with zipfile.ZipFile(path) as archive:
        content = archive.read("BTCUSDT-15m-2019-09-01.csv").decode()
    parsed = list(csv.reader(io.StringIO(content)))
    assert tuple(parsed[0]) == downloader.HEADER
    assert int(parsed[1][0]) == rows[0][0]


def test_current_csv_uses_only_rows_returned_for_requested_day(monkeypatch, tmp_path) -> None:
    rows = [_row(1_577_836_800_000)]
    captured = {}

    def fake_fetch(symbol, interval, start_ms, end_ms):
        captured.update(
            symbol=symbol,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        return rows

    monkeypatch.setattr(downloader, "fetch_completed_rows", fake_fetch)

    downloader.write_current_completed("BTCUSDT", "15m", date(2020, 1, 1), tmp_path)

    assert captured["end_ms"] - captured["start_ms"] == 86_400_000
    with (tmp_path / "BTCUSDT-15m-current.csv").open() as handle:
        parsed = list(csv.reader(handle))
    assert len(parsed) == 2


def test_empty_current_update_preserves_existing_file(monkeypatch, tmp_path) -> None:
    path = tmp_path / "BTCUSDT-15m-current.csv"
    path.write_text("existing\n")
    monkeypatch.setattr(downloader, "fetch_completed_rows", lambda *_args: [])

    downloader.write_current_completed("BTCUSDT", "15m", date(2020, 1, 1), tmp_path)

    assert path.read_text() == "existing\n"
