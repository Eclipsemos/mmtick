import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parents[1]
STRATEGY_PATH = (
    ROOT / "strategies" / "candidates" / "btc_eth_expanding_calendar_router_v1.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)


def test_frozen_calendar_router_matches_development_selected_report() -> None:
    strategy = _load(STRATEGY_PATH)
    report = _load(ROOT / strategy["source_report"])
    selected = report["selection"]["development_selected"]
    config = selected["config"]
    parameters = strategy["parameters"]

    assert strategy["status"] == "frozen_forward_paper_candidate"
    assert strategy["approved_for_paper_observation"] is True
    assert strategy["approved_for_live_trading"] is False
    assert strategy["source_commit"] == "cc5bb464ea08018984efdebda19a6148169cfd67"
    assert parameters["mapping_mode"] == "fixed_2026_mapping_no_refit"
    assert parameters["mapping_training_years"] == [2023, 2024, 2025]
    assert parameters["trend_direction_filter"] == config["direction_filter"]
    assert parameters["trend_top_k"] == config["top_k"]
    assert parameters["state_weight"] == config["state_weight"]
    assert parameters["trend_weight"] == config["trend_weight"]
    assert parameters["outer_leverage"] == config["leverage"]
    assert parameters["monthly_loss_lock"] == config["monthly_loss_limit"]
    assert parameters["monthly_profit_lock"] == config["monthly_profit_target"]
    assert parameters["fixed_month_mapping"] == selected["mappings"]["2026"]


def test_frozen_calendar_router_evidence_is_derived_from_source_report() -> None:
    strategy = _load(STRATEGY_PATH)
    report = _load(ROOT / strategy["source_report"])
    confirmation = report["selection"]["development_selected"]["confirmation"]
    evidence = strategy["evidence"]

    for cost in ("base", "stress"):
        complete = tuple(
            row
            for row in confirmation[cost]["monthly_returns"]
            if row["label"] <= "2026-07"
        )
        target_count = sum(row["return"] >= Decimal("0.15") for row in complete)
        compounded = Decimal("1")
        for row in complete:
            compounded *= Decimal("1") + row["return"]

        assert len(complete) == evidence["confirmation_complete_months"]
        assert target_count == evidence[f"confirmation_months_at_least_15pct_{cost}"]
        assert compounded - Decimal("1") == evidence[
            f"confirmation_jan_jul_compound_return_{cost}"
        ]
        assert confirmation[cost]["max_drawdown"] == evidence[
            f"confirmation_max_daily_close_drawdown_{cost}_including_partial_august"
        ]

    assert report["protocol"]["confirmation_used_for_selection"] is False
    assert evidence["confirmation_is_fresh_holdout"] is False
    assert strategy["forward_review"]["exclude_partial_august_2026"] is True
    assert strategy["first_complete_forward_month"] == "2026-09"
