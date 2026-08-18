import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


def _expanding_module():
    path = Path(__file__).parents[1] / "scripts" / "research" / "mine_expanding_calendar_router.py"
    spec = importlib.util.spec_from_file_location("expanding_calendar_router", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


EXPANDING = _expanding_module()


def _row(candidate_id, yearly_values):
    returns = tuple(
        (f"{year}-{month:02d}-02", value)
        for year, value in yearly_values.items()
        for month in range(1, 13)
    )
    return {
        "candidate": SimpleNamespace(id=candidate_id, direction="long_only"),
        "returns": {"base": returns, "stress": returns},
    }


def test_training_years_end_before_validation_year() -> None:
    assert EXPANDING._training_years(2023, 5) == (2021, 2022)
    assert EXPANDING._training_years(2025, 3) == (2022, 2023, 2024)
    assert EXPANDING._training_years(2026, 5) == (2021, 2022, 2023, 2024, 2025)


def test_final_mapping_uses_2025_but_never_2026() -> None:
    stable = _row(
        "stable",
        {
            2021: Decimal("0.1"),
            2022: Decimal("0.1"),
            2024: Decimal("0.1"),
            2025: Decimal("0.1"),
            2026: Decimal("-9"),
        },
    )
    recent = _row(
        "recent",
        {
            2021: Decimal("0.01"),
            2022: Decimal("0.01"),
            2024: Decimal("0.01"),
            2025: Decimal("0.5"),
            2026: Decimal("9"),
        },
    )
    rows = [stable, recent]
    monthly = EXPANDING._candidate_monthly(rows)

    mapping = EXPANDING._year_mapping(rows, monthly, 2026, "mean", 2, "long_only", 1)

    assert mapping[1] == ("recent",)


def test_confirmation_period_starts_after_walk_forward_validation() -> None:
    assert max(EXPANDING.VALIDATION_YEARS) == 2025
    assert EXPANDING.FINAL_TRAIN_END_YEAR == 2025
    assert EXPANDING.COMPLETE_CONFIRMATION_END.isoformat() == "2026-07-31"


def test_post_confirmation_family_extension_status_is_explicit() -> None:
    class Candidate:
        interval_minutes = 240

    payload = EXPANDING._report(
        {"btc_perp": ([SimpleNamespace(start_ms=0, end_ms=1)], [])},
        [Candidate()],
        [],
        [],
        {
            "development_selected_strict": True,
            "development_selected": None,
            "best_confirmation": None,
            "configuration_count": 0,
            "strict_pass_count": 0,
        },
        family_extension_after_confirmation_review=True,
    )

    assert payload["decision"]["status"].endswith("post_confirmation_family_extension")
    assert payload["decision"]["approved_for_trading"] is False
