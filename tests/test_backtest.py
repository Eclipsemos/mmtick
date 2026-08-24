from datetime import UTC, datetime
from decimal import Decimal

import pytest

from mastermind_tick.backtest import (
    OpenReplayTrade,
    ReplayATRTickStrategy,
    ReplayBroker,
    ReplayCandidate,
    ReplayParameters,
)
from mastermind_tick.config import InstrumentSettings
from mastermind_tick.models import Bar, FundingRate, Side, StrategySignal, Tick
from mastermind_tick.strategy import ATRTickStrategy

BAR_MS = 900_000


def instrument(*, paper_model: str = "spot") -> InstrumentSettings:
    return InstrumentSettings(
        id="test",
        symbol="TESTUSDT",
        display_symbol="TEST/USDT",
        name="Test instrument",
        asset_type="test",
        venue="test",
        currency="USDT",
        feed="test",
        quantity_step=0.01,
        reference_symbol="TEST",
        paper_model=paper_model,
        leverage=2 if paper_model == "futures" else 1,
    )


def bars(closes: list[float]) -> list[Bar]:
    return [
        Bar(
            start_ms=index * BAR_MS,
            end_ms=(index + 1) * BAR_MS - 1,
            open=Decimal(str(close)),
            high=Decimal(str(close + 0.5)),
            low=Decimal(str(close - 0.5)),
            close=Decimal(str(close)),
        )
        for index, close in enumerate(closes)
    ]


def tick(event_id: str, timestamp_ms: int, price: float) -> Tick:
    return Tick(
        event_id=event_id,
        timestamp_ms=timestamp_ms,
        price=Decimal(str(price)),
        quantity=Decimal("1"),
        source="test",
    )


@pytest.mark.parametrize("period,multiplier", [(7, 1.0), (14, 1.5), (21, 2.0)])
def test_replay_strategy_matches_production_tick_state(period: int, multiplier: float) -> None:
    warmup = bars([100 + (index % 9 - 4) * 0.3 for index in range(60)])
    production = ATRTickStrategy(period, multiplier, 15)
    replay = ReplayATRTickStrategy(period, multiplier, 15)
    production.bootstrap(warmup)
    replay.bootstrap(warmup)
    position_side = 0

    for index in range(360):
        bar_index, tick_index = divmod(index, 6)
        price = 100 + ((bar_index * 7 + tick_index * 3) % 17 - 8) * 0.22
        item = tick(
            f"tick-{index}",
            (60 + bar_index) * BAR_MS + tick_index * 30_000,
            price,
        )
        kwargs = {
            "has_position": position_side != 0,
            "has_pending_order": False,
            "allow_short": True,
            "is_short": position_side < 0,
        }
        expected = production.on_tick(item, **kwargs)
        actual = replay.on_tick(item, **kwargs)

        assert (actual.side if actual else None) == (expected.side if expected else None)
        assert (actual.reduce_only if actual else None) == (
            expected.reduce_only if expected else None
        )
        assert replay.last_atr == production.last_atr
        assert replay.trailing_stop == production.trailing_stop
        assert replay.previous_price == production.previous_price
        assert replay.bought_this_bar == production.bought_this_bar
        assert replay.flattened_this_bar == production.flattened_this_bar
        if expected is not None:
            production.on_fill(item.timestamp_ms, filled=True)
            replay.on_fill(item.timestamp_ms, filled=True)
            position_side = 0 if expected.reduce_only else 1 if expected.side is Side.BUY else -1


def test_spot_round_trip_net_pnl_includes_both_fees_and_slippage() -> None:
    broker = ReplayBroker(
        instrument(),
        Decimal("10000"),
        Decimal("1"),
        Decimal("10"),
        Decimal("5"),
        Decimal("5"),
    )

    assert broker.fill(Side.BUY, Decimal("100"), 1)
    assert broker.fill(Side.SELL, Decimal("110"), 2)

    trade = broker.trades[0]
    assert trade.direction == "LONG"
    assert trade.fees == trade.quantity * (trade.entry_price + trade.exit_price) * Decimal("0.001")
    assert trade.net_pnl == broker.cash - broker.initial_cash
    assert broker.quantity == 0


