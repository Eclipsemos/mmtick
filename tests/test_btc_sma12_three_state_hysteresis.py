import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_sma12_three_state_hysteresis import apply_bear_hysteresis  # noqa: E402


def test_bear_hysteresis_waits_two_days_and_recovers_in_one() -> None:
    raw = (
        None,
        Decimal("1.5"),
        Decimal("0"),
        Decimal("0"),
        Decimal("1.25"),
    )

    assert apply_bear_hysteresis(raw) == (
        None,
        Decimal("1.5"),
        Decimal("1.5"),
        Decimal("0"),
        Decimal("1.25"),
    )


def test_bear_hysteresis_updates_non_bear_exposure_without_delay() -> None:
    raw = (Decimal("1.25"), Decimal("1.5"), Decimal("1.25"))

    assert apply_bear_hysteresis(raw) == raw


def test_bear_hysteresis_is_causal_when_future_targets_are_appended() -> None:
    raw = (Decimal("1.5"), Decimal("0"), Decimal("0"))
    original = apply_bear_hysteresis(raw)
    extended = apply_bear_hysteresis(raw + (Decimal("1.25"), Decimal("0")))

    assert extended[: len(original)] == original


def test_bear_hysteresis_rejects_non_positive_confirmation_days() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        apply_bear_hysteresis((Decimal("1"),), enter_bear_days=0)
