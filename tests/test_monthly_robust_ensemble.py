import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest


def _ensemble_module():
    path = Path(__file__).parents[1] / "scripts" / "mine_monthly_robust_ensemble.py"
    spec = importlib.util.spec_from_file_location("monthly_robust_ensemble", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


ENSEMBLE = _ensemble_module()


def test_blend_returns_uses_aligned_labels_and_decimal_weights() -> None:
    left = (("2024-01-01", Decimal("0.1")), ("2024-01-02", Decimal("0.2")))
    right = (("2024-01-02", Decimal("0.4")), ("2024-01-03", Decimal("0.8")))

    result = ENSEMBLE._blend_returns(left, right, Decimal("0.25"))

    assert result == (("2024-01-02", Decimal("0.250")),)


def test_add_component_weights_preserves_total_weight() -> None:
    result = ENSEMBLE._add_component_weights(
        {"state": Decimal("0.5"), "first": Decimal("0.5")},
        "second",
        Decimal("0.2"),
    )

    assert result == {
        "state": Decimal("0.40"),
        "first": Decimal("0.40"),
        "second": Decimal("0.2"),
    }
    assert sum(result.values(), Decimal("0")) == Decimal("1")


def test_add_component_weights_rejects_duplicate_sleeve() -> None:
    with pytest.raises(ValueError, match="component"):
        ENSEMBLE._add_component_weights(
            {"state": Decimal("0.5"), "factor": Decimal("0.5")},
            "factor",
            Decimal("0.2"),
        )


def test_portfolio_id_is_independent_of_mapping_order() -> None:
    left = ENSEMBLE._portfolio_id({"state": Decimal("0.6"), "factor": Decimal("0.4")})
    right = ENSEMBLE._portfolio_id({"factor": Decimal("0.4"), "state": Decimal("0.6")})

    assert left == right