def test_futures_short_trade_includes_fees_slippage_and_funding() -> None:
    broker = ReplayBroker(
        instrument(paper_model="futures"),
        Decimal("10000"),
        Decimal("0.95"),
        Decimal("5"),
        Decimal("2"),
        Decimal("5"),
    )
    assert broker.fill(Side.SELL, Decimal("100"), 1)
    entry = broker.open_trade
    assert entry is not None
    funding = FundingRate(2, Decimal("0.001"), Decimal("95"))
    funding_amount = broker.apply_funding(funding)
    assert funding_amount > 0
    assert broker.fill(Side.BUY, Decimal("90"), 3, reduce_only=True)

    trade = broker.trades[0]
    expected_gross = trade.quantity * (trade.entry_price - trade.exit_price)
    assert trade.direction == "SHORT"
    assert trade.funding == funding_amount
    assert trade.net_pnl == expected_gross - trade.fees + funding_amount
    assert broker.quantity == 0
    assert broker.total_fees == trade.fees


def test_candidate_fills_signal_on_the_next_tick() -> None:
    strategy = ReplayATRTickStrategy(2, 0.75, 15, 2, 0)
    strategy.bootstrap(bars([10, 9, 8]))
    broker = ReplayBroker(
        instrument(),
        Decimal("10000"),
        Decimal("1"),
        Decimal("10"),
        Decimal("5"),
        Decimal("5"),
    )
    candidate = ReplayCandidate(
        parameters=ReplayParameters(2, 0.75),
        strategy=strategy,
        broker=broker,
    )

    candidate.process_tick(tick("signal", 3 * BAR_MS, 10), [])
    assert candidate.pending_signal is not None
    assert candidate.pending_signal.side is Side.BUY
    assert not broker.has_position

    candidate.process_tick(tick("fill", 3 * BAR_MS + 1, 10), [])
    assert candidate.pending_signal is None
    assert broker.has_position
    assert broker.average_price == Decimal("10.005")


