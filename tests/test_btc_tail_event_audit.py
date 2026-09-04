import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_tail_event_audit import (  # noqa: E402
    dense_active_exposures,
    last_change_at_or_before,
)


def test_sparse_signal_becomes_active_on_next_bar() -> None:
    targets = (Decimal("0.5"), None, None, Decimal("1.75"), None)

    exposures = dense_active_exposures(targets)

    assert exposures == (
        Decimal("1"),
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("1.75"),
    )


def test_last_change_finds_state_establishing_current_exposure() -> None:
    exposures = (
        Decimal("1"),
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("1.75"),
    )

    assert last_change_at_or_before(exposures, 3) == 1
    assert last_change_at_or_before(exposures, 4) == 4


def test_dense_exposure_rejects_more_than_three_x() -> None:
    with pytest.raises(ValueError, match="between zero and three"):
        dense_active_exposures((Decimal("3.1"), None))
