import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_stitched_sma_family_ensemble import average_targets


def test_average_targets_emits_only_actual_exposure_changes() -> None:
    result = average_targets(
        (
            (None, Decimal("1.5"), None, Decimal("0")),
            (None, Decimal("1.25"), None, Decimal("0")),
        )
    )

    assert result == (None, Decimal("1.375"), None, Decimal("0"))
