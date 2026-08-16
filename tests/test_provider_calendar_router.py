import importlib.util
import sys
from decimal import Decimal
from pathlib import Path


def _provider_module():
    path = Path(__file__).parents[1] / "scripts" / "mine_provider_calendar_router.py"
    spec = importlib.util.spec_from_file_location("provider_calendar_router", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


PROVIDER = _provider_module()


def test_provider_mapping_uses_only_training_years() -> None:
    curves = {
        cost: {
            2024: {
                "calendar": tuple(
                    (f"2024-{month:02d}-02", Decimal("0.1")) for month in range(1, 13)
                ),
                "volatility_guard": tuple(
                    (f"2024-{month:02d}-02", Decimal("0.01")) for month in range(1, 13)
                ),
            },
            2025: {
                "calendar": tuple(
                    (f"2025-{month:02d}-02", Decimal("0.1")) for month in range(1, 13)
                ),
                "volatility_guard": tuple(
                    (f"2025-{month:02d}-02", Decimal("0.01")) for month in range(1, 13)
                ),
            },
            2026: {
                "calendar": tuple(
                    (f"2026-{month:02d}-02", Decimal("-9")) for month in range(1, 13)
                ),
                "volatility_guard": tuple(
                    (f"2026-{month:02d}-02", Decimal("9")) for month in range(1, 13)
                ),
            },
        }
        for cost in ("base", "stress")
    }

    mapping = PROVIDER._provider_mapping(curves, 2026, 2, "mean")

    assert mapping[1] == "calendar"


def test_provider_switch_charges_turnover() -> None:
    result = PROVIDER._selected_provider_returns(
        {
            "calendar": (("2025-01-02", Decimal("0.02")), ("2025-02-02", Decimal("0.02"))),
            "volatility_guard": (("2025-01-02", Decimal("0.03")), ("2025-02-02", Decimal("0.03"))),
        },
        {1: "calendar", 2: "volatility_guard", **{month: "calendar" for month in range(3, 13)}},
        Decimal("10"),
    )

    assert result == (("2025-01-02", Decimal("0.019")), ("2025-02-02", Decimal("0.029")))


def test_provider_confirmation_excludes_partial_august() -> None:
    assert max(PROVIDER.VALIDATION_YEARS) == 2025
    assert PROVIDER.COMPLETE_CONFIRMATION_END.isoformat() == "2026-07-31"
