from decimal import Decimal

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.factor_stability import (
    FactorStabilityConfig,
    causal_market_regimes,
    cost_aware_forward_returns,
    normal_ic_p_value,
    percentile_ranks,
    spearman_ic,
)
from mastermind_tick.models import FundingRate


def _bars(count: int) -> list[ResearchBar]:
    return [
        ResearchBar(
            start_ms=index * 240 * 60_000,
            end_ms=(index + 1) * 240 * 60_000 - 1,
            open=Decimal("100") + Decimal(index),
            high=Decimal("101") + Decimal(index),
            low=Decimal("99") + Decimal(index),
            close=Decimal("100.5") + Decimal(index),
            volume=Decimal("1000"),
        )
        for index in range(count)
    ]


def test_cost_aware_label_deducts_round_trip_cost_and_funding() -> None:
    np = pytest.importorskip("numpy")
    bars = _bars(8)
    funding = [[] for _ in bars]
    funding[1] = [
        FundingRate(
            timestamp_ms=bars[1].start_ms,
            rate=Decimal("0.001"),
            mark_price=bars[1].close,
        )
    ]

    labels = cost_aware_forward_returns(np, bars, funding, 1, 5.0, 2.0)

    raw = float(bars[2].open / bars[1].open - Decimal("1"))
    assert labels["raw"][0] == pytest.approx(raw)
    assert labels["cost_adjusted"][0] == pytest.approx(raw - 0.0014 - 0.001)


def test_spearman_ic_uses_average_tie_ranks() -> None:
    assert percentile_ranks([1.0, 1.0, 3.0]) == pytest.approx([0.25, 0.25, 1.0])
    assert spearman_ic([1.0, 2.0, 3.0], [9.0, 7.0, 1.0]) == pytest.approx(-1.0)
    assert spearman_ic([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None


def test_market_regime_is_causal_when_future_bars_are_appended() -> None:
    np = pytest.importorskip("numpy")
    config = FactorStabilityConfig(
        trend_window=12,
        volatility_window=12,
        volatility_history=24,
    )
    original = causal_market_regimes(np, _bars(80), config)
    extended = causal_market_regimes(np, _bars(100), config)

    assert original == extended[:80]
    assert any(value is not None for value in original)


def test_normal_ic_p_value_rewards_positive_ic_and_sample_size() -> None:
    assert normal_ic_p_value(0.1, 1000) < normal_ic_p_value(0.1, 100)
    assert normal_ic_p_value(-0.1, 100) > 0.5
