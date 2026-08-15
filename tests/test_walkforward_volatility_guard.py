import importlib.util
import sys
from decimal import Decimal
from pathlib import Path


def _walkforward_module():
    path = Path(__file__).parents[1] / "scripts" / "mine_walkforward_volatility_guard.py"
    spec = importlib.util.spec_from_file_location("walkforward_volatility_guard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


WALK = _walkforward_module()


def test_walkforward_config_keeps_train_validation_protocol_explicit() -> None:
    config = WALK.WalkForwardConfig(
        "candidate",
        3,
        60,
        Decimal("0.25"),
        Decimal("0.5"),
        Decimal("0"),
        Decimal("6"),
        Decimal("0.2"),
        Decimal("0.16"),
    )

    assert config.as_dict()["candidate_id"] == "candidate"
    assert config.as_dict()["calm_state_weight"] == 0.5
    assert config.as_dict()["volatile_state_weight"] == 1.0


def test_walkforward_candidate_periods_exclude_partial_august() -> None:
    assert WALK.TRAIN_PERIOD[1] < WALK.VALIDATION_PERIOD[0]
    assert WALK.VALIDATION_PERIOD[1] < WALK.CONFIRMATION_PERIOD[0]
    assert WALK.COMPLETE_CONFIRMATION_END.isoformat() == "2026-07-31"


def test_risk_search_passes_complete_cost_split_matrix_to_eligibility(monkeypatch) -> None:
    candidate = type("Candidate", (), {"id": "candidate"})()
    raw = [
        {
            "candidate": candidate,
            "returns": {"base": (), "stress": ()},
            "params": (
                3,
                60,
                Decimal("0.25"),
                Decimal("0.5"),
                Decimal("0"),
            ),
        }
    ]
    observed = []

    monkeypatch.setattr(WALK, "LEVERAGES", (Decimal("1"),))
    monkeypatch.setattr(WALK, "LOSS_LIMITS", (Decimal("0.2"),))
    monkeypatch.setattr(WALK, "PROFIT_TARGETS", (Decimal("0.16"),))
    monkeypatch.setattr(WALK, "evaluate_monthly_risk_overlay", lambda *_args: object())

    def eligible(results):
        observed.append(results)
        return False

    monkeypatch.setattr(WALK, "_development_eligible", eligible)

    assert WALK._risk_search(raw, {}, [], ()) == []
    assert len(observed) == 1
    assert set(observed[0]) == {"base", "stress"}
    assert set(observed[0]["base"]) == {"train", "validation"}
