import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_sma12_bear_vol_floor import (  # noqa: E402
    calm_bear_floor,
    rolling_medians,
)


def test_rolling_medians_use_only_current_and_past_values() -> None:
    values = tuple(Decimal(value) for value in ("4", "1", "3", "2"))

    assert rolling_medians(values, 3) == (
        None,
        None,
        Decimal("3"),
        Decimal("2"),
    )


def test_calm_bear_floor_only_changes_bear_below_median() -> None:
    targets = (None, Decimal("0"), Decimal("0"), Decimal("1.25"))
    ratios = (None, Decimal("0.02"), Decimal("0.04"), Decimal("0.01"))
    medians = (None, Decimal("0.03"), Decimal("0.03"), Decimal("0.03"))

    assert calm_bear_floor(targets, ratios, medians, Decimal("0.25")) == (
        None,
        Decimal("0.25"),
        Decimal("0"),
        Decimal("1.25"),
    )


def test_calm_bear_floor_is_causal_when_future_values_are_appended() -> None:
    values = tuple(Decimal(value) for value in ("4", "1", "3"))
    original = rolling_medians(values, 3)
    extended = rolling_medians(values + (Decimal("100"),), 3)

    assert extended[: len(original)] == original


def test_calm_bear_floor_rejects_mismatched_streams() -> None:
    with pytest.raises(ValueError, match="equal lengths"):
        calm_bear_floor((Decimal("0"),), (), (), Decimal("0.25"))
