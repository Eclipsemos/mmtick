from collections import deque
from decimal import Decimal

from mastermind_tick.calendar_router import (
    RouterRuntime,
    _all_macd_candidates,
    _causal_volatility_exposure,
    _funding_by_bar,
    _mapping,
    _metric_zscores,
    _portfolio_return,
    _route_turnover,
)
from mastermind_tick.config import InstrumentSettings, load_settings
from mastermind_tick.models import Bar, FundingRate, FuturesMetricBar
from mastermind_tick.store import PaperStore


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
