import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma_ensemble_exposure import (  # noqa: E402
    EXPOSURES,
    build_dense_targets_with_active,
)


def test_exposure_grid_is_predeclared_and_within_requested_range() -> None:
    assert EXPOSURES == (
        Decimal("1.5"),
        Decimal("1.6"),
        Decimal("1.7"),
        Decimal("1.75"),
        Decimal("1.8"),
    )


def test_dense_target_uses_active_when_not_bearish() -> None:
    class Bar:
        close = Decimal("110")

    targets = build_dense_targets_with_active(
        [Bar()] * 2,
        1,
        2,
        Decimal("0"),
        Decimal("1.7"),
    )
    assert targets == (None, Decimal("1.7"))
