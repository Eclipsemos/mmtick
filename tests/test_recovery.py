import sqlite3
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from mastermind_tick.config import load_settings
from mastermind_tick.models import Bar, Side, StrategySignal, Tick
from mastermind_tick.recovery import (
    BACKFILL_SOURCE,
    FUTURES_BACKFILL_SOURCE,
    RECONSTRUCTED_SOURCE,
    TradeGap,
    apply_recovery_candidate,
    detect_trade_gaps,
    fetch_gap_agg_trades,
    recover_candidate,
)
from mastermind_tick.store import PaperStore

BAR_MS = 900_000


def test_gap_fetch_accepts_official_raw_id_skip_when_aggregate_ids_are_continuous() -> None:
    gap = TradeGap(1_000, 2_000, 10, 14)
    payload = [
        {"a": 100, "p": "1", "q": "1", "f": 11, "l": 12, "T": 1_100, "m": False},
        {"a": 101, "p": "1", "q": "1", "f": 14, "l": 14, "T": 1_200, "m": False},
    ]

    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )

    assert fetch_gap_agg_trades(client, "SOXLUSDT", gap) == payload


def test_gap_detection_threshold_can_include_short_feed_outages(tmp_path) -> None:
    store = PaperStore(tmp_path / "threshold.db")
    base = load_settings("config/settings.toml")
    instrument = next(item for item in base.instruments if item.id == "soxl_perp")
    store.ensure_account(instrument, base.initial_cash, 1)
    store.record_market_tick(
        instrument,
        15,
        Tick(
            "left",
            1_000,
            Decimal("100"),
            Decimal("1"),
            "test",
            first_trade_id=1,
            last_trade_id=1,
        ),
    )
    store.record_market_tick(
        instrument,
        15,
        Tick(
            "right",
            11_000,
            Decimal("101"),
            Decimal("1"),
            "test",
            first_trade_id=10,
            last_trade_id=10,
        ),
    )

    with store.connection() as connection:
        assert detect_trade_gaps(connection, instrument.id, 1_001, 10_999) == []
        assert len(
            detect_trade_gaps(
                connection,
                instrument.id,
                1_001,
                10_999,
                minimum_gap_ms=5_000,
            )
        ) == 1


