import importlib.util
import sys
from decimal import Decimal
from pathlib import Path


def _guard_module():
    path = Path(__file__).parents[1] / "scripts" / "research" / "mine_volatility_guarded_trend.py"
    spec = importlib.util.spec_from_file_location("volatility_guarded_trend", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


GUARD = _guard_module()


def test_guard_config_serializes_both_regime_weights() -> None:
    config = GUARD.VolatilityGuardConfig(
        3,
        60,
        Decimal("0.25"),
        Decimal("0.6"),
        Decimal("0.05"),
        Decimal("8"),
        Decimal("0.2"),
        Decimal("0.16"),
        "local_post_confirmation",
    )

    values = config.as_dict()

    assert values["calm_trend_weight"] == 0.6
    assert values["calm_baseline_weight"] == 0.4
    assert values["volatile_trend_weight"] == 0.05
    assert values["volatile_baseline_weight"] == 0.95
    assert config.risk(Decimal("15")).turnover_bps == Decimal("15")


def test_guard_grid_local_has_five_strict_neighborhood_axes() -> None:
    assert len(GUARD.LOCAL_CALM_WEIGHTS) == 5
    assert len(GUARD.LOCAL_VOLATILE_WEIGHTS) == 5
    assert len(GUARD.LOCAL_LEVERAGES) == 7
    assert len(GUARD.LOCAL_LOSS_LIMITS) == 7
    assert len(GUARD.LOCAL_PROFIT_TARGETS) == 3
