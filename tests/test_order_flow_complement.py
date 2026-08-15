import importlib.util
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path


def _order_flow_complement_module():
    path = Path(__file__).parents[1] / "scripts" / "mine_order_flow_complement.py"
    spec = importlib.util.spec_from_file_location("order_flow_complement", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


ORDER_FLOW = _order_flow_complement_module()


def _period(start: str, end: str) -> tuple[int, int]:
    return (
        int(datetime.fromisoformat(start).replace(tzinfo=UTC).timestamp() * 1000),
        int(datetime.fromisoformat(end).replace(tzinfo=UTC).timestamp() * 1000),
    )


def test_pair_shortlist_builds_weighted_development_returns() -> None:
    left = (
        ("2024-01-01", Decimal("0.10")),
        ("2025-01-01", Decimal("0.10")),
        ("2026-01-01", Decimal("0.10")),
    )
    right = (
        ("2024-01-01", Decimal("0.20")),
        ("2025-01-01", Decimal("0.20")),
        ("2026-01-01", Decimal("0.20")),
    )
    replays = {
        "left": {"base": left, "stress": left},
        "right": {"base": right, "stress": right},
    }
    periods = {
        "train": _period("2024-01-01", "2024-12-31"),
        "validation": _period("2025-01-01", "2025-12-31"),
        "confirmation": _period("2026-01-01", "2026-08-10"),
    }

    rows = ORDER_FLOW._pair_shortlist(replays, periods)

    assert len(rows) == len(ORDER_FLOW.PAIR_WEIGHTS)
    weighted = next(row for row in rows if row["left_weight"] == Decimal("0.25"))
    assert weighted["right_weight"] == Decimal("0.75")
    assert weighted["returns"]["base"] == (
        ("2024-01-01", Decimal("0.1750")),
        ("2025-01-01", Decimal("0.1750")),
        ("2026-01-01", Decimal("0.1750")),
    )


def test_strict_count_excludes_partial_august() -> None:
    result = ORDER_FLOW._unlocked_result(
        tuple((f"2026-{month:02d}-01", Decimal("0.15")) for month in range(1, 9))
    )

    assert ORDER_FLOW._strict_complete_month_count(result) == 7


def test_aggregate_confirmation_audit_combines_families() -> None:
    single = {
        "configuration_count": 11,
        "strict_pass_count": 2,
        "best_confirmation": {"counts": {"base": 6, "stress": 5}},
    }
    pair = {
        "configuration_count": 13,
        "strict_pass_count": 3,
        "best_confirmation": {"counts": {"base": 4, "stress": 4}},
    }

    assert ORDER_FLOW._aggregate_confirmation_audit(single, pair) == {
        "configuration_count": 24,
        "strict_pass_count": 5,
        "best_complete_month_count": 5,
    }
