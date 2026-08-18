from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/research/audit_high_annualized_arbitrage_candidates.py"
)
SPEC = importlib.util.spec_from_file_location("arbitrage_candidate_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def test_confirmation_annualization_uses_actual_222_day_interval() -> None:
    assert AUDIT.duration_days(*AUDIT.CONFIRMATION) == 222
    assert AUDIT.annualize(1.0, *AUDIT.CONFIRMATION) == pytest.approx(2.1257, rel=1e-3)


def test_audit_retains_ten_stress_cost_qualified_historical_candidates() -> None:
    payload = AUDIT.build_payload()
    candidates = payload["candidates"]

    assert len(candidates) == 10
    assert all(candidate["qualifies_as_historical_candidate"] for candidate in candidates)
    assert all(not candidate["fresh_forward_evidence"] for candidate in candidates)
    assert (
        sum(
            candidate["qualification"]["validation_annualized_over_100pct"]
            for candidate in candidates
        )
        == 0
    )


def test_audit_uses_corrected_market_state_artifact() -> None:
    payload = AUDIT.build_payload()
    state = next(
        candidate
        for candidate in payload["candidates"]
        if candidate["id"] == "eth_oi_state_anchor_overlay"
    )

    assert state["source"].endswith("market-state-overlay-20260815-142603-459615.json")
    assert state["confirmation"]["total_return"] == pytest.approx(0.8233921483)
    assert state["stress_confirmation"]["annualized_return"] > 1
