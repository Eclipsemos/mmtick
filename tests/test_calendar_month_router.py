import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


def _calendar_module():
    path = Path(__file__).parents[1] / "scripts" / "mine_calendar_month_router.py"
    spec = importlib.util.spec_from_file_location("calendar_month_router", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


CALENDAR = _calendar_module()


def _row(candidate_id, direction, discovery_return, confirmation_return):
    returns = tuple(
        (f"{year}-{month:02d}-02", discovery_return)
        for year in CALENDAR.DISCOVERY_YEARS
        for month in range(1, 13)
    ) + tuple(
        (f"2026-{month:02d}-02", confirmation_return) for month in range(1, 9)
    )
    return {
        "candidate": SimpleNamespace(id=candidate_id, direction=direction),
        "returns": {"base": returns, "stress": returns},
    }


def test_calendar_mapping_does_not_use_confirmation_returns() -> None:
    rows = [
        _row("stable", "long_only", Decimal("0.1"), Decimal("-0.9")),
        _row("future_winner", "long_only", Decimal("0.01"), Decimal("9")),
    ]

    mapping = CALENDAR._calendar_mapping(rows, "mean", 3, "long_only", 1)

    assert mapping[1] == ("stable",)


def test_seasonal_returns_switch_candidates_at_calendar_month_boundary() -> None:
    state = (
        ("2025-01-02", Decimal("0.01")),
        ("2025-02-02", Decimal("0.01")),
    )
    candidates = {
        "jan": {
            "2025-01-02": Decimal("0.02"),
            "2025-02-02": Decimal("0.50"),
        },
        "feb": {
            "2025-01-02": Decimal("0.50"),
            "2025-02-02": Decimal("0.03"),
        },
    }
    mapping = {month: (("jan",) if month == 1 else ("feb",)) for month in range(1, 13)}

    result = CALENDAR._seasonal_returns(
        state,
        candidates,
        mapping,
        Decimal("0.5"),
        Decimal("10"),
    )

    assert result == (
        ("2025-01-02", Decimal("0.014")),
        ("2025-02-02", Decimal("0.0195")),
    )


def test_calendar_confirmation_excludes_partial_august() -> None:
    assert CALENDAR.VALIDATION_2024[1] < CALENDAR.VALIDATION_2025[0]
    assert CALENDAR.VALIDATION_2025[1] < CALENDAR.CONFIRMATION[0]
    assert CALENDAR.COMPLETE_CONFIRMATION_END.isoformat() == "2026-07-31"
