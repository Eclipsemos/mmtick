import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma11_active2_strict3x import FUTURES_CAP


def test_active_two_x_candidate_uses_three_x_futures_cap() -> None:
    assert FUTURES_CAP == Decimal("3")
