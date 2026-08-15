from decimal import Decimal

import pytest

from mastermind_tick.funding_event_factor import (
    FundingEventCandidate,
    funding_event_scores,
    funding_event_targets,
)
from mastermind_tick.models import FundingRate


def _rate(value: str) -> FundingRate:
    return FundingRate(1, Decimal(value), Decimal("100"))


def test_funding_event_score_excludes_the_current_event_from_normalization() -> None:
    funding = [[_rate(value)] for value in ("0", "0.0001", "0", "0.0001", "0", "0.001")]

    scores = funding_event_scores(funding, 5)

    assert scores[:5] == (None,) * 5
    assert scores[-1] is not None
    assert scores[-1] > Decimal("10")


def test_funding_reversal_holds_for_fixed_number_of_closed_bar_signals() -> None:
    candidate = FundingEventCandidate(30, Decimal("2"), 2, "reversal", "long_short")

    targets = funding_event_targets((Decimal("3"), None, None), candidate)

    assert targets == (Decimal("-1"), Decimal("-1"), Decimal("0"))


def test_long_only_event_ignores_short_signal() -> None:
    candidate = FundingEventCandidate(30, Decimal("2"), 2, "continuation", "long_only")

    assert funding_event_targets((Decimal("-3"), None), candidate) == (
        Decimal("0"),
        Decimal("0"),
    )


def test_funding_event_candidate_rejects_invalid_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        FundingEventCandidate(30, Decimal("2"), 2, "reversal", "short_only")
