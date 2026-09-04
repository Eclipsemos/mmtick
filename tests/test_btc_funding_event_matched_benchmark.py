import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_funding_event_matched_benchmark import (  # noqa: E402
    ACTIVE,
    HOLD_BARS,
    LOOKBACK_EVENTS,
    MODES,
    THRESHOLDS,
    candidate_library,
    map_exposure_targets,
    target_indices,
)


def test_funding_event_screen_is_a_fixed_predeclared_grid() -> None:
    assert len(candidate_library()) == (
        len(LOOKBACK_EVENTS) * len(THRESHOLDS) * len(HOLD_BARS) * len(MODES)
    )


def test_funding_event_signals_map_to_long_or_flat() -> None:
    assert map_exposure_targets((Decimal("-1"), Decimal("0"), Decimal("1"))) == (
        Decimal("0"),
        Decimal("0"),
        ACTIVE,
    )


def test_funding_targets_execute_after_the_completed_four_hour_bar() -> None:
    assert target_indices(10, (15, 31)) == (25, 41)
