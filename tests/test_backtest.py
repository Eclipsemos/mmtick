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
from mastermind_tick.models import Bar, FundingRate, Side, Tick
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
            position_side = (
                0
                if expected.reduce_only
                else 1 if expected.side is Side.BUY else -1
            )


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
    assert trade.fees == trade.quantity * (
        trade.entry_price + trade.exit_price
    ) * Decimal("0.001")
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
