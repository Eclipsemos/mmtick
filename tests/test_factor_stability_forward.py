import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.factor_stability_forward import (
    evaluate_frozen_factor_forward,
    load_frozen_factor_monitor,
    render_factor_forward_markdown,
)


def _candidate(tmp_path):
    parameters = {
        "bar_interval_minutes": 240,
        "factors": [
            {
                "factor": "own_ret_6",
                "horizon_bars": 1,
                "orientation": -1,
                "source_asset": "btc_perp",
                "target_asset": "btc_perp",
            },
            {
                "factor": "other_ret_6",
                "horizon_bars": 1,
                "orientation": -1,
                "source_asset": "btc_perp",
                "target_asset": "eth_perp",
            },
        ],
    }
    encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    payload = {
        "schema_version": 1,
        "id": "btc-eth-4h-reversal-factor-forward-v1",
        "strategy_family": "single_factor_ic_monitor",
        "status": "forward_observation_only",
        "approved_for_trading": False,
        "evidence_lock_date": "2026-08-11",
        "forward_evidence_start_date": "2026-08-12",
        "source_report": "reports/source.json",
        "parameter_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "parameters": parameters,
        "costs": {
            "fee_bps_per_fill": 5.0,
            "slippage_bps_per_fill": 2.0,
            "funding_included": True,
        },
        "development_reference": {
            "btc_perp": {"cost_adjusted_ic": 0.05},
            "eth_perp": {"cost_adjusted_ic": 0.04},
        },
        "forward_gates": {
            "minimum_complete_days_for_review": 30,
            "minimum_non_overlapping_samples_for_review": 150,
            "minimum_cost_adjusted_ic": 0.02,
            "minimum_development_ic_retention": 0.5,
        },
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _market(start: date, days: int):
    bars = []
    for index in range(days * 6):
        start_ms = _day_start(start) + index * 4 * 3_600_000
        phase = index % 5
        close = Decimal("100") + Decimal(index) + Decimal(phase) / Decimal("10")
        bars.append(
            ResearchBar(
                start_ms=start_ms,
                end_ms=start_ms + 4 * 3_600_000 - 1,
                open=close - Decimal("0.2"),
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=Decimal("1000"),
            )
        )
    return {"btc_perp": bars, "eth_perp": list(bars)}


def test_frozen_monitor_rejects_parameter_tampering(tmp_path) -> None:
    path = _candidate(tmp_path)
    payload = json.loads(path.read_text())
    payload["parameters"]["factors"][0]["orientation"] = 1
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="hash mismatch"):
        load_frozen_factor_monitor(path)


def test_forward_monitor_uses_only_complete_post_lock_days(tmp_path) -> None:
    pytest.importorskip("numpy")
    monitor = load_frozen_factor_monitor(_candidate(tmp_path))
    bars = _market(date(2026, 8, 10), 6)
    funding = {asset: [[] for _ in values] for asset, values in bars.items()}

    payload = evaluate_frozen_factor_forward(monitor, bars, funding)

    assert payload["status"] == "collecting_forward_evidence"
    assert payload["complete_forward_days"] == 3
    assert payload["parameter_search_performed"] is False
    for result in payload["factors"].values():
        assert result["first_day"] == "2026-08-12"
        assert result["last_day"] == "2026-08-14"
        assert result["samples"] == 18
        assert result["gates"]["sample_ready"] is False
    assert "Transformer combination remains disabled" in render_factor_forward_markdown(payload)


def test_forward_monitor_is_deterministic_except_generation_time(tmp_path) -> None:
    pytest.importorskip("numpy")
    monitor = load_frozen_factor_monitor(_candidate(tmp_path))
    bars = _market(date(2026, 8, 10), 6)
    funding = {asset: [[] for _ in values] for asset, values in bars.items()}

    first = evaluate_frozen_factor_forward(monitor, bars, funding)
    second = evaluate_frozen_factor_forward(monitor, bars, funding)
    first.pop("generated_at")
    second.pop("generated_at")

    assert first == second


def test_repository_candidate_matches_frozen_hash() -> None:
    path = (
        Path(__file__).parents[1]
        / "strategies/candidates/btc_eth_4h_reversal_factor_forward_v1.json"
    )

    candidate = load_frozen_factor_monitor(path)

    assert candidate.parameter_hash == (
        "04c313e63e0d6383bb09f7144e4b97e444038c3f0a956363cb13aa252cc264eb"
    )


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)
