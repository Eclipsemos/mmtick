import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_funding_hysteresis_strict import funding_gate_hysteresis  # noqa: E402

from mastermind_tick.models import FundingRate  # noqa: E402


def test_gate_waits_for_consecutive_funding_events_and_restores() -> None:
    funding = [
        [],
        [FundingRate(1, Decimal("0.0002"), Decimal("100"))],
        [],
        [FundingRate(2, Decimal("0.0002"), Decimal("100"))],
        [FundingRate(3, Decimal("0.0000"), Decimal("100"))],
        [FundingRate(4, Decimal("0.0000"), Decimal("100"))],
    ]
    targets = funding_gate_hysteresis((Decimal("1.5"),) * len(funding), funding, enter=2, exit=2)
    assert targets == (Decimal("1.5"), None, None, Decimal("1"), None, Decimal("1.5"))


def test_gate_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="equal lengths"):
        funding_gate_hysteresis((Decimal("1"),), [], enter=1, exit=1)
