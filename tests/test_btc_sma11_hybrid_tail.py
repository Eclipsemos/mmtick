import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma11_hybrid_tail import aggregate, remove_top_excess


def _row(date: str, strategy: float, benchmark: float) -> dict:
    return {
        "date": date,
        "strategy_log_return": strategy,
        "benchmark_log_return": benchmark,
        "excess_log_return": strategy - benchmark,
    }


def test_tail_removal_excludes_the_largest_paired_excess_day() -> None:
    rows = [
        _row("2024-01-01", 0.1, 0.0),
        _row("2024-01-02", 0.01, 0.0),
        _row("2024-01-03", -0.01, 0.0),
    ]

    result = remove_top_excess(rows, 1)

    assert result["removed_days"] == ["2024-01-01"]
    assert result["strategy_return"] == aggregate(rows[1:])["strategy_return"]
