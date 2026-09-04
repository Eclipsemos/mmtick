import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_composite_controls import (  # noqa: E402
    apply_drawdown_control,
    apply_funding_control,
)


def test_funding_control_preserves_unwarmed_none_and_uses_latest_known_event() -> None:
    class Event:
        def __init__(self, rate):
            self.rate = Decimal(rate)

    targets = (None, Decimal("1.5"), Decimal("1.5"), Decimal("0"))
    funding = ([], [Event("0.0002")], [], [])
    assert apply_funding_control(targets, funding, Decimal("0.0001")) == (
        None,
        Decimal("1"),
        Decimal("1"),
        Decimal("0"),
    )


def test_drawdown_guard_is_causal_and_only_reduces_active_target() -> None:
    class Bar:
        def __init__(self, close):
            self.close = Decimal(close)

    daily = [Bar("100"), Bar("90"), Bar("95")]
    baseline = (None, Decimal("1.5"), Decimal("0"))
    guarded = apply_drawdown_control(
        baseline,
        daily,
        {
            "lookback": 2,
            "drawdown_trigger": Decimal("0.05"),
            "guard_exposure": Decimal("1"),
        },
    )
    assert guarded == (None, Decimal("1"), Decimal("0"))
