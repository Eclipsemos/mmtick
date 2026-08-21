import asyncio
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from mastermind_tick.calendar_router import (
    DAILY_WARMUP_START_MS,
    FOUR_HOUR_HISTORY_START_MS,
    FOUR_HOUR_MS,
    REPLAY_START_MS,
    RouterRuntime,
    _all_macd_candidates,
    _causal_volatility_exposure,
    _effective_outer_state,
    _funding_by_bar,
    _klines_from,
    _mapping,
    _metric_zscores,
    _portfolio_return,
    _route_turnover,
    _validate_metric_warmup,
    _validate_replay_inputs,
)
from mastermind_tick.config import InstrumentSettings, load_settings
from mastermind_tick.models import Bar, FundingRate, FuturesMetricBar
from mastermind_tick.store import PaperStore


def _continuous_bars(start_ms: int, interval_ms: int, count: int) -> list[Bar]:
    return [
        Bar(
            start_ms + index * interval_ms,
            start_ms + (index + 1) * interval_ms - 1,
            Decimal("100"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
        )
        for index in range(count)
    ]


def test_frozen_mapping_has_three_unique_candidates_for_every_month() -> None:
    mapping = _mapping()

    assert set(mapping) == set(range(1, 13))
    assert all(len(candidates) == 3 for candidates in mapping.values())
    assert all("long_only" in candidate for values in mapping.values() for candidate in values)


def test_empty_forward_ledger_waits_for_first_daily_close(tmp_path) -> None:
    settings = load_settings("config/settings.toml")
    store = PaperStore(tmp_path / "paper.db")
    portfolio = settings.portfolio_paper
    store.ensure_portfolio_account(
        portfolio.id,
        portfolio.symbol,
        portfolio.display_symbol,
        portfolio.venue,
        portfolio.currency,
        settings.initial_cash,
        1,
    )
    runtime = RouterRuntime(portfolio, store, Decimal("100000"), status="LIVE")

    view = runtime.view()

    assert view["decision"]["state"] == "WAITING_FOR_DAILY_CLOSE"
    assert view["decision"]["has_position"] is False
    assert view["decision"]["next_trigger"] == "NEXT_UTC_DAILY_CLOSE"


def test_month_lock_immediately_zeros_effective_outer_exposure() -> None:
    day_end_ms = int(datetime(2026, 8, 21, tzinfo=UTC).timestamp() * 1000) - 1
    latest = {
        "day": "2026-08-20",
        "timestamp_ms": day_end_ms,
        "month_locked": 1,
        "state": {
            "outer_exposure": "4",
            "month_locked": True,
            "month_return": "0.269",
        },
    }

    locked = _effective_outer_state(latest, day_end_ms + 1)
    reset = _effective_outer_state(
        latest, int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)
    )

    assert locked == {
        "ledger_outer_exposure": "4",
        "effective_outer_exposure": "0",
        "effective_since_ms": day_end_ms + 1,
        "month_locked": True,
        "reason": "UTC_MONTHLY_PROFIT_LOCK",
    }
    assert reset["effective_outer_exposure"] == "4"
    assert reset["month_locked"] is False
    assert reset["reason"] == "UTC_MONTH_RESET"


