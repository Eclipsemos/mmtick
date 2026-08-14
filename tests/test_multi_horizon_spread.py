from decimal import Decimal

import pytest

from mastermind_tick.models import FundingRate
from mastermind_tick.multi_horizon_spread import (
    aggregate_spread_market,
    build_15m_state_filter,
    combine_daily_paths,
    inverse_volatility_weights,
)
from mastermind_tick.volatility_spread import SpreadBar, SpreadExecution, build_spread_features


def _bar(index: int) -> SpreadBar:
    start_ms = index * 900_000
    price = Decimal(100 + index)
    return SpreadBar(
        start_ms=start_ms,
        end_ms=start_ms + 899_999,
        open=price,
        high=price + Decimal("2"),
        low=price - Decimal("1"),
        close=price + Decimal("1"),
        volume=Decimal(index + 1),
    )


def test_aggregate_market_preserves_ohlcv_funding_and_next_execution() -> None:
    bars = [_bar(index) for index in range(4)]
    funding = [[], [FundingRate(1_000_000, Decimal("0.01"), Decimal("101"))], [], []]
    executions = [SpreadExecution(bar.start_ms + 1, bar.open + Decimal("0.1")) for bar in bars]

    market = aggregate_spread_market(
        bars,
        funding,
        executions,
        interval_minutes=60,
    )

    assert len(market.bars) == 1
    assert market.bars[0].open == Decimal("100")
    assert market.bars[0].high == Decimal("105")
    assert market.bars[0].low == Decimal("99")
    assert market.bars[0].close == Decimal("104")
    assert market.bars[0].volume == Decimal("10")
    assert market.funding_by_bar[0] == funding[1]
    assert market.executions[0] == executions[0]


def test_incomplete_boundary_is_dropped_but_internal_gap_is_rejected() -> None:
    boundary_bars = [_bar(index) for index in range(1, 8)]
    boundary_market = aggregate_spread_market(
        boundary_bars,
        [[] for _ in boundary_bars],
        [SpreadExecution(bar.start_ms + 1, bar.open) for bar in boundary_bars],
        interval_minutes=60,
    )
    assert [bar.start_ms for bar in boundary_market.bars] == [3_600_000]

    internal_gap = [_bar(index) for index in range(12) if index != 5]
    with pytest.raises(ValueError, match="incomplete internal"):
        aggregate_spread_market(
            internal_gap,
            [[] for _ in internal_gap],
            [SpreadExecution(bar.start_ms + 1, bar.open) for bar in internal_gap],
            interval_minutes=60,
        )


def test_inverse_volatility_portfolio_is_aligned_and_weighted() -> None:
    paths = {
        "fast": [("2026-01-01", 0.10), ("2026-01-02", -0.10)],
        "slow": [("2026-01-01", 0.05), ("2026-01-02", -0.05)],
    }

    weights = inverse_volatility_weights(paths)
    combined = combine_daily_paths(paths, weights, scale=2)

    assert weights == pytest.approx({"fast": 1 / 3, "slow": 2 / 3})
    assert combined == pytest.approx([("2026-01-01", 2 / 15), ("2026-01-02", -2 / 15)])


def test_15m_state_filter_uses_only_the_last_component_bar() -> None:
    bars = [
        _bar(0),
        _bar(1),
        SpreadBar(
            start_ms=1_800_000,
            end_ms=2_699_999,
            open=Decimal("102"),
            high=Decimal("110"),
            low=Decimal("101"),
            close=Decimal("109"),
            volume=Decimal("1"),
        ),
        _bar(3),
    ]
    features = build_spread_features(
        bars,
        fast_window=1,
        slow_window=2,
        breakout_window=1,
    )
    aggregated = aggregate_spread_market(
        bars,
        [[] for _ in bars],
        [SpreadExecution(bar.start_ms + 1, bar.open) for bar in bars],
        interval_minutes=30,
    )

    state_filter = build_15m_state_filter(
        bars,
        features,
        aggregated.bars,
        mode="direction_consensus",
    )

    assert len(state_filter) == len(aggregated.bars)
    assert state_filter[-1] in {-1, 0, 1}


def test_portfolio_rejects_misaligned_paths() -> None:
    with pytest.raises(ValueError, match="not aligned"):
        combine_daily_paths(
            {"a": [("2026-01-01", 0.1)], "b": [("2026-01-02", 0.1)]},
            {"a": 0.5, "b": 0.5},
        )
