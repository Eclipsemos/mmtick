import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


def _conditional_module():
    path = (
        Path(__file__).parents[1]
        / "scripts"
        / "research"
        / "mine_conditional_calendar_complement.py"
    )
    spec = importlib.util.spec_from_file_location("conditional_calendar_complement", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


CONDITIONAL = _conditional_module()


def test_weak_labels_are_limited_to_requested_training_years() -> None:
    history = {
        "base": {
            2023: (("2023-01-02", Decimal("-0.1")),),
            2024: (("2024-01-02", Decimal("-0.2")),),
            2025: (("2025-01-02", Decimal("-0.3")),),
        },
        "stress": {
            2023: (("2023-01-02", Decimal("-0.1")),),
            2024: (("2024-01-02", Decimal("-0.2")),),
            2025: (("2025-01-02", Decimal("-0.3")),),
        },
    }

    labels = CONDITIONAL._weak_labels(history, (2023, 2024), "negative")

    assert labels == ("2023-01", "2024-01")


def test_complement_selection_does_not_use_unlisted_confirmation_month() -> None:
    rows = [
        {"candidate": SimpleNamespace(id="stable", direction="long_short")},
        {"candidate": SimpleNamespace(id="future", direction="long_short")},
    ]
    monthly = {
        "stable": {
            "base": {"2025-01": Decimal("0.1"), "2026-01": Decimal("-9")},
            "stress": {"2025-01": Decimal("0.1"), "2026-01": Decimal("-9")},
        },
        "future": {
            "base": {"2025-01": Decimal("0.01"), "2026-01": Decimal("9")},
            "stress": {"2025-01": Decimal("0.01"), "2026-01": Decimal("9")},
        },
    }

    selected = CONDITIONAL._select_complements(rows, monthly, ("2025-01",), "mean", "long_short", 1)

    assert selected == ("stable",)


def test_composite_returns_apply_fixed_weight_and_opening_cost() -> None:
    result = CONDITIONAL._composite_returns(
        (("2025-01-02", Decimal("0.02")), ("2025-01-03", Decimal("0.02"))),
        {"hedge": {"2025-01-02": Decimal("0.04"), "2025-01-03": Decimal("0.04")}},
        ("hedge",),
        Decimal("0.25"),
        Decimal("10"),
    )

    assert result == (
        ("2025-01-02", Decimal("0.02475")),
        ("2025-01-03", Decimal("0.025")),
    )


def test_short_only_targets_retain_only_confirmed_shorts() -> None:
    assert CONDITIONAL._short_only_targets((None, 1, 0, -1, -1)) == (None, 0, 0, -1, -1)


def test_conditional_confirmation_excludes_partial_august() -> None:
    assert max(CONDITIONAL.VALIDATION_YEARS) == 2025
    assert CONDITIONAL.COMPLETE_CONFIRMATION_END.isoformat() == "2026-07-31"
