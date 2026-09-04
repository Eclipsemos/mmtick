import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_donchian_matched_benchmark import WINDOWS


def test_donchian_windows_are_ordered_and_valid() -> None:
    assert WINDOWS == tuple(sorted(WINDOWS))
    assert all(entry > exit_window > 0 for entry, exit_window in WINDOWS)
