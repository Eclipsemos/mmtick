import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def _research_module():
    path = Path(__file__).parents[1] / "scripts" / "research" / "research_individual_crypto_atr.py"
    spec = importlib.util.spec_from_file_location("individual_crypto_atr", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _tick_verify_module():
    path = (
        Path(__file__).parents[1]
        / "scripts"
        / "research"
        / "verify_individual_crypto_tick_atr.py"
    )
    spec = importlib.util.spec_from_file_location("individual_crypto_tick_atr", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(*, net_return=0.10, max_drawdown=-0.10, trades=20, positive_months=8):
    return SimpleNamespace(
        net_return=net_return,
        max_drawdown=max_drawdown,
        completed_trades=trades,
        monthly_returns=tuple(
            (f"2025-{month:02d}", 0.01 if month <= positive_months else -0.01)
            for month in range(1, 13)
        ),
    )


def test_stability_gates_accept_independent_consistent_candidate() -> None:
    research = _research_module()
    base = {name: _result() for name in ("train", "validation", "confirmation")}
    stress = {name: _result(net_return=0.04) for name in base}

    gates = research.stability_gates(base, stress, neighbors=0.60)

    assert all(gates.values())


def test_stability_gates_reject_confirmation_drawdown() -> None:
    research = _research_module()
    base = {name: _result() for name in ("train", "validation", "confirmation")}
    base["confirmation"] = _result(max_drawdown=-0.30)
    stress = {name: _result(net_return=0.04) for name in base}

    gates = research.stability_gates(base, stress, neighbors=0.60)

    assert not gates["drawdown_controlled"]


def test_tick_winner_uses_development_and_validation_only() -> None:
    verify = _tick_verify_module()

    def result(period, multiplier, net_return, drawdown):
        return SimpleNamespace(
            atr_period=period,
            atr_multiplier=multiplier,
            net_return=net_return,
            max_drawdown=drawdown,
        )

    development = {
        (14, 2.0): result(14, 2.0, 0.30, -0.10),
        (21, 3.0): result(21, 3.0, 0.15, -0.08),
    }
    validation = {
        (14, 2.0): result(14, 2.0, -0.05, -0.12),
        (21, 3.0): result(21, 3.0, 0.10, -0.09),
    }

    assert verify.select_development_winner(development, validation) == (21, 3.0)
