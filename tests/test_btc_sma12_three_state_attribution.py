import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma12_three_state_attribution import targets_for_states  # noqa: E402


def test_attribution_maps_states_without_changing_unknown_warmup() -> None:
    states = (None, "bear", "neutral", "bull")
    exposures = {"bear": "0", "neutral": "1.25", "bull": "1.5"}

    assert targets_for_states(states, exposures) == (
        None,
        Decimal("0"),
        Decimal("1.25"),
        Decimal("1.5"),
    )
