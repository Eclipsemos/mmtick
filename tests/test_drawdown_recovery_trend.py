import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

from mastermind_tick.factor_portfolio import PortfolioResult, monthly_returns


def _recovery_module():
    path = Path(__file__).parents[1] / "scripts" / "mine_drawdown_recovery_trend.py"
    spec = importlib.util.spec_from_file_location("drawdown_recovery_trend", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


RECOVERY = _recovery_module()


def test_weighted_returns_requires_exact_weights_and_alignment() -> None:
    result = RECOVERY._weighted_returns(
        {
            "left": (("2024-01-01", Decimal("0.1")), ("2024-01-02", Decimal("0.2"))),
            "right": (("2024-01-02", Decimal("0.4")),),
        },
        {"left": Decimal("0.25"), "right": Decimal("0.75")},
    )

    assert result == (("2024-01-02", Decimal("0.350")),)


def test_loss_months_are_separated_by_cost_and_split() -> None:
    def result(rows):
        return PortfolioResult(
            Decimal("1"),
            Decimal("1"),
            Decimal("0"),
            Decimal("0"),
            False,
            rows,
            monthly_returns(rows),
        )

    base = result((("2024-01-01", Decimal("-0.1")), ("2024-02-01", Decimal("0.2"))))
    stress = result((("2024-01-01", Decimal("0.1")), ("2024-02-01", Decimal("-0.2"))))

    result = RECOVERY._loss_months(
        {
            "base": {"train": base, "validation": stress},
            "stress": {"train": stress, "validation": base},
        }
    )

    assert result == {
        "base": {"train": ("2024-01",), "validation": ("2024-02",)},
        "stress": {"train": ("2024-02",), "validation": ("2024-01",)},
    }


def test_recovery_activates_next_day_and_resets_at_month_boundary() -> None:
    baseline = (
        ("2024-01-01", Decimal("-0.02")),
        ("2024-01-02", Decimal("0.01")),
        ("2024-01-03", Decimal("0.01")),
        ("2024-02-01", Decimal("0.01")),
    )
    trend = tuple((label, Decimal("0.04")) for label, _value in baseline)

    result = RECOVERY._recovery_returns(
        baseline,
        trend,
        Decimal("-0.01"),
        Decimal("0.5"),
        Decimal("10"),
    )

    assert result == (
        ("2024-01-01", Decimal("-0.02")),
        ("2024-01-02", Decimal("0.0245")),
        ("2024-01-03", Decimal("0.025")),
        ("2024-02-01", Decimal("0.0095")),
    )
