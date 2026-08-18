from decimal import Decimal

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.pair_research import (
    align_pair_bars,
    relative_shock_targets,
    short_horizon_ratio_targets,
)


def _bar(index: int, left: str, right: str):
    start_ms = index * 900_000
    left_value = Decimal(left)
    right_value = Decimal(right)
    return (
        ResearchBar(start_ms, start_ms + 899_999, left_value, left_value, left_value, left_value),
        ResearchBar(
            start_ms, start_ms + 899_999, right_value, right_value, right_value, right_value
        ),
    )


def test_short_horizon_ratio_signal_is_causal_and_time_limited() -> None:
    values = [
        _bar(0, "100", "100"),
        _bar(1, "101", "100"),
        _bar(2, "100", "100"),
        _bar(3, "120", "100"),
        _bar(4, "121", "100"),
        _bar(5, "122", "100"),
    ]
    bars = align_pair_bars([item[0] for item in values], [item[1] for item in values])

    targets = short_horizon_ratio_targets(bars, 3, 1.0, 0.0, 2)

    assert targets[:3] == (None, None, None)
    assert targets[3] == -1
    assert targets[4] == -1
    assert targets[5] == 0


def test_relative_shock_modes_take_opposite_pair_directions() -> None:
    values = [
        _bar(0, "100", "100"),
        _bar(1, "101", "100"),
        _bar(2, "100", "100"),
        _bar(3, "101", "100"),
        _bar(4, "120", "100"),
    ]
    bars = align_pair_bars([item[0] for item in values], [item[1] for item in values])

    continuation = relative_shock_targets(bars, 3, 1.0, 2, "continuation")
    reversion = relative_shock_targets(bars, 3, 1.0, 2, "reversion")

    assert continuation[-1] == 1
    assert reversion[-1] == -1
