import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mastermind_tick.volatility_spread import SpreadBar, SpreadExecution
from mastermind_tick.volatility_spread_forward import (
    evaluate_frozen_forward,
    load_frozen_candidate,
    render_forward_markdown,
)


def _candidate(tmp_path):
    payload = {
        "schema_version": 1,
        "id": "test-spread-v1",
        "instrument_id": "soxl_perp",
        "symbol": "SOXLUSDT",
        "strategy_family": "volatility_spread",
        "status": "insufficient_fresh_evidence",
        "approved_for_trading": False,
        "evidence_lock_date": "2026-08-13",
        "forward_evidence_start_date": "2026-08-14",
        "continuous_replay_start_ms": _day_start(date(2026, 8, 12)),
        "source_report": "reports/source.json",
        "parameters": {
            "variant": "expansion_breakout",
            "direction": "long_only",
            "fast_window": 1,
            "slow_window": 2,
            "entry_ratio": 1.1,
            "exit_ratio": 0,
            "breakout_window": 1,
            "stop_atr": 10,
            "max_hold_bars": 1,
            "exposure": 1,
            "compression_ratio": 0.85,
            "compression_lookback": 2,
            "spread_measure": "true_range",
            "minimum_volume_ratio": None,
        },
        "execution": {
            "bar_interval_minutes": 15,
            "fill_timing": "first_persisted_tick_after_closed_bar_signal",
            "fee_bps_per_fill": 0,
            "slippage_bps_per_fill": 0,
            "quantity_step": "0.01",
            "initial_equity": "100000",
            "funding_included": True,
        },
        "research_target": {"geometric_daily_return": 0.05, "achieved": False},
        "forward_gates": {
            "minimum_complete_days_for_interim_review": 1,
            "minimum_completed_trades_for_interim_review": 1,
            "minimum_complete_days_for_approval_review": 90,
            "minimum_completed_trades_for_approval_review": 100,
        },
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_frozen_candidate(path)


def _market(start: date, days: int):
    bars = []
    executions = []
    for offset in range(days * 96):
        start_ms = _day_start(start) + offset * 900_000
        phase = offset % 8
        close = Decimal("110") if phase == 2 else Decimal("100")
        bar = SpreadBar(
            start_ms=start_ms,
            end_ms=start_ms + 899_999,
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=Decimal("1"),
        )
        bars.append(bar)
        executions.append(SpreadExecution(timestamp_ms=start_ms + 1, price=close))
    return bars, [[] for _ in bars], executions


def test_no_post_lock_complete_day_returns_awaiting_data(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    bars, funding, executions = _market(date(2026, 8, 12), 2)

    payload = evaluate_frozen_forward(candidate, bars, funding, executions)

    assert payload["status"] == "awaiting_data"
    assert payload["forward"]["complete_days"] == 0
    assert payload["forward"]["daily_returns"] == []
    assert payload["parameter_search_performed"] is False
    assert "awaiting_data" in render_forward_markdown(payload)


def test_complete_post_lock_day_produces_forward_only_metrics(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    bars, funding, executions = _market(date(2026, 8, 12), 3)

    payload = evaluate_frozen_forward(candidate, bars, funding, executions)

    assert payload["data_through_date"] == "2026-08-14"
    assert payload["forward"]["complete_days"] == 1
    assert [row["date"] for row in payload["forward"]["daily_returns"]] == ["2026-08-14"]
    assert all(
        row["date"] > payload["evidence_lock_date"] for row in payload["forward"]["daily_returns"]
    )
    assert payload["parameter_search_performed"] is False
    assert payload["target"]["achieved"] is False
    assert payload["gates"]["approval_review_sample_ready"] is False


def test_pre_lock_as_of_date_cannot_be_forward_evidence(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    bars, funding, executions = _market(date(2026, 8, 12), 3)

    with pytest.raises(ValueError, match="must be after 2026-08-13"):
        evaluate_frozen_forward(
            candidate,
            bars,
            funding,
            executions,
            as_of_date=date(2026, 8, 13),
        )


def test_forward_output_is_deterministic_for_the_same_inputs(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    bars, funding, executions = _market(date(2026, 8, 12), 3)

    first = evaluate_frozen_forward(candidate, bars, funding, executions)
    second = evaluate_frozen_forward(candidate, bars, funding, executions)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert render_forward_markdown(first) == render_forward_markdown(second)


def test_frozen_parameters_match_the_source_research_report() -> None:
    root = Path(__file__).parents[1]
    candidate_payload = json.loads(
        (root / "strategies/candidates/soxl_volatility_spread_true_range_v1.json").read_text()
    )
    report_payload = json.loads(
        (root / "reports/experiments/soxl_volatility_spread/2026-08-14-v2/results.json").read_text()
    )

    assert (
        candidate_payload["parameters"]
        == report_payload["locked_candidates"]["true_range"]["parameters"]
    )
    assert candidate_payload["approved_for_trading"] is False


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)
