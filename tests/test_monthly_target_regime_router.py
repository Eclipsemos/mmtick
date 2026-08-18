import importlib.util
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path


def _router_module():
    path = (
        Path(__file__).parents[1] / "scripts" / "research" / "mine_monthly_target_regime_router.py"
    )
    spec = importlib.util.spec_from_file_location("monthly_target_regime_router", path)
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


def _timestamp(label: str) -> int:
    return int(datetime.fromisoformat(label).replace(tzinfo=UTC).timestamp() * 1000)


def test_persistent_targets_flatten_while_new_direction_is_unconfirmed() -> None:
    targets = (None, 1, 1, -1, -1, 0, 1, 1)

    assert ROUTER._persistent_targets(targets, 2) == (None, 0, 1, 0, -1, 0, 0, 1)


def test_route_uses_weighted_trend_and_charges_switch_cost() -> None:
    state = (("2021-01-01", Decimal("0.01")), ("2021-01-02", Decimal("0.01")))
    trend = (("2021-01-01", Decimal("0.05")), ("2021-01-02", Decimal("0.05")))

    result = ROUTER._route_result(
        state,
        trend,
        {"2021-01-01": 0, "2021-01-02": 1},
        Decimal("0.25"),
        Decimal("1"),
        (_timestamp("2021-01-01"), _timestamp("2021-01-02")),
        Decimal("100"),
    )

    assert result.daily_returns == (
        ("2021-01-01", Decimal("0.01")),
        ("2021-01-02", Decimal("0.0175")),
    )


def test_complete_month_audit_excludes_partial_august() -> None:
    rows = (
        ("2026-06", Decimal("0.16")),
        ("2026-07", Decimal("0.17")),
        ("2026-08", Decimal("0.99")),
    )

    assert ROUTER._complete_months(rows) == rows[:2]
