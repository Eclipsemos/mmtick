import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_frozen_ensemble import combine_sparse_targets  # noqa: E402


def test_combine_sparse_targets_updates_only_from_known_current_states() -> None:
    left = (None, Decimal("0"), None, Decimal("1.5"), None)
    right = (None, None, Decimal("0.5"), None, Decimal("1.75"))

    combined = combine_sparse_targets(left, right)

    assert combined == (
        None,
        Decimal("0.5"),
        Decimal("0.25"),
        Decimal("1.0"),
        Decimal("1.625"),
    )


def test_combine_sparse_targets_rejects_exposure_above_cap() -> None:
    with pytest.raises(ValueError, match="exceeds exposure bounds"):
        combine_sparse_targets(
            (Decimal("4"),),
            (Decimal("4"),),
            maximum_exposure=Decimal("3"),
        )


def test_combine_sparse_targets_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="lengths differ"):
        combine_sparse_targets((Decimal("1"),), ())
