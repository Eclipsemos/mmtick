import json
from decimal import Decimal

from mastermind_tick.config import (
    ExecutionSettings,
    InstrumentSettings,
    Settings,
    StrategySettings,
)
from mastermind_tick.models import Bar, Side, StrategySignal, Tick
from mastermind_tick.rebuild import apply_candidate, rebuild_candidate
from mastermind_tick.store import PaperStore

BAR_MS = 900_000


def _instrument() -> InstrumentSettings:
    return InstrumentSettings(
        id="test",
        symbol="TESTUSDT",
        display_symbol="TEST/USDT",
        name="Test",
        asset_type="test",
        venue="test",
        currency="USDT",
        feed="test",
        quantity_step=0.01,
        reference_symbol="TEST",
    )


def _settings(tmp_path) -> Settings:
    return Settings(
        project_root=tmp_path,
        app_name="test",
        environment="paper",
        database_path=tmp_path / "paper.db",
        frontend_dist=tmp_path / "dist",
        initial_cash=10_000,
        equity_snapshot_seconds=10,
        strategy=StrategySettings("atr", 15, 2, 0.5, 1),
        execution=ExecutionSettings(10, 0, 5),
        warmup_bars=10,
        instruments=(_instrument(),),
    )


def _bar(index: int, close: str) -> Bar:
    value = Decimal(close)
    return Bar(
        start_ms=index * BAR_MS,
        end_ms=(index + 1) * BAR_MS - 1,
        open=value,
        high=value + Decimal("0.1"),
        low=value - Decimal("0.1"),
        close=value,
        volume=Decimal("10"),
    )


def _tick(event_id: str, timestamp_ms: int, price: str) -> Tick:
    value = Decimal(price)
    return Tick(
        event_id=event_id,
        timestamp_ms=timestamp_ms,
        price=value,
        quantity=Decimal("1"),
        source="test",
        notional=value,
    )


def _signal() -> StrategySignal:
    return StrategySignal(
        side=Side.BUY,
        reason="contaminated",
        signal_price=Decimal("1"),
        trailing_stop=Decimal("1"),
        atr=Decimal("1"),
        bar_start_ms=0,
        tick_id="old",
    )


def test_rebuild_candidate_replaces_ledger_and_preserves_market_data(tmp_path) -> None:
    settings = _settings(tmp_path)
    store = PaperStore(settings.database_path)
    instrument = settings.instruments[0]
    store.ensure_account(instrument, settings.initial_cash, 1)
    store.upsert_history_bars(instrument, 15, [_bar(0, "10"), _bar(1, "9")], "test")
    ticks = [
        _tick("tick-1", 2 * BAR_MS, "10"),
        _tick("tick-2", 2 * BAR_MS + 1, "10.1"),
        _tick("tick-3", 2 * BAR_MS + 2, "8"),
        _tick("tick-4", 2 * BAR_MS + 3, "7.9"),
    ]
    for item in ticks:
        store.record_market_tick(instrument, 15, item)
    store.submit_order(instrument.id, _signal(), 1)
    source_market_count = len(store.agg_trades(instrument.id, 100))

    candidate_path = tmp_path / "candidate.db"
    report = rebuild_candidate(settings, candidate_path)

    assert len(store.orders(instrument.id)) == 1
    assert store.orders(instrument.id)[0]["reason"] == "contaminated"
    candidate = PaperStore(candidate_path)
    assert len(candidate.agg_trades(instrument.id, 100)) == source_market_count
    orders = candidate.orders(instrument.id, 100)
    fills = candidate.fills(instrument.id, 100)
    assert orders
    assert all(order["reason"] != "contaminated" for order in orders)
    assert fills
    assert min(fill["timestamp_ms"] for fill in fills) > min(
        order["submitted_at_ms"] for order in orders
    )
    state = candidate.strategy_state(instrument.id)
    assert state is not None
    assert state["period"] == 2
    assert state["multiplier"] == "0.5"
    assert state["bar_ms"] == BAR_MS
    assert report["market_counts"]["agg_trades"] == source_market_count


def test_apply_candidate_atomically_replaces_derived_rows(tmp_path) -> None:
    settings = _settings(tmp_path)
    store = PaperStore(settings.database_path)
    instrument = settings.instruments[0]
    store.ensure_account(instrument, settings.initial_cash, 1)
    store.upsert_history_bars(instrument, 15, [_bar(0, "10"), _bar(1, "9")], "test")
    for item in (
        _tick("tick-1", 2 * BAR_MS, "10"),
        _tick("tick-2", 2 * BAR_MS + 1, "10.1"),
    ):
        store.record_market_tick(instrument, 15, item)
    store.submit_order(instrument.id, _signal(), 1)
    candidate_path = tmp_path / "candidate.db"
    rebuild_candidate(settings, candidate_path)

    apply_candidate(settings.database_path, candidate_path, (instrument.id,))

    rebuilt = PaperStore(settings.database_path)
    assert all(order["reason"] != "contaminated" for order in rebuilt.orders(instrument.id))
    state = rebuilt.strategy_state(instrument.id)
    assert state is not None
    assert json.loads(json.dumps(state))["period"] == 2
