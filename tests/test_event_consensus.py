from decimal import Decimal

import pytest

from mastermind_tick.event_consensus import ConsensusConfig, consensus_targets


def test_consensus_requires_active_count_and_agreement() -> None:
    result = consensus_targets(
        (
            (None, 1, 1, 1),
            (None, 0, 1, -1),
            (None, 0, 0, 1),
        ),
        ConsensusConfig(2, Decimal("0.6"), "follow", Decimal("2")),
    )

    assert result == (None, Decimal("0"), Decimal("2"), Decimal("0"))


def test_fade_consensus_inverts_only_the_current_vote() -> None:
    original = ((1, 1, 0), (1, 0, 0), (1, 1, 0))
    result = consensus_targets(
        original,
        ConsensusConfig(2, Decimal("0.5"), "fade", Decimal("1.5")),
    )

    assert result == (Decimal("-1.5"), Decimal("-1.5"), Decimal("0"))


def test_consensus_prefix_does_not_change_with_future_votes() -> None:
    config = ConsensusConfig(1, Decimal("1"), "follow", Decimal("1"))
    original = consensus_targets(((1, 0), (1, 0)), config)
    extended = consensus_targets(((1, 0, -1), (1, 0, -1)), config)

    assert extended[:2] == original


def test_consensus_rejects_misaligned_members() -> None:
    with pytest.raises(ValueError, match="lengths differ"):
        consensus_targets(
            ((1, 0), (1,)),
            ConsensusConfig(1, Decimal("1"), "follow", Decimal("1")),
        )
