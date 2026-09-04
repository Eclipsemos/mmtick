import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma10_hysteresis_funding_strict import funding_gate  # noqa: E402

from mastermind_tick.models import FundingRate  # noqa: E402


def test_funding_gate_reduces_only_active_high_funding_target() -> None:
    funding = [
        [],
        [FundingRate(1, Decimal("0.0002"), Decimal("100"))],
        [FundingRate(2, Decimal("-0.0002"), Decimal("100"))],
    ]

    assert funding_gate(
        (Decimal("1.5"), Decimal("1.5"), Decimal("0")),
        funding,
        Decimal("0.0001"),
    ) == (
        Decimal("1.5"),
        Decimal("1"),
        Decimal("0"),
    )


def test_funding_gate_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="equal lengths"):
        funding_gate((Decimal("1"),), [], Decimal("0.0001"))
