import sys
from decimal import Decimal
from pathlib import Path

from mastermind_tick.bar_research import ResearchBar

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_momentum_gated_3x import (  # noqa: E402
    momentum_state_targets,
    trailing_momentum,
)


def _bar(index: int, close: str) -> ResearchBar:
    value = Decimal(close)
    start = index * 14_400_000
    return ResearchBar(start, start + 14_399_999, value, value, value, value)


def test_trailing_momentum_uses_only_completed_history() -> None:
    bars = [_bar(0, "100"), _bar(1, "110"), _bar(2, "120")]

    result = trailing_momentum(bars, 1)

    assert result == (None, Decimal("0.1"), Decimal("120") / Decimal("110") - 1)


def test_momentum_state_requires_both_macro_and_momentum() -> None:
    bars = [_bar(0, "110"), _bar(1, "90"), _bar(2, "100")]
    result = momentum_state_targets(
        bars,
        (Decimal("100"), Decimal("100"), None),
        (Decimal("0.1"), Decimal("-0.1"), Decimal("0.1")),
        Decimal("0"),
        Decimal("3"),
    )

    assert result == (Decimal("3"), Decimal("0"), Decimal("1"))
