import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_macro_gated_3x import (  # noqa: E402
    summarize_windows,
    tail_concentration,
)


def test_tail_concentration_removes_best_relative_observations() -> None:
    strategy = (0.3, 0.1, -0.1) + (0.0,) * 22
    benchmark = (0.0,) * 25
    result = tail_concentration(strategy, benchmark)

    assert result[0]["removed_best_relative_days"] == 0
    assert result[1]["removed_best_relative_days"] == 1
    assert result[1]["strategy_cagr"] < result[0]["strategy_cagr"]


def test_rolling_summary_counts_wins_and_liquidations() -> None:
    rows = [
        {
            "excess_return": 0.2,
            "beats_return": True,
            "beats_return_and_drawdown": True,
            "liquidated": False,
        },
        {
            "excess_return": -0.1,
            "beats_return": False,
            "beats_return_and_drawdown": False,
            "liquidated": True,
        },
    ]

    result = summarize_windows(rows)

    assert result["return_win_rate"] == pytest.approx(0.5)
    assert result["return_and_drawdown_win_rate"] == pytest.approx(0.5)
    assert result["liquidations"] == 1
    assert result["median_excess"] == pytest.approx(0.05)
