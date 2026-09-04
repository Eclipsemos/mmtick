import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_eth_ratio_filter_strict import ratio_hysteresis  # noqa: E402


def test_ratio_filter_is_causal_and_marks_bearish_ordering() -> None:
    ratio = tuple(Decimal(str(value)) for value in ([1] * 4 + [0.9, 0.8, 0.7]))
    state = ratio_hysteresis(ratio, fast_period=2, slow_period=3)
    assert state[:4] == ("unknown", "unknown", "active", "active")
    assert state[-1] == "bear"
