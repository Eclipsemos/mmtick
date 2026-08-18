import importlib.util
import sys
from decimal import Decimal
from pathlib import Path


def _router_module():
    path = (
        Path(__file__).parents[1] / "scripts" / "research" / "mine_volatility_order_flow_router.py"
    )
    spec = importlib.util.spec_from_file_location("volatility_order_flow_router", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


ROUTER = _router_module()


def test_quantile_interpolates_without_float_math() -> None:
    assert ROUTER._quantile(
        [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")], Decimal("0.25")
    ) == Decimal("1.75")


def test_volatility_regime_applies_signal_to_next_day() -> None:
    values = tuple(
        (f"2024-01-{day:02d}", Decimal(value))
        for day, value in enumerate(("1", "1", "1", "9", "1", "1"), start=1)
    )

    regimes = ROUTER._prior_day_volatility_regimes(values, 1, 3, Decimal("0.5"))

    assert "2024-01-04" not in regimes
    assert regimes["2024-01-05"] is False
    assert regimes["2024-01-06"] is True


def test_route_returns_blends_weights_and_charges_switch_turnover() -> None:
    state = (
        ("2024-01-01", Decimal("0.01")),
        ("2024-01-02", Decimal("0.01")),
        ("2024-01-03", Decimal("0.01")),
    )
    flow = (
        ("2024-01-01", Decimal("0.03")),
        ("2024-01-02", Decimal("0.03")),
        ("2024-01-03", Decimal("0.03")),
    )
    regimes = {"2024-01-01": True, "2024-01-02": True, "2024-01-03": False}

    result = ROUTER._route_returns(
        state,
        flow,
        regimes,
        Decimal("0.5"),
        Decimal("0"),
        Decimal("10"),
    )

    assert result == (
        ("2024-01-01", Decimal("0.0195")),
        ("2024-01-02", Decimal("0.020")),
        ("2024-01-03", Decimal("0.0095")),
    )


def test_route_id_records_causal_controls() -> None:
    route = ROUTER.VolatilityRoute(
        "flow",
        5,
        120,
        Decimal("0.5"),
        Decimal("0.75"),
        Decimal("0.25"),
    )

    assert route.id == "vol-flow-flow-lookback5-calibration120-q0p5-calm0p75-volatile0p25"
