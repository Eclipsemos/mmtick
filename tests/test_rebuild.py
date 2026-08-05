import json
from dataclasses import replace
from decimal import Decimal

from mastermind_tick.config import (
    ExecutionSettings,
    InstrumentSettings,
    Settings,
    StrategySettings,
)
from mastermind_tick.models import Bar, Side, StrategySignal, Tick
from mastermind_tick.rebuild import (
    apply_candidate,
    rebuild_candidate,
    replay_candidate_tail,
)
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
        strategy=StrategySettings("atr", 15, 2, 0.5, 1, 2, 0, 0.25),
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
    assert state["trend_efficiency_period"] == 2
    assert state["minimum_trend_efficiency"] == "0"
    assert report["market_counts"]["agg_trades"] == source_market_count


def test_rebuild_persists_profit_protection_for_target_futures_account(tmp_path) -> None:
    base = _settings(tmp_path)
    instrument = replace(
        _instrument(),
        id="soxl_perp",
        paper_model="futures",
        leverage=2,
        margin_mode="isolated",
        allow_short=True,
    )
    settings = replace(
        base,
        instruments=(instrument,),
        live_futures=replace(
            base.live_futures,
            instrument_id=instrument.id,
            profit_activation_atr=2,
            profit_trailing_atr=0.5,
        ),
    )
    store = PaperStore(settings.database_path)
    store.upsert_history_bars(instrument, 15, [_bar(0, "10"), _bar(1, "9")], "test")
    for item in (
        _tick("tick-1", 2 * BAR_MS, "10"),
        _tick("tick-2", 2 * BAR_MS + 1, "10.1"),
        _tick("tick-3", 3 * BAR_MS, "8"),
        _tick("tick-4", 3 * BAR_MS + 1, "7.9"),
    ):
        store.record_market_tick(instrument, 15, item)

    candidate_path = tmp_path / "profit-protection.db"
    report = rebuild_candidate(settings, candidate_path, (instrument.id,))

    state = PaperStore(candidate_path).strategy_state(instrument.id)
    assert state is not None
    assert state["profit_protection"]["activation_atr"] == "2"
    assert state["profit_protection"]["trailing_atr"] == "0.5"
    assert report["strategy"]["profit_activation_atr"] == 2
    assert report["strategy"]["profit_trailing_atr"] == 0.5


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
    store.add_event(
        instrument.id,
        2,
        "ERROR",
        "FEED_DISCONNECTED",
        "preserve operational diagnostics",
    )
    candidate_path = tmp_path / "candidate.db"
    rebuild_candidate(settings, candidate_path)

    apply_candidate(settings.database_path, candidate_path, (instrument.id,))

    rebuilt = PaperStore(settings.database_path)
    assert all(order["reason"] != "contaminated" for order in rebuilt.orders(instrument.id))
    assert any(
        event["event_type"] == "FEED_DISCONNECTED"
        for event in rebuilt.events(instrument.id, 100)
    )
    state = rebuilt.strategy_state(instrument.id)
    assert state is not None
    assert json.loads(json.dumps(state))["period"] == 2


def test_selected_long_only_account_replays_shared_futures_market(tmp_path) -> None:
    base = _settings(tmp_path)
    market = InstrumentSettings(
        **{
            **_instrument().__dict__,
            "id": "perp",
            "paper_model": "futures",
            "leverage": 2,
            "margin_mode": "isolated",
        }
    )
    long_only = InstrumentSettings(
        **{
            **market.__dict__,
            "id": "perp_long",
            "display_symbol": "TEST/USDT PERP LONG ONLY",
            "market_data_id": market.id,
            "allow_short": False,
        }
    )
    settings = Settings(**{**base.__dict__, "instruments": (market, long_only)})
    store = PaperStore(settings.database_path)
    store.upsert_history_bars(market, 15, [_bar(0, "10"), _bar(1, "9")], "test")
    for item in (
        _tick("tick-1", 2 * BAR_MS, "10"),
        _tick("tick-2", 2 * BAR_MS + 1, "10.1"),
        _tick("tick-3", 3 * BAR_MS, "8"),
        _tick("tick-4", 3 * BAR_MS + 1, "7.9"),
    ):
        store.record_market_tick(market, 15, item)

    candidate_path = tmp_path / "long-only.db"
    report = rebuild_candidate(settings, candidate_path, (long_only.id,))

    candidate = PaperStore(candidate_path)
    account = candidate.account(long_only.id)
    fills = candidate.fills(long_only.id, 100)
    assert report["accounts"][0]["account_id"] == long_only.id
    assert not candidate.agg_trades(long_only.id, 100)
    assert len(candidate.agg_trades(market.id, 100)) == 4
    assert Decimal(account["quantity"]) >= 0
    assert all(Decimal(fill["position_after"] or "0") >= 0 for fill in fills)


def test_new_account_apply_replays_append_only_market_tail(tmp_path) -> None:
    base = _settings(tmp_path)
    market = InstrumentSettings(
        **{
            **_instrument().__dict__,
            "id": "perp",
            "paper_model": "futures",
            "leverage": 2,
            "margin_mode": "isolated",
        }
    )
    long_only = InstrumentSettings(
        **{
            **market.__dict__,
            "id": "perp_long",
            "market_data_id": market.id,
            "allow_short": False,
        }
    )
    settings = Settings(**{**base.__dict__, "instruments": (market, long_only)})
    store = PaperStore(settings.database_path)
    store.ensure_account(market, settings.initial_cash, 1)
    store.upsert_history_bars(market, 15, [_bar(0, "10"), _bar(1, "9")], "test")
    for item in (
        _tick("tick-1", 2 * BAR_MS, "10"),
        _tick("tick-2", 2 * BAR_MS + 1, "10.1"),
    ):
        store.record_market_tick(market, 15, item)
    preserved_order_id = store.submit_order(market.id, _signal(), 1)

    candidate_path = tmp_path / "candidate.db"
    rebuild_candidate(settings, candidate_path, (long_only.id,))

    tail = (
        _tick("tick-3", 3 * BAR_MS, "8"),
        _tick("tick-4", 3 * BAR_MS + 1, "7.9"),
    )
    for item in tail:
        store.record_market_tick(market, 15, item)

    apply_candidate(settings.database_path, candidate_path, (long_only.id,))
    replayed = replay_candidate_tail(settings, candidate_path, (long_only.id,))

    rebuilt = PaperStore(settings.database_path)
    assert replayed == {long_only.id: len(tail)}
    assert rebuilt.orders(market.id, 10)[0]["id"] == preserved_order_id
    assert Decimal(rebuilt.account(long_only.id)["quantity"]) >= 0
    assert all(
        Decimal(fill["position_after"] or "0") >= 0
        for fill in rebuilt.fills(long_only.id, 100)
    )
    assert rebuilt.strategy_state(long_only.id) is not None
    assert rebuilt.equity(long_only.id, 1)[0]["timestamp_ms"] == tail[-1].timestamp_ms
