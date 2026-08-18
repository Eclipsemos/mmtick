from array import array
from decimal import Decimal

import pytest

from mastermind_tick.high_frequency_research import (
    ExecutionCost,
    feature_scores,
    replay_fixed_hold,
    rolling_prior_zscore,
    threshold_events,
)


def test_rolling_zscore_uses_only_prior_window() -> None:
    scores = rolling_prior_zscore([1.0] * 8 + [3.0], 8)

    assert all(value != value for value in scores[:8])
    assert scores[8] > 1_000_000


def test_fixed_hold_signal_fills_on_next_bar() -> None:
    result = replay_fixed_hold(
        array("q", [0, 60_000, 120_000, 180_000]),
        array("d", [90.0, 100.0, 110.0, 110.0]),
        ((0, 1),),
        hold_bars=1,
        start_ms=0,
        end_ms=180_000,
        cost=ExecutionCost("zero", Decimal("0"), Decimal("0")),
    )

    assert result.completed_trades == 1
    assert result.net_return == pytest.approx(0.10)
    assert result.average_gross_bps == pytest.approx(1_000)


def test_round_trip_taker_cost_can_overwhelm_small_edge() -> None:
    result = replay_fixed_hold(
        array("q", [0, 60_000, 120_000]),
        array("d", [100.0, 100.0, 100.1]),
        ((0, 1),),
        hold_bars=1,
        start_ms=0,
        end_ms=120_000,
        cost=ExecutionCost("base", Decimal("5"), Decimal("2")),
    )

    assert result.net_return < 0
    assert result.approximate_break_even_bps_per_fill == pytest.approx(5.0)


def test_feature_library_and_threshold_events_are_symmetric() -> None:
    closes = array("d", [100 + index * 0.1 for index in range(30)])
    imbalance = array("d", [0.0] * 29 + [1.0])
    notionals = array("d", [100.0] * 30)

    features = feature_scores(closes, imbalance, imbalance, notionals, 8)
    follow = threshold_events(features["tick_flow_follow"], 1.0)
    revert = threshold_events(features["tick_flow_revert"], 1.0)

    assert follow[-1][1] == 1
    assert revert[-1][1] == -1
