import sys
from decimal import Decimal
from pathlib import Path

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.models import FundingRate

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_macro_vol_scaled_3x import (  # noqa: E402
    rolling_annualized_volatility,
    variable_funding_cap_targets,
    volatility_scaled_targets,
)


def _bar(index: int, close: str) -> ResearchBar:
    value = Decimal(close)
    start = index * 14_400_000
    return ResearchBar(start, start + 14_399_999, value, value, value, value)


def test_volatility_history_is_not_rewritten_by_future_bar() -> None:
    initial = [_bar(0, "100"), _bar(1, "110"), _bar(2, "100")]
    before = rolling_annualized_volatility(initial, 2)
    after = rolling_annualized_volatility(initial + [_bar(3, "200")], 2)

    assert after[: len(before)] == before


def test_volatility_scaling_clips_bull_exposure_between_one_and_three() -> None:
    result = volatility_scaled_targets(
        (Decimal("3"), Decimal("3"), Decimal("0.5"), Decimal("1")),
        (Decimal("0.2"), Decimal("2"), Decimal("2"), Decimal("2")),
        Decimal("1.2"),
        Decimal("3"),
    )

    assert result == (Decimal("3"), Decimal("1"), Decimal("0.5"), Decimal("1"))


def test_funding_cap_changes_only_after_known_event() -> None:
    event = FundingRate(1, Decimal("0.0002"), Decimal("100"))
    result = variable_funding_cap_targets(
        (Decimal("2"), None, None),
        ([], [event], []),
        Decimal("0.0001"),
    )

    assert result == (Decimal("2"), Decimal("1"), None)
