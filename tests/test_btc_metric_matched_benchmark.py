import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_metric_matched_benchmark import (  # noqa: E402
    ACTIVE,
    METRIC_FEATURES,
    POLARITIES,
    THRESHOLDS,
    WINDOWS,
    candidate_library,
    exposures,
    metric_target_indices,
)


def test_metric_screen_is_a_fixed_predeclared_grid() -> None:
    candidates = candidate_library()

    assert len(candidates) == (
        len(METRIC_FEATURES) * len(WINDOWS) * len(THRESHOLDS) * len(POLARITIES)
    )
    assert {candidate.feature for candidate in candidates} == set(METRIC_FEATURES)


def test_missing_and_short_signals_map_to_flat_exposure() -> None:
    assert exposures((None, 1, 0, -1)) == (Decimal("0"), ACTIVE, Decimal("0"), Decimal("0"))


def test_metric_targets_execute_after_the_completed_four_hour_bar() -> None:
    assert metric_target_indices(10, (15, 31)) == (25, 41)