def test_futures_gap_recovery_backfills_market_and_chart_without_touching_ledger(
    tmp_path,
) -> None:
    base = load_settings("config/settings.toml")
    instrument = next(item for item in base.instruments if item.id == "soxl_perp")
    settings = replace(
        base,
        database_path=tmp_path / "paper.db",
        warmup_bars=30,
        instruments=(instrument,),
    )
    store = PaperStore(settings.database_path)
    replay_start = 30 * BAR_MS
    store.ensure_account(instrument, settings.initial_cash, 1)
    store.upsert_history_bars(
        instrument,
        15,
        [
            Bar(
                start_ms=index * BAR_MS,
                end_ms=(index + 1) * BAR_MS - 1,
                open=Decimal(100 + index),
                high=Decimal(102 + index),
                low=Decimal(99 + index),
                close=Decimal(101 + index),
                volume=Decimal("10"),
            )
            for index in range(30)
        ],
        "test_kline_rest",
    )
    store.submit_order(
        instrument.id,
        StrategySignal(
            side=Side.SELL,
            reason="open-short",
            signal_price=Decimal("130"),
            trailing_stop=Decimal("135"),
            atr=Decimal("2"),
            bar_start_ms=replay_start - BAR_MS,
            tick_id="open-signal",
        ),
        replay_start - 20,
    )
    open_tick = Tick(
        "open-fill",
        replay_start - 10,
        Decimal("130"),
        Decimal("1"),
        "test",
    )
    store.fill_pending(instrument.id, open_tick, instrument, settings.execution, 0.1)
    store.snapshot(
        instrument.id,
        open_tick,
        {"atr": "2", "trailing_stop": "135", "relation": "below"},
    )
    store.snapshot(
        instrument.id,
        Tick(
            "polluted-runtime-snapshot",
            replay_start + 50_000,
            Decimal("128"),
            Decimal("1"),
            "binance_futures",
        ),
        {"atr": "999", "trailing_stop": "999", "relation": "below"},
    )

    before = Tick(
        "live-before",
        replay_start + 1_000,
        Decimal("129"),
        Decimal("1"),
        "binance_futures",
        first_trade_id=10,
        last_trade_id=10,
    )
    after = Tick(
        "live-after",
        replay_start + 131_000,
        Decimal("127"),
        Decimal("1"),
        "binance_futures",
        first_trade_id=20,
        last_trade_id=20,
    )
    store.record_market_tick(instrument, 15, before)
    store.record_market_tick(instrument, 15, after)
    original_orders = store.orders(instrument.id, 100)
    original_fills = store.fills(instrument.id, 100)

    payload = [
        {
            "a": 100 + index,
            "p": str(Decimal("129") - Decimal(index) / 10),
            "q": "1",
            "f": 11 + index,
            "l": 11 + index,
            "T": replay_start + 10_000 + index * 10_000,
            "m": bool(index % 2),
        }
        for index in range(9)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/aggTrades")
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    candidate_path = tmp_path / "candidate.db"
    report = recover_candidate(
        settings,
        candidate_path,
        instrument.id,
        replay_start + 1_001,
        replay_start + 130_999,
        client=client,
    )

    assert report.fetched_agg_trades == 9
    assert report.inserted_tick_buckets == 9
    assert report.reconstructed_snapshots > 0
    assert store.orders(instrument.id, 100) == original_orders
    assert store.fills(instrument.id, 100) == original_fills
    candidate = PaperStore(candidate_path)
    with candidate.connection() as connection:
        gap_count = connection.execute(
            "SELECT COUNT(*) FROM agg_trades WHERE source = ?", (BACKFILL_SOURCE,)
        ).fetchone()[0]
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM equity_snapshots WHERE source = ?",
            (RECONSTRUCTED_SOURCE,),
        ).fetchone()[0]
        polluted_snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM equity_snapshots WHERE source = 'binance_futures' "
            "AND account_id = ? AND timestamp_ms BETWEEN ? AND ?",
            (instrument.id, replay_start + 1_001, replay_start + 130_999),
        ).fetchone()[0]
        assert detect_trade_gaps(
            connection,
            instrument.id,
            replay_start + 1_001,
            replay_start + 130_999,
        ) == []
    assert gap_count == 9
    assert snapshot_count == report.reconstructed_snapshots
    assert polluted_snapshot_count == 0

    applied = apply_recovery_candidate(
        settings.database_path,
        candidate_path,
        instrument.id,
        replay_start + 1_001,
        replay_start + 130_999,
        market_start_ms=report.replay_start_ms,
    )

    assert applied["agg_trade_buckets"] == 9
    assert applied["snapshots"] == report.reconstructed_snapshots
    rebuilt = PaperStore(settings.database_path)
    assert rebuilt.orders(instrument.id, 100) == original_orders
    assert rebuilt.fills(instrument.id, 100) == original_fills
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agg_trades WHERE source = ?", (BACKFILL_SOURCE,)
        ).fetchone()[0] == 9
        assert connection.execute(
            "SELECT COUNT(*) FROM equity_snapshots WHERE source = 'binance_futures' "
            "AND account_id = ? AND timestamp_ms BETWEEN ? AND ?",
            (instrument.id, replay_start + 1_001, replay_start + 130_999),
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("account_id", "expected_path", "expected_source"),
    [
        ("soxl_perp_long", "/fapi/v1/aggTrades", FUTURES_BACKFILL_SOURCE),
    ],
)
def test_gap_recovery_supports_spot_and_shared_futures_market_data(
    tmp_path,
    account_id: str,
    expected_path: str,
    expected_source: str,
) -> None:
    base = load_settings("config/settings.toml")
    account_instrument = next(item for item in base.instruments if item.id == account_id)
    market_instrument = next(
        item for item in base.instruments if item.id == account_instrument.market_id
    )
    instruments = tuple(
        item
        for item in base.instruments
        if item.id in {account_instrument.id, market_instrument.id}
    )
    settings = replace(
        base,
        database_path=tmp_path / f"{account_id}.db",
        warmup_bars=40,
        instruments=instruments,
    )
    store = PaperStore(settings.database_path)
    replay_start = 40 * BAR_MS
    store.ensure_account(account_instrument, settings.initial_cash, 1)
    store.upsert_history_bars(
        market_instrument,
        15,
        [
            Bar(
                start_ms=index * BAR_MS,
                end_ms=(index + 1) * BAR_MS - 1,
                open=Decimal(100 + index),
                high=Decimal(102 + index),
                low=Decimal(99 + index),
                close=Decimal(101 + index),
                volume=Decimal("10"),
            )
            for index in range(40)
        ],
        "test_kline_rest",
    )
    checkpoint = Tick(
        "checkpoint",
        replay_start - 10,
        Decimal("130"),
        Decimal("1"),
        "test",
    )
    store.snapshot(
        account_instrument.id,
        checkpoint,
        {"atr": "2", "trailing_stop": "135", "relation": "below"},
    )
    store.record_market_tick(
        market_instrument,
        15,
        Tick(
            "left",
            replay_start + 1_000,
            Decimal("129"),
            Decimal("1"),
            "test",
            first_trade_id=10,
            last_trade_id=10,
        ),
    )
    store.record_market_tick(
        market_instrument,
        15,
        Tick(
            "right",
            replay_start + 131_000,
            Decimal("127"),
            Decimal("1"),
            "test",
            first_trade_id=20,
            last_trade_id=20,
        ),
    )
    payload = [
        {
            "a": 100 + index,
            "p": str(Decimal("129") - Decimal(index) / 10),
            "q": "1",
            "f": 11 + index,
            "l": 11 + index,
            "T": replay_start + 10_000 + index * 10_000,
            "m": bool(index % 2),
        }
        for index in range(9)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_path
        return httpx.Response(200, json=payload)

    candidate_path = tmp_path / f"{account_id}-candidate.db"
    report = recover_candidate(
        settings,
        candidate_path,
        account_instrument.id,
        replay_start + 1_001,
        replay_start + 130_999,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert report.fetched_agg_trades == 9
    assert report.reconstructed_snapshots > 0
    with sqlite3.connect(candidate_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM agg_trades WHERE instrument_id = ? AND source = ?",
            (market_instrument.id, expected_source),
        ).fetchone()[0] == 9
        assert connection.execute(
            "SELECT COUNT(*) FROM equity_snapshots WHERE account_id = ? AND source = ?",
            (account_instrument.id, RECONSTRUCTED_SOURCE),
        ).fetchone()[0] == report.reconstructed_snapshots

    applied = apply_recovery_candidate(
        settings.database_path,
        candidate_path,
        account_instrument.id,
        replay_start + 1_001,
        replay_start + 130_999,
        market_start_ms=report.replay_start_ms,
        market_id=market_instrument.id,
    )
    assert applied["agg_trade_buckets"] == 9
    assert applied["snapshots"] == report.reconstructed_snapshots
