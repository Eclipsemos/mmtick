import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma11_cost_break_even import break_even, split_cost


def test_cost_split_preserves_the_requested_per_side_total() -> None:
    fee, slippage = split_cost(15)
    assert fee + slippage == 15


def test_break_even_reports_positive_and_non_positive_test_levels() -> None:
    positive = {"research": {"excess": 0.1}}
    non_positive = {"research": {"excess": 0.0}}
    for name in ("validation", "oos", "full"):
        positive[name] = {"excess": 0.1}
        non_positive[name] = {"excess": 0.0}
    results = {
        "15": {"periods": positive},
        "30": {"periods": non_positive},
    }

    assert break_even(results)["research"] == {
        "highest_tested_positive_cost_bps": 15,
        "first_tested_non_positive_cost_bps": 30,
    }
