import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_sma_matched_benchmark_grid import ENTER_DAYS, FAST_PERIODS


def test_matched_benchmark_grid_is_predeclared() -> None:
    assert FAST_PERIODS == (8, 9, 10, 11, 12)
    assert ENTER_DAYS == (1, 2, 3)
