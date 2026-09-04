import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_momentum_gated_3x import summarize, tail_concentration  # noqa: E402


def test_summary_reports_win_rates_and_liquidation_count() -> None:
    result = summarize(
        [
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
    )

    assert result["return_win_rate"] == pytest.approx(0.5)
    assert result["liquidations"] == 1


def test_tail_concentration_removes_ranked_relative_days() -> None:
    strategy = (0.3, 0.1, -0.1) + (0.0,) * 22
    benchmark = (0.0,) * 25
    result = tail_concentration(strategy, benchmark)

    assert result[0]["removed_best_relative_days"] == 0
    assert result[1]["removed_best_relative_days"] == 1
