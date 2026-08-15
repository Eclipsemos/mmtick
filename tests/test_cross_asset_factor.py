from decimal import Decimal

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.cross_asset_factor import (
    CrossAssetCandidate,
    causal_asset_scores,
    evaluate_portfolio_targets,
    factor_targets,
)
from mastermind_tick.models import FundingRate
from mastermind_tick.pair_research import PairBar


def _pair_bars(count: int) -> list[PairBar]:
    result = []
    for index in range(count):
        start = index * 240 * 60_000
        left_close = Decimal("100") + Decimal(index)
        right_close = Decimal("80") + Decimal(index) / Decimal("2")
        left = ResearchBar(
            start_ms=start,
            end_ms=start + 240 * 60_000 - 1,
            open=left_close - Decimal("0.5"),
            high=left_close + Decimal("1"),
            low=left_close - Decimal("1"),
            close=left_close,
            volume=Decimal("100"),
        )
        right = ResearchBar(
            start_ms=start,
            end_ms=start + 240 * 60_000 - 1,
            open=right_close - Decimal("0.25"),
            high=right_close + Decimal("1"),
            low=right_close - Decimal("1"),
            close=right_close,
            volume=Decimal("100"),
        )
        result.append(PairBar(timestamp_ms=start, end_ms=left.end_ms, left=left, right=right))
    return result


def test_cross_asset_features_are_causal_under_future_append() -> None:
    bars = _pair_bars(40)
    empty_funding = [[] for _bar in bars]
    original = causal_asset_scores(
        bars,
        empty_funding,
        empty_funding,
        lookback=4,
        normalization_window=8,
        feature_set="all_equal",
    )
    extended = _pair_bars(45)
    extended_funding = [[] for _bar in extended]
    appended = causal_asset_scores(
        extended,
        extended_funding,
        extended_funding,
        lookback=4,
        normalization_window=8,
        feature_set="all_equal",
    )

    assert appended[0][: len(bars)] == original[0]
    assert appended[1][: len(bars)] == original[1]


def test_rotation_targets_respect_minimum_hold() -> None:
    candidate = CrossAssetCandidate(
        interval_minutes=1440,
        lookback_days=3,
        feature_set="momentum",
        family="rotation",
        direction="long_only",
        threshold=Decimal("0"),
        minimum_hold_days=3,
    )

    targets = factor_targets(
        (Decimal("2"), Decimal("0"), Decimal("0"), Decimal("2")),
        (Decimal("0"), Decimal("2"), Decimal("2"), Decimal("0")),
        candidate,
        _pair_bars(4),
    )

    assert targets == (
        (Decimal("1"), Decimal("0")),
        (Decimal("1"), Decimal("0")),
        (Decimal("1"), Decimal("0")),
        (Decimal("1"), Decimal("0")),
    )


def test_adaptive_relative_factor_uses_only_realized_history() -> None:
    bars = _pair_bars(12)
    candidate = CrossAssetCandidate(
        interval_minutes=1440,
        lookback_days=3,
        feature_set="momentum",
        family="relative_adaptive",
        direction="long_short",
        threshold=Decimal("0"),
        minimum_hold_days=0,
        adaptation_days=3,
    )
    left = tuple(Decimal("1") for _bar in bars)
    right = tuple(Decimal("0") for _bar in bars)

    original = factor_targets(left, right, candidate, bars)
    extended_bars = _pair_bars(14)
    extended = factor_targets(
        tuple(Decimal("1") for _bar in extended_bars),
        tuple(Decimal("0") for _bar in extended_bars),
        candidate,
        extended_bars,
    )

    assert extended[: len(bars)] == original


def test_flat_portfolio_has_no_cost_or_return() -> None:
    bars = _pair_bars(6)
    funding = [[] for _bar in bars]
    targets = tuple((Decimal("0"), Decimal("0")) for _bar in bars)

    result = evaluate_portfolio_targets(
        bars,
        targets,
        funding,
        funding,
        start_ms=bars[0].timestamp_ms,
        end_ms=bars[-1].timestamp_ms,
    )

    assert result.net_return == 0
    assert result.completed_trades == 0
    assert result.total_fees == 0


def test_portfolio_rejects_gross_weight_above_one() -> None:
    bars = _pair_bars(4)
    funding = [[] for _bar in bars]
    targets = tuple((Decimal("1"), Decimal("0.5")) for _bar in bars)

    with pytest.raises(ValueError, match="gross weight"):
        evaluate_portfolio_targets(
            bars,
            targets,
            funding,
            funding,
            start_ms=bars[0].timestamp_ms,
            end_ms=bars[-1].timestamp_ms,
        )


def test_long_portfolio_profit_and_funding_are_applied() -> None:
    bars = _pair_bars(8)
    targets = tuple((Decimal("1"), Decimal("0")) for _bar in bars)
    empty_funding = [[] for _bar in bars]
    charged_funding = [[] for _bar in bars]
    charged_funding[4] = [
        FundingRate(
            timestamp_ms=bars[4].timestamp_ms,
            rate=Decimal("0.01"),
            mark_price=bars[4].left.close,
        )
    ]

    without_funding = evaluate_portfolio_targets(
        bars,
        targets,
        empty_funding,
        empty_funding,
        start_ms=bars[0].timestamp_ms,
        end_ms=bars[-1].timestamp_ms,
    )
    with_funding = evaluate_portfolio_targets(
        bars,
        targets,
        charged_funding,
        empty_funding,
        start_ms=bars[0].timestamp_ms,
        end_ms=bars[-1].timestamp_ms,
    )

    assert without_funding.net_return > 0
    assert with_funding.total_funding < 0
    assert with_funding.net_return < without_funding.net_return
