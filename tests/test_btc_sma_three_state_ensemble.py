import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_sma_three_state_ensemble import equal_weight_targets  # noqa: E402


def test_equal_weight_targets_average_known_members() -> None:
    streams = (
        (None, Decimal("0"), Decimal("1.5")),
        (None, Decimal("1"), Decimal("1")),
    )

    assert equal_weight_targets(streams) == (
        None,
        Decimal("0.5"),
        Decimal("1.25"),
    )


def test_equal_weight_targets_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="equally sized"):
        equal_weight_targets(((Decimal("1"),), (Decimal("1"), Decimal("1"))))