def test_locked_runtime_reports_flat_outer_portfolio(monkeypatch, tmp_path) -> None:
    settings = load_settings("config/settings.toml")
    store = PaperStore(tmp_path / "paper.db")
    portfolio = settings.portfolio_paper
    store.ensure_portfolio_account(
        portfolio.id,
        portfolio.symbol,
        portfolio.display_symbol,
        portfolio.venue,
        portfolio.currency,
        settings.initial_cash,
        1,
    )
    day_end_ms = int(datetime(2026, 8, 21, tzinfo=UTC).timestamp() * 1000) - 1
    store.save_portfolio_day(
        portfolio.id,
        "base",
        "2026-08-20",
        day_end_ms,
        Decimal("126000"),
        Decimal("0.2"),
        Decimal("100000"),
        True,
        {
            "outer_exposure": "4",
            "month_locked": True,
            "month_return": "0.26",
            "metrics": {},
        },
        "test",
    )
    monkeypatch.setattr(
        "mastermind_tick.calendar_router.time.time", lambda: (day_end_ms + 1) / 1000
    )
    runtime = RouterRuntime(portfolio, store, Decimal("100000"), status="LIVE")

    view = runtime.view()
    runtime._reconcile_effective_outer_exposure("base", Decimal("7"), day_end_ms + 1)
    events = store.portfolio_sleeve_events(portfolio.id, "base")

    assert view["decision"]["state"] == "PAUSED"
    assert view["decision"]["has_position"] is False
    assert view["decision"]["reason"] == "UTC_MONTHLY_PROFIT_LOCK"
    assert view["decision"]["next_trigger"] == "NEXT_UTC_MONTH_OPEN"
    assert view["market_state"]["effective_outer_exposure"] == "0"
    assert view["market_state"]["ledger_outer_exposure"] == "4"
    assert events[0]["day"] == "2026-08-21"
    assert events[0]["event_index"] == -100
    assert events[0]["payload"]["target_before"] == "4"
    assert events[0]["payload"]["target_after"] == "0"
    assert events[0]["payload"]["route_cost"] == "0.0028"


def test_portfolio_return_does_not_charge_already_costed_turnover_twice() -> None:
    state = {"raw_return": "0.0072"}

    assert _portfolio_return(state) == Decimal("0.0072")


def test_all_ninety_macd_candidates_continue_in_shadow() -> None:
    candidates = _all_macd_candidates()

    assert len(candidates) == 90
    assert len(set(candidates)) == 90


def test_volatility_exposure_uses_only_twenty_prior_unscaled_state_returns() -> None:
    history = deque([Decimal("0.02")] * 20, maxlen=20)

    exposure, rms = _causal_volatility_exposure(history)

    assert rms == Decimal("0.02")
    assert exposure == Decimal("1.1")
    assert list(history) == [Decimal("0.02")] * 20


def test_calendar_route_turnover_counts_removed_and_added_sleeves() -> None:
    previous = {
        "state": Decimal("0.5"),
        "removed": Decimal("0.1666666666666667"),
        "kept_a": Decimal("0.1666666666666667"),
        "kept_b": Decimal("0.1666666666666667"),
    }
    current = {
        "state": Decimal("0.5"),
        "added": Decimal("0.1666666666666667"),
        "kept_a": Decimal("0.1666666666666667"),
        "kept_b": Decimal("0.1666666666666667"),
    }

    assert _route_turnover(previous, current) == Decimal("0.1666666666666667")


