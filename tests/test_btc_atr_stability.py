import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def _audit_module():
    path = Path(__file__).parents[1] / "scripts" / "research" / "audit_btc_atr_stability.py"
    spec = importlib.util.spec_from_file_location("btc_atr_stability", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _result(net_return=0.10, max_drawdown=-0.10, trades=8, positive_months=5):
    monthly = tuple(
        (f"2026-{month:02d}", 0.01 if month <= positive_months else -0.01) for month in range(1, 9)
    )
    return SimpleNamespace(
        net_return=net_return,
        max_drawdown=max_drawdown,
        completed_trades=trades,
        monthly_returns=monthly,
    )


def test_stability_gates_accept_consistent_results() -> None:
    audit = _audit_module()
    results = {name: _result() for name in ("train", "validation", "confirmation")}
    stress = {name: _result(net_return=0.05) for name in results}

    gates = audit.stability_gates(results, stress, neighbor_pass_rate=0.60)

    assert all(gates.values())


def test_stability_gates_reject_sparse_confirmation() -> None:
    audit = _audit_module()
    results = {name: _result() for name in ("train", "validation", "confirmation")}
    results["confirmation"] = _result(trades=5)
    stress = {name: _result(net_return=0.05) for name in results}

    gates = audit.stability_gates(results, stress, neighbor_pass_rate=0.60)

    assert not gates["confirmation_trades"]