def test_mark_tracks_tick_level_max_drawdown() -> None:
    broker = ReplayBroker(
        instrument(),
        Decimal("100"),
        Decimal("1"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )
    broker.cash = Decimal("0")
    broker.quantity = Decimal("1")
    broker.average_price = Decimal("100")
    broker.open_trade = OpenReplayTrade("LONG", 1, Decimal("100"), Decimal("1"), Decimal("0"))

    broker.mark(Decimal("120"))
    broker.mark(Decimal("90"))

    assert broker.max_drawdown == Decimal("-0.25")


def opened_candidate(
    parameters: ReplayParameters,
    side: Side = Side.BUY,
) -> ReplayCandidate:
    strategy = ReplayATRTickStrategy(2, 4, 15, 2, 0)
    strategy.bootstrap(bars([100, 99, 98]))
    strategy.previous_price = Decimal("100")
    strategy.trailing_stop = Decimal("90") if side is Side.BUY else Decimal("110")
    strategy.startup_alignment_checked = True
    broker = ReplayBroker(
        instrument(paper_model="futures"),
        Decimal("10000"),
        Decimal("0.5"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )
    assert broker.fill(side, Decimal("100"), 1)
    return ReplayCandidate(
        parameters=parameters,
        strategy=strategy,
        broker=broker,
        entry_atr=Decimal("1"),
        favorable_extreme=Decimal("100"),
    )


def test_fixed_atr_take_profit_closes_on_next_tick() -> None:
    candidate = opened_candidate(ReplayParameters(2, 4, variant="fixed", fixed_take_profit_atr=6))

    candidate.process_tick(tick("take-profit", 3 * BAR_MS, 106), [])

    assert candidate.pending_signal is not None
    assert candidate.pending_signal.reason == "fixed_atr_take_profit"
    assert candidate.pending_signal.reduce_only
    assert candidate.profit_exit_signals == 1

    candidate.process_tick(tick("take-profit-fill", 3 * BAR_MS + 1, 106), [])

    assert not candidate.broker.has_position
    assert candidate.broker.trades[0].net_pnl > 0


def test_profit_protection_activates_then_closes_on_retrace() -> None:
    candidate = opened_candidate(
        ReplayParameters(
            2,
            4,
            variant="profit_protection",
            profit_activation_atr=2,
            profit_trailing_atr=0.5,
        )
    )

    candidate.process_tick(tick("activate", 3 * BAR_MS, 104), [])
    assert candidate.profit_protection_active
    assert candidate.profit_stop is not None
    candidate.strategy.action_this_bar = False
    candidate.process_tick(
        tick("retrace", 3 * BAR_MS + BAR_MS, float(candidate.profit_stop)),
        [],
    )

    assert candidate.pending_signal is not None
    assert candidate.pending_signal.reason == "atr_profit_protection"
    assert candidate.profit_exit_signals == 1


def test_fixed_atr_take_profit_is_symmetric_for_short_position() -> None:
    candidate = opened_candidate(
        ReplayParameters(2, 4, variant="fixed", fixed_take_profit_atr=6),
        Side.SELL,
    )

    candidate.process_tick(tick("short-take-profit", 3 * BAR_MS, 94), [])

    assert candidate.pending_signal is not None
    assert candidate.pending_signal.side is Side.BUY
    assert candidate.pending_signal.reason == "fixed_atr_take_profit"
    candidate.process_tick(tick("short-take-profit-fill", 3 * BAR_MS + 1, 94), [])
    assert not candidate.broker.has_position
    assert candidate.broker.trades[0].net_pnl > 0


def test_profit_protection_is_symmetric_for_short_position() -> None:
    candidate = opened_candidate(
        ReplayParameters(
            2,
            4,
            variant="profit_protection",
            profit_activation_atr=2,
            profit_trailing_atr=0.5,
        ),
        Side.SELL,
    )

    candidate.process_tick(tick("short-activate", 3 * BAR_MS, 96), [])
    assert candidate.profit_protection_active
    assert candidate.profit_stop is not None
    candidate.strategy.action_this_bar = False
    candidate.process_tick(
        tick("short-retrace", 3 * BAR_MS + BAR_MS, float(candidate.profit_stop)),
        [],
    )

    assert candidate.pending_signal is not None
    assert candidate.pending_signal.side is Side.BUY
    assert candidate.pending_signal.reason == "atr_profit_protection"


def test_continuation_reentry_uses_actual_exit_and_only_next_bar() -> None:
    candidate = opened_candidate(
        ReplayParameters(
            2,
            4,
            variant="continuation",
            continuation_reentry_atr=0.5,
        )
    )
    candidate.pending_signal = StrategySignal(
        side=Side.SELL,
        reason="test_exit",
        signal_price=Decimal("99"),
        trailing_stop=Decimal("99"),
        atr=Decimal("1"),
        bar_start_ms=3 * BAR_MS,
        tick_id="exit-signal",
        reduce_only=True,
    )

    candidate.process_tick(tick("exit-fill", 3 * BAR_MS + 1, 99), [])

    assert not candidate.broker.has_position
    assert candidate.continuation_direction == "LONG"
    assert candidate.continuation_anchor == Decimal("99")
    candidate.strategy.trailing_stop = Decimal("98")
    candidate.strategy.last_trend_efficiency = Decimal("1")
    candidate.process_tick(tick("below-threshold", 4 * BAR_MS, 99.1), [])
    assert candidate.pending_signal is None
    candidate.process_tick(tick("confirmed", 4 * BAR_MS + 1, 100), [])
    assert candidate.pending_signal is not None
    assert candidate.pending_signal.side is Side.BUY
    assert candidate.pending_signal.reason == "confirmed_long_continuation"
    assert candidate.continuation_reentry_signals == 1


def test_continuation_reentry_expires_after_one_bar() -> None:
    candidate = opened_candidate(ReplayParameters(2, 4, continuation_reentry_atr=0))
    candidate.continuation_direction = "LONG"
    candidate.continuation_anchor = Decimal("100")
    candidate.continuation_eligible_bar_ms = 4 * BAR_MS
    candidate.broker.quantity = Decimal("0")
    candidate.broker.average_price = Decimal("0")
    candidate.broker.open_trade = None

    candidate.process_tick(tick("expired", 5 * BAR_MS, 110), [])

    assert candidate.pending_signal is None
    assert candidate.continuation_direction is None


def test_continuation_reentry_is_symmetric_for_short_position() -> None:
    candidate = opened_candidate(
        ReplayParameters(2, 4, continuation_reentry_atr=0.5),
        Side.SELL,
    )
    candidate.pending_signal = StrategySignal(
        side=Side.BUY,
        reason="test_exit",
        signal_price=Decimal("101"),
        trailing_stop=Decimal("101"),
        atr=Decimal("1"),
        bar_start_ms=3 * BAR_MS,
        tick_id="exit-signal",
        reduce_only=True,
    )
    candidate.process_tick(tick("exit-fill", 3 * BAR_MS + 1, 101), [])
    candidate.strategy.trailing_stop = Decimal("102")
    candidate.strategy.last_trend_efficiency = Decimal("1")

    candidate.process_tick(tick("confirmed", 4 * BAR_MS, 100), [])

    assert candidate.pending_signal is not None
    assert candidate.pending_signal.side is Side.SELL
    assert candidate.pending_signal.reason == "confirmed_short_continuation"


def test_session_reentry_scope_uses_beijing_session_boundaries() -> None:
    candidate = opened_candidate(
        ReplayParameters(
            2,
            4,
            session_reentry_threshold_atr=0.25,
            session_reentry_window_bars=2,
            session_reentry_scope="0816",
        )
    )

    sunday_0800_bjt = int(datetime(2026, 8, 9, 0, 0, tzinfo=UTC).timestamp() * 1000)
    tuesday_1600_bjt = int(datetime(2026, 8, 11, 8, 0, tzinfo=UTC).timestamp() * 1000)
    regular_open = int(datetime(2026, 8, 11, 13, 30, tzinfo=UTC).timestamp() * 1000)

    assert candidate._is_session_reentry_exit(sunday_0800_bjt)
    assert candidate._is_session_reentry_exit(tuesday_1600_bjt)
    assert not candidate._is_session_reentry_exit(regular_open)


def test_session_reentry_uses_frozen_stop_from_next_bar() -> None:
    candidate = opened_candidate(
        ReplayParameters(
            2,
            4,
            session_reentry_threshold_atr=0.25,
            session_reentry_window_bars=2,
            session_reentry_scope="0816",
        )
    )
    exit_fill_ms = int(datetime(2026, 8, 9, 0, 5, tzinfo=UTC).timestamp() * 1000)
    exit_bar_ms = exit_fill_ms // BAR_MS * BAR_MS
    assert candidate.session_reentry is not None
    candidate.session_reentry.pending_exit_stop = Decimal("99")
    candidate.pending_signal = StrategySignal(
        side=Side.SELL,
        reason="price_crossed_below_atr_stop",
        signal_price=Decimal("98"),
        trailing_stop=Decimal("103"),
        atr=Decimal("1"),
        bar_start_ms=exit_bar_ms,
        tick_id="exit-signal",
        reduce_only=True,
    )

    candidate.process_tick(tick("exit-fill", exit_fill_ms, 98), [])

    assert not candidate.broker.has_position
    assert candidate.session_reentry.anchor == Decimal("98")
    assert candidate.session_reentry.frozen_stop == Decimal("99")
    candidate.strategy.trailing_stop = Decimal("110")
    candidate.process_tick(tick("same-bar", exit_bar_ms + BAR_MS - 1, 105), [])
    assert candidate.pending_signal is None

    candidate.process_tick(tick("recovered", exit_bar_ms + BAR_MS, 105), [])

    assert candidate.pending_signal is not None
    assert candidate.pending_signal.reason == "session_recovery_reentry"
    assert candidate.pending_signal.side is Side.BUY
    assert candidate.session_reentry_signals == 1