def test_funding_is_assigned_to_the_position_bar_at_the_event_timestamp() -> None:
    bars = [
        Bar(0, 14_399_999, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")),
        Bar(
            14_400_000,
            28_799_999,
            Decimal("100"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
        ),
    ]
    rates = [
        FundingRate(1, Decimal("0.001"), Decimal("100")),
        FundingRate(14_400_001, Decimal("0.002"), Decimal("101")),
    ]

    grouped = _funding_by_bar(bars, rates)

    assert grouped == [[rates[0]], [rates[1]]]


def test_metric_zscore_requires_540_complete_aligned_4h_snapshots() -> None:
    bars = [
        Bar(
            index * 14_400_000,
            (index + 1) * 14_400_000 - 1,
            Decimal("100"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
        )
        for index in range(541)
    ]
    metrics = [
        FuturesMetricBar(
            bar.start_ms,
            bar.end_ms,
            Decimal("1") + Decimal(index) / Decimal("10000"),
            Decimal("1"),
            "test",
        )
        for index, bar in enumerate(bars)
    ]

    complete = _metric_zscores(bars, metrics)
    missing = _metric_zscores(bars, metrics[:100] + metrics[101:])

    assert complete[bars[538].start_ms][0] is None
    assert complete[bars[539].start_ms][0] is not None
    assert missing[bars[539].start_ms][0] is None


def test_replay_validation_requires_continuous_pre_start_signal_history() -> None:
    four_hour_count = 540 + 12
    daily_count = 64 + 12
    btc4 = _continuous_bars(FOUR_HOUR_HISTORY_START_MS, FOUR_HOUR_MS, four_hour_count)
    eth4 = _continuous_bars(FOUR_HOUR_HISTORY_START_MS, FOUR_HOUR_MS, four_hour_count)
    btcd = _continuous_bars(DAILY_WARMUP_START_MS, 86_400_000, daily_count)
    ethd = _continuous_bars(DAILY_WARMUP_START_MS, 86_400_000, daily_count)

    details = _validate_replay_inputs(btc4, eth4, btcd, ethd)

    assert details["btc_4h"]["pre_replay_bar_count"] == 540
    assert details["eth_4h"]["continuous"] is True
    assert details["btc_1d"]["pre_replay_bar_count"] == 64


def test_replay_validation_rejects_the_previous_134_bar_warmup() -> None:
    short_start = REPLAY_START_MS - 134 * FOUR_HOUR_MS
    btc4 = _continuous_bars(short_start, FOUR_HOUR_MS, 150)
    eth4 = _continuous_bars(short_start, FOUR_HOUR_MS, 150)
    btcd = _continuous_bars(DAILY_WARMUP_START_MS, 86_400_000, 76)
    ethd = _continuous_bars(DAILY_WARMUP_START_MS, 86_400_000, 76)

    with pytest.raises(RuntimeError, match="after required"):
        _validate_replay_inputs(btc4, eth4, btcd, ethd)


def test_replay_validation_rejects_a_pre_start_gap() -> None:
    btc4 = _continuous_bars(FOUR_HOUR_HISTORY_START_MS, FOUR_HOUR_MS, 552)
    eth4 = _continuous_bars(FOUR_HOUR_HISTORY_START_MS, FOUR_HOUR_MS, 552)
    del eth4[100]
    btcd = _continuous_bars(DAILY_WARMUP_START_MS, 86_400_000, 76)
    ethd = _continuous_bars(DAILY_WARMUP_START_MS, 86_400_000, 76)

    with pytest.raises(RuntimeError, match="not aligned"):
        _validate_replay_inputs(btc4, eth4, btcd, ethd)


def test_metric_warmup_must_form_a_score_before_replay_start() -> None:
    eth4 = _continuous_bars(FOUR_HOUR_HISTORY_START_MS, FOUR_HOUR_MS, 541)
    complete = [
        FuturesMetricBar(
            bar.start_ms,
            bar.end_ms,
            Decimal("1.2"),
            Decimal("1.1"),
            "archive",
        )
        for bar in eth4
    ]

    details = _validate_metric_warmup(eth4, complete)

    assert details["first_replay_score_at_ms"] == REPLAY_START_MS - FOUR_HOUR_MS
    with pytest.raises(RuntimeError, match="unavailable before replay start"):
        _validate_metric_warmup(eth4, complete[1:])


def test_kline_loader_pages_past_binance_single_request_limit(monkeypatch) -> None:
    interval_ms = FOUR_HOUR_MS
    start_ms = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000)
    all_rows = [
        [
            start_ms + index * interval_ms,
            "100",
            "101",
            "99",
            "100",
            "1",
            start_ms + (index + 1) * interval_ms - 1,
            "0",
            1,
        ]
        for index in range(1510)
    ]
    requested_starts = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, _url, *, params):
            requested_starts.append(params["startTime"])
            offset = (params["startTime"] - start_ms) // interval_ms
            return Response(all_rows[offset : offset + params["limit"]])

    monkeypatch.setattr("mastermind_tick.calendar_router.httpx.AsyncClient", Client)

    bars = asyncio.run(_klines_from("BTCUSDT", "4h", interval_ms, start_ms))

    assert len(bars) == 1510
    assert requested_starts == [start_ms, start_ms + 1500 * interval_ms]


def test_portfolio_day_persists_normalized_sleeve_audit_events(tmp_path) -> None:
    settings = load_settings("config/settings.toml")
    store = PaperStore(tmp_path / "paper.db")
    portfolio = settings.portfolio_paper
    store.ensure_portfolio_account(
        portfolio.id,
        portfolio.symbol,
        portfolio.display_symbol,
        portfolio.venue,
        portfolio.currency,
        settings.initial_cash,
        1,
    )
    event = {
        "timestamp_ms": 2,
        "sleeve_id": "state_btc_btc",
        "instrument_id": "btc_perp",
        "event_type": "FUNDING",
        "funding_return": "-0.001",
    }

    inserted = store.save_portfolio_day(
        portfolio.id,
        "base",
        "2026-08-16",
        3,
        Decimal("100000"),
        Decimal("0"),
        Decimal("100000"),
        False,
        {},
        "test",
        [event],
    )

    assert inserted is True
    assert store.portfolio_sleeve_events(portfolio.id, "base")[0]["payload"] == event


def test_runtime_outer_rebalance_is_persisted_idempotently(tmp_path) -> None:
    settings = load_settings("config/settings.toml")
    store = PaperStore(tmp_path / "paper.db")
    portfolio = settings.portfolio_paper
    store.ensure_portfolio_account(
        portfolio.id,
        portfolio.symbol,
        portfolio.display_symbol,
        portfolio.venue,
        portfolio.currency,
        settings.initial_cash,
        1,
    )
    event = {
        "timestamp_ms": 2,
        "sleeve_id": "outer_exposure",
        "instrument_id": None,
        "event_type": "OUTER_EXPOSURE_REBALANCE",
        "target_before": "4",
        "target_after": "0",
    }

    first = store.save_portfolio_runtime_event(
        portfolio.id, "base", "2026-08-21", -100, event, "test"
    )
    duplicate = store.save_portfolio_runtime_event(
        portfolio.id, "base", "2026-08-21", -100, event, "test"
    )

    assert first is True
    assert duplicate is False
    assert store.portfolio_sleeve_event_exists(
        portfolio.id, "base", 2, "outer_exposure", "OUTER_EXPOSURE_REBALANCE"
    )
    assert store.portfolio_sleeve_events(portfolio.id, "base")[0]["payload"] == event


def test_delete_paper_account_preserves_shared_market_bars(tmp_path) -> None:
    store = PaperStore(tmp_path / "paper.db")
    instrument = InstrumentSettings(
        id="soxl_perp",
        symbol="SOXLUSDT",
        display_symbol="SOXL/USDT PERP",
        name="SOXL",
        asset_type="tradifi_perpetual",
        venue="Binance USD-M Futures",
        currency="USDT",
        feed="binance_futures",
        quantity_step=0.01,
        reference_symbol="SOXL",
        paper_model="futures",
    )
    store.ensure_account(instrument, 100_000, 1)
    store.upsert_history_bars(
        instrument,
        15,
        [Bar(0, 899_999, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"))],
        "test",
    )
    metric = FuturesMetricBar(
        0,
        14_399_999,
        Decimal("1.2"),
        Decimal("1.1"),
        "first-seen",
    )
    store.upsert_futures_metric_bars("ETHUSDT", 240, [metric])
    store.upsert_futures_metric_bars(
        "ETHUSDT",
        240,
        [
            FuturesMetricBar(
                0,
                14_399_999,
                Decimal("9"),
                Decimal("9"),
                "late-rewrite",
            )
        ],
    )

    store.delete_paper_account(instrument.id)

    assert store.accounts() == []
    assert len(store.ohlcv_bars(instrument.id, 15, 10)) == 1
    assert store.futures_metric_bars("ETHUSDT", 240) == [metric]
