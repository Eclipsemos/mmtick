import importlib.util
import sys
from decimal import Decimal
from pathlib import Path


def _static_module():
    path = Path(__file__).parents[1] / "scripts" / "research" / "mine_static_defensive_trend.py"
    spec = importlib.util.spec_from_file_location("static_defensive_trend", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


STATIC = _static_module()


def test_static_config_records_complement_weights_and_risk() -> None:
    config = STATIC.StaticTrendConfig(
        "trend",
        Decimal("0.4"),
        Decimal("8"),
        Decimal("0.15"),
        Decimal("0.18"),
    )

    assert config.as_dict() == {
        "id": "static-defense-trend-weight0.4-lev8-loss0.15-profit0.18",
        "sleeve_id": "trend",
        "trend_weight": 0.4,
        "baseline_weight": 0.6,
        "leverage": 8.0,
        "monthly_loss_limit": 0.15,
        "monthly_profit_target": 0.18,
    }
    assert config.risk(Decimal("15")).turnover_bps == Decimal("15")


def test_static_search_grid_has_expected_size_per_sleeve() -> None:
    assert (
        len(STATIC.TREND_WEIGHTS)
        * len(STATIC.LEVERAGES)
        * len(STATIC.LOSS_LIMITS)
        * len(STATIC.PROFIT_TARGETS)
        == 448
    )
