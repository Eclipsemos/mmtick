import sys
from decimal import Decimal
from pathlib import Path

from mastermind_tick.bar_research import ResearchBar

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_macro_gated_3x import (  # noqa: E402
    development_score,
    macro_gated_targets,
    select_development_candidate,
    select_max_leverage_challenger,
)


def _bar(index: int, close: str) -> ResearchBar:
    value = Decimal(close)
    start = index * 900_000
    return ResearchBar(start, start + 899_999, value, value, value, value)


def test_macro_gate_blocks_only_bull_leverage_below_macro() -> None:
    bars = [_bar(0, "90"), _bar(1, "110"), _bar(2, "80")]

    result = macro_gated_targets(
        bars,
        (Decimal("3"), Decimal("3"), Decimal("0.5")),
        (Decimal("100"), Decimal("100"), Decimal("100")),
        Decimal("3"),
    )

    assert result == (Decimal("1"), Decimal("3"), Decimal("0.5"))


def test_macro_gate_blocks_bull_until_slow_average_exists() -> None:
    result = macro_gated_targets(
        [_bar(0, "100")],
        (Decimal("3"),),
        (None,),
        Decimal("3"),
    )

    assert result == (Decimal("1"),)


def test_development_selection_uses_only_research_and_validation() -> None:
    year_ms = 365 * 24 * 60 * 60 * 1000
    splits = {
        "research": (0, 3 * year_ms),
        "validation": (3 * year_ms + 1, 5 * year_ms),
    }
    benchmarks = {
        "research": {"net_return": 1.0, "max_drawdown": -0.8},
        "validation": {"net_return": 1.0, "max_drawdown": -0.4},
    }

    def candidate(name: str, research: float, validation: float, oos: float):
        metric = lambda value, drawdown: {  # noqa: E731
            "stress": {
                "net_return": value,
                "max_drawdown": drawdown,
                "liquidated": False,
                "maximum_futures_leverage": 3.0,
            }
        }
        return {
            "id": name,
            "bull_exposure": "3",
            "macro_period": 1200,
            "metrics": {
                "research": metric(research, -0.7),
                "validation": metric(validation, -0.3),
                "oos": metric(oos, -0.2),
            },
        }

    better_development = candidate("development", 2.0, 2.0, -0.9)
    better_oos = candidate("oos", 1.1, 1.1, 10.0)

    selected, qualifying = select_development_candidate(
        [better_development, better_oos], benchmarks, splits
    )

    assert selected["id"] == "development"
    assert [row["id"] for row in qualifying] == ["development", "oos"]
    assert development_score(better_development, benchmarks, splits) > development_score(
        better_oos, benchmarks, splits
    )


def test_max_leverage_challenger_is_selected_from_development_ranking() -> None:
    ranking = [
        {"id": "lower", "bull_exposure": "2.75"},
        {"id": "first_3x", "bull_exposure": "3"},
        {"id": "later_3x", "bull_exposure": "3"},
    ]

    assert select_max_leverage_challenger(ranking)["id"] == "first_3x"
