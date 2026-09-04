import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_mechanism_attribution import (  # noqa: E402
    concentration_analysis,
    leave_one_year_out,
)


def _record(date, year, month, excess):
    return {
        "date": date,
        "year": year,
        "month": month,
        "strategy_log_return": excess,
        "benchmark_log_return": 0.0,
        "excess_log_return": excess,
    }


def test_concentration_analysis_removes_largest_excess_days() -> None:
    records = [
        _record(f"2024-01-{day:02d}", 2024, "2024-01", value)
        for day, value in enumerate((0.10, 0.05, -0.02, 0.01, 0.01, 0.01), start=1)
    ]

    result = concentration_analysis(records)

    assert result["top_10_excess_days"][0]["excess_log_return"] == 0.10
    assert result["top_day_removal"]["5"]["share_of_positive_excess"] == 1.0
    assert result["top_day_removal"]["5"]["remaining_annualized_excess"] < 0


def test_leave_one_year_out_uses_only_remaining_years() -> None:
    records = [
        _record("2023-01-01", 2023, "2023-01", math.log(1.2)),
        _record("2024-01-01", 2024, "2024-01", math.log(0.9)),
        _record("2025-01-01", 2025, "2025-01", math.log(1.1)),
    ]

    result = {row["omitted_year"]: row for row in leave_one_year_out(records)}

    assert result[2024]["still_beats_buy_and_hold"] is True
    assert result[2023]["still_beats_buy_and_hold"] is False
