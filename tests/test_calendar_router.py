from decimal import Decimal

from mastermind_tick.calendar_router import _mapping, _portfolio_return
from mastermind_tick.config import InstrumentSettings
from mastermind_tick.models import Bar
from mastermind_tick.store import PaperStore


def test_frozen_mapping_has_three_unique_candidates_for_every_month() -> None:
    mapping = _mapping()

    assert set(mapping) == set(range(1, 13))
    assert all(len(candidates) == 3 for candidates in mapping.values())
    assert all("long_only" in candidate for values in mapping.values() for candidate in values)


def test_portfolio_return_applies_state_trend_and_turnover_costs() -> None:
    state = {
        "state_return": "0.02",
        "trend_returns": ["0.01", "0", "-0.01"],
        "turnover": "2",
    }

    assert _portfolio_return(state, Decimal("7"), Decimal("7")) == Decimal("0.0072")


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

    store.delete_paper_account(instrument.id)

    assert store.accounts() == []
    assert len(store.ohlcv_bars(instrument.id, 15, 10)) == 1
