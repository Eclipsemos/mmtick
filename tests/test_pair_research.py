from decimal import Decimal

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.models import FundingRate
from mastermind_tick.pair_research import (
    align_pair_bars,
    evaluate_pair_targets,
    ratio_momentum_targets,
)


def _bar(index: int, open_price: str, close_price: str) -> ResearchBar:
    start_ms = index * 86_400_000
    open_value = Decimal(open_price)
    close_value = Decimal(close_price)
    return ResearchBar(
        start_ms=start_ms,
        end_ms=start_ms + 86_400_000 - 1,
        open=open_value,
        high=max(open_value, close_value),
        low=min(open_value, close_value),
        close=close_value,
    )


def test_pair_bars_must_share_timestamps() -> None:
    left = [_bar(0, "100", "101")]
    right = [_bar(1, "50", "51")]

    with pytest.raises(ValueError, match="not aligned"):
        align_pair_bars(left, right)


def test_pair_target_executes_both_legs_and_applies_funding() -> None:
    left = [_bar(i, str(100 + i * 5), str(105 + i * 5)) for i in range(4)]
    right = [_bar(i, str(50 + i), str(51 + i)) for i in range(4)]
    bars = align_pair_bars(left, right)
    funding_left = [
        [],
        [
            FundingRate(
                timestamp_ms=bars[1].timestamp_ms,
                rate=Decimal("0.01"),
                mark_price=Decimal("110"),
            )
        ],
        [],
        [],
    ]
    funding_right = [[], [], [], []]

    result = evaluate_pair_targets(
        bars,
        (1, 1, 0, 0),
        funding_left,
        funding_right,
        start_ms=bars[0].timestamp_ms,
        end_ms=bars[-1].timestamp_ms,
        initial_equity=Decimal("1000"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.completed_trades == 1
    assert result.trades[0].entry_at_ms == bars[1].timestamp_ms
    assert result.trades[0].exit_at_ms == bars[3].timestamp_ms
    assert result.total_funding < 0
    assert result.final_equity > 1000


def test_pair_momentum_uses_ratio_not_absolute_price() -> None:
    left = [_bar(0, "100", "100"), _bar(1, "110", "110"), _bar(2, "120", "120")]
    right = [_bar(0, "50", "50"), _bar(1, "50", "50"), _bar(2, "50", "50")]
    bars = align_pair_bars(left, right)

    assert ratio_momentum_targets(bars, lookback=1, threshold=0.05) == (None, 1, 1)


def test_pair_round_trip_slippage_reduces_equity() -> None:
    left = [_bar(i, "100", "100") for i in range(3)]
    right = [_bar(i, "50", "50") for i in range(3)]
    bars = align_pair_bars(left, right)

    result = evaluate_pair_targets(
        bars,
        (1, 0, 0),
        [[], [], []],
        [[], [], []],
        start_ms=bars[0].timestamp_ms,
        end_ms=bars[-1].timestamp_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("10"),
    )

    assert result.final_equity < result.initial_equity
