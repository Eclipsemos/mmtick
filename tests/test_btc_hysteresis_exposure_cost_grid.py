import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_hysteresis_exposure_cost_grid import hysteresis_targets  # noqa: E402


class Bar:
    def __init__(self, close):
        self.close = Decimal(str(close))


def test_hysteresis_requires_configured_bear_confirmation() -> None:
    bars = [Bar(value) for value in ([100] * 40 + [99, 98, 100, 101])]
    # With a slow SMA of 2 and fast SMA of 1, two bearish observations are
    # required before leaving the active state.
    targets = hysteresis_targets(bars, fast_period=1, enter=2, exit=1, active=Decimal("1.5"))
    assert targets[-1] == Decimal("1.5")


def test_hysteresis_returns_zero_in_confirmed_bear_state() -> None:
    bars = [Bar(value) for value in ([100] * 40 + [90, 80, 70, 60, 50])]
    targets = hysteresis_targets(bars, fast_period=1, enter=1, exit=1, active=Decimal("1.5"))
    assert targets[-1] == Decimal("0")
