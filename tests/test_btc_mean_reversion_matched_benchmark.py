import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_mean_reversion_matched_benchmark import BOLLINGER, RSI


def test_mean_reversion_grid_is_predeclared_and_valid() -> None:
    assert len(BOLLINGER) == 4
    assert len(RSI) == 4
    assert all(period > 1 and deviation > 0 for period, deviation in BOLLINGER)
    assert all(period > 1 and 0 < lower < 50 for period, lower in RSI)
