import importlib.util
import sys
from decimal import Decimal
from pathlib import Path


def _trend_module():
    path = Path(__file__).parents[1] / "scripts" / "mine_fast_trend_complement.py"
    spec = importlib.util.spec_from_file_location("fast_trend_complement", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


TREND = _trend_module()


def test_fast_trend_grid_has_fixed_candidate_count() -> None:
    per_asset = sum(
        len(values["lookbacks"]) * len(values["thresholds"]) * len(TREND.CONFIRMATION_BARS)
        for values in TREND.INTERVAL_CONFIGS.values()
    )

    assert per_asset * 2 == 224


def test_fast_trend_candidate_id_records_causal_parameters() -> None:
    candidate = TREND.FastTrendCandidate(
        "eth_perp",
        1440,
        3,
        Decimal("0.04"),
        2,
        [],
        [],
        (),
    )

    assert candidate.id == (
        "eth_perp-fast-momentum-1440m-lookback3-threshold0p04-confirm2-long_short"
    )
    assert candidate.as_dict()["lookback_days"] == 3


def test_unlocked_result_compounds_daily_returns() -> None:
    rows = (("2024-01-01", Decimal("0.1")), ("2024-01-02", Decimal("-0.1")))

    result = TREND._unlocked_result(rows)

    assert result.net_return == Decimal("-0.01")
    assert result.max_drawdown == Decimal("-0.1")
    assert result.monthly_returns == (("2024-01", Decimal("-0.01")),)
