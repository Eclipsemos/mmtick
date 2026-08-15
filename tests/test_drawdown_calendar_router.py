import importlib.util
import sys
from decimal import Decimal
from pathlib import Path


def _drawdown_module():
    path = Path(__file__).parents[1] / "scripts" / "mine_drawdown_calendar_router.py"
    spec = importlib.util.spec_from_file_location("drawdown_calendar_router", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


DRAWDOWN = _drawdown_module()


def test_drawdown_recovery_activates_on_day_after_threshold_cross() -> None:
    state = tuple((f"2025-01-0{day}", Decimal("0")) for day in (2, 3, 4))
    candidates = {
        "long": {
            "2025-01-02": Decimal("-0.06"),
            "2025-01-03": Decimal("0.02"),
            "2025-01-04": Decimal("0.02"),
        },
        "recovery": {
            "2025-01-02": Decimal("0.01"),
            "2025-01-03": Decimal("0.05"),
            "2025-01-04": Decimal("0.05"),
        },
    }
    long_mapping = {month: ("long",) for month in range(1, 13)}
    recovery_mapping = {month: ("recovery",) for month in range(1, 13)}

    result = DRAWDOWN._drawdown_recovery_returns(
        state,
        candidates,
        long_mapping,
        recovery_mapping,
        Decimal("0.5"),
        Decimal("0.02"),
        Decimal("0"),
        2025,
    )

    assert result == (
        ("2025-01-02", Decimal("-0.030")),
        ("2025-01-03", Decimal("0.025")),
        ("2025-01-04", Decimal("0.025")),
    )


def test_drawdown_recovery_resets_at_new_month() -> None:
    state = (("2025-01-31", Decimal("0")), ("2025-02-01", Decimal("0")))
    candidates = {
        "long": {"2025-01-31": Decimal("-0.1"), "2025-02-01": Decimal("0.02")},
        "recovery": {"2025-01-31": Decimal("0.1"), "2025-02-01": Decimal("0.5")},
    }
    long_mapping = {month: ("long",) for month in range(1, 13)}
    recovery_mapping = {month: ("recovery",) for month in range(1, 13)}

    result = DRAWDOWN._drawdown_recovery_returns(
        state,
        candidates,
        long_mapping,
        recovery_mapping,
        Decimal("0"),
        Decimal("0.05"),
        Decimal("0"),
        2025,
    )

    assert result[1] == ("2025-02-01", Decimal("0.02"))


def test_drawdown_confirmation_excludes_partial_august() -> None:
    assert max(DRAWDOWN.VALIDATION_YEARS) == 2025
    assert DRAWDOWN.COMPLETE_CONFIRMATION_END.isoformat() == "2026-07-31"
