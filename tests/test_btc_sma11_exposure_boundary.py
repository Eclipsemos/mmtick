import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma11_exposure_boundary import ACTIVE_EXPOSURES, FUTURES_CAP


def test_exposure_boundary_has_ordered_predeclared_exposures_and_cap() -> None:
    assert ACTIVE_EXPOSURES == tuple(sorted(ACTIVE_EXPOSURES))
    assert ACTIVE_EXPOSURES[0] == Decimal("1.50")
    assert ACTIVE_EXPOSURES[-1] == Decimal("1.75")
    assert Decimal("1.51") in ACTIVE_EXPOSURES
    assert Decimal("1.54") in ACTIVE_EXPOSURES
    assert FUTURES_CAP == Decimal("2.5")
