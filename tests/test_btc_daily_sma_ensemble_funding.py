import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_daily_sma_ensemble_funding import apply_funding_gate  # noqa: E402

from mastermind_tick.models import FundingRate  # noqa: E402


def test_funding_gate_uses_only_the_latest_known_event() -> None:
    targets = (None, Decimal("1.5"), None, None)
    funding = (
        (),
        (FundingRate(1, Decimal("0.0001"), Decimal("100")),),
        (),
        (FundingRate(4, Decimal("0.0003"), Decimal("100")),),
    )

    gated = apply_funding_gate(targets, funding, Decimal("0.0002"))

    assert gated == (Decimal("0"), Decimal("1.5"), Decimal("1.5"), Decimal("1"))


def test_funding_gate_preserves_bear_exposure() -> None:
    targets = (Decimal("-0.1"), Decimal("-0.1"))
    funding = (
        (FundingRate(1, Decimal("0.001"), Decimal("100")),),
        (),
    )

    assert apply_funding_gate(targets, funding, Decimal("0.0002")) == targets
