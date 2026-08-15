import importlib.util
import sys
from decimal import Decimal
from pathlib import Path


def _defensive_module():
    path = Path(__file__).parents[1] / "scripts" / "mine_defensive_factor_portfolio.py"
    spec = importlib.util.spec_from_file_location("defensive_factor_portfolio", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


DEFENSIVE = _defensive_module()


def test_conditional_summary_rejects_incomplete_factor_months() -> None:
    state = {"2024-01": Decimal("-0.1"), "2024-02": Decimal("0.1")}

    result = DEFENSIVE._conditional_summary(state, {"2024-02": Decimal("0.2")})

    assert result["month_count"] == 0
    assert result["positive_rate"] == 0


def test_conditional_summary_uses_only_baseline_loss_months() -> None:
    state = {
        "2024-01": Decimal("-0.1"),
        "2024-02": Decimal("0.1"),
        "2024-03": Decimal("-0.2"),
    }
    factor = {
        "2024-01": Decimal("0.2"),
        "2024-02": Decimal("-0.9"),
        "2024-03": Decimal("-0.1"),
    }

    result = DEFENSIVE._conditional_summary(state, factor)

    assert result == {
        "month_count": 2,
        "positive_rate": Decimal("0.5"),
        "average_return": Decimal("0.05"),
        "worst_return": Decimal("-0.1"),
    }


def test_defensive_config_builds_monthly_risk_control() -> None:
    config = DEFENSIVE.DefensiveConfig(
        "factor",
        Decimal("0.75"),
        Decimal("2"),
        Decimal("0.15"),
        Decimal("0.16"),
    )

    risk = config.risk(Decimal("7"))

    assert risk.leverage == Decimal("2")
    assert risk.loss_limit == Decimal("0.15")
    assert risk.profit_target == Decimal("0.16")
    assert risk.turnover_bps == Decimal("7")
