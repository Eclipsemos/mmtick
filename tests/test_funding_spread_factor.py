from decimal import Decimal

import pytest

from mastermind_tick.funding_spread_factor import (
    FundingSpreadCandidate,
    funding_spread_scores,
    funding_spread_targets,
)
from mastermind_tick.models import FundingRate


def _rate(value: str) -> FundingRate:
    return FundingRate(1, Decimal(value), Decimal("100"))


def test_funding_spread_score_is_trailing_and_expressed_in_bps() -> None:
    btc = [[_rate("0.0001")], [], [_rate("0.0003")]]
    eth = [[_rate("0.0002")], [_rate("0.0001")], []]

    scores = funding_spread_scores(btc, eth, 2)

    assert scores == (Decimal("-1.0000"), Decimal("-2.0000"), Decimal("2.0000"))


def test_carry_targets_short_the_higher_funding_asset() -> None:
    candidate = FundingSpreadCandidate(6, Decimal("1"), "carry", 1, 1)

    btc, eth = funding_spread_targets((Decimal("2"), Decimal("-2")), candidate)

    assert btc == (Decimal("-1"), Decimal("1"))
    assert eth == (Decimal("1"), Decimal("-1"))


def test_funding_targets_require_confirmation() -> None:
    candidate = FundingSpreadCandidate(6, Decimal("1"), "crowding_follow", 1, 2)

    btc, _eth = funding_spread_targets(
        (Decimal("2"), Decimal("0"), Decimal("2"), Decimal("2")), candidate
    )

    assert btc == (Decimal("0"), Decimal("0"), Decimal("0"), Decimal("1"))


def test_funding_candidate_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        FundingSpreadCandidate(6, Decimal("1"), "unknown", 1, 1)
