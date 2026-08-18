#!/usr/bin/env python3
"""Search an expanding-window calendar-month BTC/ETH trend router."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_calendar_month_router import (  # noqa: E402
    DIRECTION_FILTERS,
    LEVERAGES,
    LOSS_LIMITS,
    PROFIT_TARGETS,
    SCORING_METHODS,
    STATE_WEIGHTS,
    TOP_K_VALUES,
    _candidate_monthly,
    _config_line,
    _month_score_for_years,
    _monthly_table,
    _seasonal_returns,
)
from mine_defensive_factor_portfolio import _development_eligible, _strict_count  # noqa: E402
from mine_factor_portfolio import CONFIRMATION  # noqa: E402
from mine_fast_trend_complement import _unlocked_result  # noqa: E402
from mine_monthly_robust_ensemble import _result_payload, _risk_score  # noqa: E402
from mine_monthly_target_regime_router import (  # noqa: E402
    ASSETS,
    BASE_OVERLAY_TURNOVER_BPS,
    COMPLETE_CONFIRMATION_END,
    STRESS_OVERLAY_TURNOVER_BPS,
    TARGET_MONTHLY_RETURN,
    _evaluate_macd,
    _macd_candidates,
    _period_payload,
    _state_curves,
    _timestamp,
)

from mastermind_tick.bar_research import ResearchBar  # noqa: E402
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import (  # noqa: E402
    MonthlyRiskConfig,
    evaluate_monthly_risk_overlay,
)
from mastermind_tick.factor_portfolio import DailyReturns  # noqa: E402

FIRST_TRAIN_YEAR = 2021
VALIDATION_YEARS = (2023, 2024, 2025)
FINAL_TRAIN_END_YEAR = 2025
LOOKBACK_YEARS = (2, 3, 5)


@dataclass(frozen=True)
class ExpandingConfig:
    scoring: str
    lookback_years: int
    direction_filter: str
    top_k: int
    state_weight: Decimal
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"expanding-calendar-{self.scoring}-years{self.lookback_years}-"
            f"{self.direction_filter}-top{self.top_k}-state{self.state_weight}-"
            f"lev{self.leverage}-loss{self.loss_limit}-profit{self.profit_target}"
        )

    def risk(self, turnover_bps: Decimal) -> MonthlyRiskConfig:
        return MonthlyRiskConfig(
            self.leverage,
            self.loss_limit,
            self.profit_target,
            turnover_bps,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scoring": self.scoring,
            "lookback_years": self.lookback_years,
            "direction_filter": self.direction_filter,
            "top_k": self.top_k,
            "state_weight": float(self.state_weight),
            "trend_weight": float(Decimal("1") - self.state_weight),
            "leverage": float(self.leverage),
            "monthly_loss_limit": float(self.loss_limit),
            "monthly_profit_target": float(self.profit_target),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--intervals",
        type=int,
        nargs="+",
        choices=(60, 240, 1440),
        default=(1440,),
        help="MACD bar intervals included in the frozen candidate universe",
    )
    parser.add_argument(
        "--family-extension-after-confirmation-review",
        action="store_true",
        help="mark a model-family extension proposed after prior 2026 review",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/expanding_calendar_router/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state = _state_curves(loaded, args.metrics_dir)
    candidates = [
        candidate
        for candidate in _macd_candidates(loaded)
        if candidate.interval_minutes in args.intervals
    ]
    print(f"replaying {len(candidates)} MACD candidates", flush=True)
    rows = [_evaluate_macd(candidate) for candidate in candidates]
    routes = _route_search(state, rows)
    print(f"walk-forward route variants: {len(routes)}", flush=True)
    eligible = _risk_search(routes)
    print(f"walk-forward-risk-eligible configurations: {len(eligible)}", flush=True)
    audit = _confirmation_audit(eligible)
    payload = _report(
        loaded,
        candidates,
        routes,
        eligible,
        audit,
        family_extension_after_confirmation_review=(
            args.family_extension_after_confirmation_review
        ),
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"expanding-calendar-router-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    (args.output_dir / f"{report_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / f"{report_id}.md").write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)


def _training_years(validation_year: int, lookback_years: int) -> tuple[int, ...]:
    available = tuple(range(FIRST_TRAIN_YEAR, validation_year))
    return available[-lookback_years:]


def _year_mapping(
    rows: list[dict[str, Any]],
    monthly: dict[str, dict[str, dict[str, Decimal]]],
    validation_year: int,
    scoring: str,
    lookback_years: int,
    direction_filter: str,
    top_k: int,
) -> dict[int, tuple[str, ...]]:
    years = _training_years(validation_year, lookback_years)
    mapping = {}
    for month in range(1, 13):
        ranked = []
        for row in rows:
            candidate = row["candidate"]
            if direction_filter != "all" and candidate.direction != direction_filter:
                continue
            score = _month_score_for_years(monthly[candidate.id], month, scoring, years)
            if score is not None:
                ranked.append((score, candidate.id))
        ranked.sort(reverse=True)
        if len(ranked) < top_k:
            raise RuntimeError(f"year {validation_year} month {month} has too few candidates")
        mapping[month] = tuple(candidate_id for _score, candidate_id in ranked[:top_k])
    return mapping


def _year_returns(
    state: DailyReturns,
    candidate_returns: dict[str, dict[str, Decimal]],
    mapping: dict[int, tuple[str, ...]],
    state_weight: Decimal,
    turnover_bps: Decimal,
    year: int,
) -> DailyReturns:
    prefix = f"{year}-"
    year_state = tuple((label, value) for label, value in state if label.startswith(prefix))
    year_candidates = {
        candidate_id: {
            label: value for label, value in returns.items() if label.startswith(prefix)
        }
        for candidate_id, returns in candidate_returns.items()
    }
    return _seasonal_returns(
        year_state,
        year_candidates,
        mapping,
        state_weight,
        turnover_bps,
    )


def _route_search(
    state: dict[str, DailyReturns], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    monthly = _candidate_monthly(rows)
    candidate_returns = {
        cost: {row["candidate"].id: dict(row["returns"][cost]) for row in rows}
        for cost in ("base", "stress")
    }
    routes = []
    for scoring in SCORING_METHODS:
        for lookback_years in LOOKBACK_YEARS:
            for direction_filter in DIRECTION_FILTERS:
                for top_k in TOP_K_VALUES:
                    mappings = {
                        year: _year_mapping(
                            rows,
                            monthly,
                            year,
                            scoring,
                            lookback_years,
                            direction_filter,
                            top_k,
                        )
                        for year in (*VALIDATION_YEARS, FINAL_TRAIN_END_YEAR + 1)
                    }
                    for state_weight in STATE_WEIGHTS:
                        development = {
                            cost: {
                                year: _year_returns(
                                    state[cost],
                                    candidate_returns[cost],
                                    mappings[year],
                                    state_weight,
                                    BASE_OVERLAY_TURNOVER_BPS
                                    if cost == "base"
                                    else STRESS_OVERLAY_TURNOVER_BPS,
                                    year,
                                )
                                for year in VALIDATION_YEARS
                            }
                            for cost in ("base", "stress")
                        }
                        raw_results = {
                            cost: {
                                str(year): _unlocked_result(values)
                                for year, values in years.items()
                            }
                            for cost, years in development.items()
                        }
                        if not all(
                            result.net_return > 0
                            and result.max_drawdown >= Decimal("-0.35")
                            and result.positive_month_rate >= Decimal("0.5")
                            and not result.bankrupt
                            for costs in raw_results.values()
                            for result in costs.values()
                        ):
                            continue
                        confirmation_returns = {
                            cost: _year_returns(
                                state[cost],
                                candidate_returns[cost],
                                mappings[FINAL_TRAIN_END_YEAR + 1],
                                state_weight,
                                BASE_OVERLAY_TURNOVER_BPS
                                if cost == "base"
                                else STRESS_OVERLAY_TURNOVER_BPS,
                                FINAL_TRAIN_END_YEAR + 1,
                            )
                            for cost in ("base", "stress")
                        }
                        routes.append(
                            {
                                "route": (
                                    scoring,
                                    lookback_years,
                                    direction_filter,
                                    top_k,
                                    state_weight,
                                ),
                                "mappings": mappings,
                                "development": development,
                                "confirmation_returns": confirmation_returns,
                            }
                        )
    return routes


def _risk_search(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = []
    for route in routes:
        scoring, lookback_years, direction_filter, top_k, state_weight = route["route"]
        for leverage in LEVERAGES:
            for loss_limit in LOSS_LIMITS:
                for profit_target in PROFIT_TARGETS:
                    config = ExpandingConfig(
                        scoring,
                        lookback_years,
                        direction_filter,
                        top_k,
                        state_weight,
                        leverage,
                        loss_limit,
                        profit_target,
                    )
                    results = {
                        cost: {
                            str(year): evaluate_monthly_risk_overlay(
                                values,
                                config.risk(
                                    BASE_OVERLAY_TURNOVER_BPS
                                    if cost == "base"
                                    else STRESS_OVERLAY_TURNOVER_BPS
                                ),
                            )
                            for year, values in years.items()
                        }
                        for cost, years in route["development"].items()
                    }
                    if _development_eligible(results):
                        eligible.append(
                            {
                                "config": config,
                                "mappings": route["mappings"],
                                "confirmation_returns": route["confirmation_returns"],
                                "results": results,
                                "score": _risk_score(results),
                            }
                        )
    return sorted(eligible, key=lambda row: row["score"], reverse=True)


def _confirmation_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    audited = []
    for row in rows:
        config: ExpandingConfig = row["config"]
        results = {
            cost: evaluate_monthly_risk_overlay(
                values,
                config.risk(
                    BASE_OVERLAY_TURNOVER_BPS
                    if cost == "base"
                    else STRESS_OVERLAY_TURNOVER_BPS
                ),
            )
            for cost, values in row["confirmation_returns"].items()
        }
        counts = {cost: _strict_count(result) for cost, result in results.items()}
        audited.append({"row": row, "results": results, "counts": counts})
    ranked = sorted(
        audited,
        key=lambda item: (
            min(item["counts"].values()),
            sum(item["counts"].values()),
            item["row"]["score"],
        ),
        reverse=True,
    )
    selected = audited[0] if audited else None
    return {
        "configuration_count": len(audited),
        "strict_pass_count": sum(
            item["counts"] == {"base": 7, "stress": 7} for item in audited
        ),
        "development_selected": selected,
        "development_selected_strict": bool(
            selected and selected["counts"] == {"base": 7, "stress": 7}
        ),
        "best_confirmation": ranked[0] if ranked else None,
    }


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    candidates: list[Any],
    routes: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    audit: dict[str, Any],
    *,
    family_extension_after_confirmation_review: bool = False,
) -> dict[str, Any]:
    selected_strict = audit["development_selected_strict"]
    best = audit["best_confirmation"]
    candidate_intervals = sorted({candidate.interval_minutes for candidate in candidates})
    status = (
        "reused_confirmation_candidate_post_confirmation_family_extension"
        if selected_strict and family_extension_after_confirmation_review
        else "reused_confirmation_candidate"
        if selected_strict
        else "rejected_no_development_selected_strict_solution"
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "expanding-window calendar-month BTC/ETH MACD router",
        "data": {
            "btc_first_bar": _timestamp(loaded["btc_perp"][0][0].start_ms),
            "btc_last_bar": _timestamp(loaded["btc_perp"][0][-1].end_ms),
        },
        "protocol": {
            "validation_years": list(VALIDATION_YEARS),
            "training_rule": "each validation year uses only prior calendar years",
            "final_mapping_train_years": [FIRST_TRAIN_YEAR, FINAL_TRAIN_END_YEAR],
            "confirmation": _period_payload(CONFIRMATION),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "candidate_count": len(candidates),
            "candidate_intervals_minutes": candidate_intervals,
            "family_extension_after_confirmation_review": (
                family_extension_after_confirmation_review
            ),
        },
        "search": {
            "walk_forward_route_count": len(routes),
            "walk_forward_risk_eligible_count": len(eligible),
        },
        "selection": {"development_selected": _audit_payload(audit["development_selected"])},
        "confirmation_audit": {
            "configuration_count": audit["configuration_count"],
            "strict_pass_count": audit["strict_pass_count"],
            "development_selected_strict": selected_strict,
            "best_complete_month_count": min(best["counts"].values()) if best else 0,
            "best_confirmation_diagnostic": _audit_payload(best),
        },
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "required_complete_months": 7,
            "achieved_by_development_selected": selected_strict,
        },
        "decision": {
            "status": status,
            "approved_for_trading": False,
            "reason": (
                "The development-selected family extension reached 7/7 only after the model "
                "family was expanded in response to prior confirmation review; fresh forward "
                "evidence remains required."
                if selected_strict and family_extension_after_confirmation_review
                else "The expanding-window development selection reached 7/7 in reused "
                "confirmation; fresh forward evidence remains required."
                if selected_strict
                else "The expanding-window calendar router selected without 2026 data did not "
                "reach +15% in all seven complete months under both cost models."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "Early validation years have only two or three same-month training observations.",
            "The frozen state strategy itself was selected in earlier research.",
            "Daily-close drawdown omits intraday liquidation and borrowing costs.",
        ],
    }


def _audit_payload(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    row = item["row"]
    return {
        "config": row["config"].as_dict(),
        "mappings": {
            str(year): {f"{month:02d}": list(ids) for month, ids in mapping.items()}
            for year, mapping in row["mappings"].items()
        },
        "counts": item["counts"],
        "walk_forward_development": {
            cost: {year: _result_payload(result) for year, result in values.items()}
            for cost, values in row["results"].items()
        },
        "confirmation": {
            cost: _result_payload(result) for cost, result in item["results"].items()
        },
    }


def _markdown(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    selected = payload["selection"]["development_selected"]
    best = audit["best_confirmation_diagnostic"]
    lines = [
        f"# {payload['id']}",
        "",
        "Expanding-window calendar-month routing of BTC/ETH MACD sleeves.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        f"- Walk-forward route variants: `{payload['search']['walk_forward_route_count']}`.",
        f"- Walk-forward-risk-eligible controls: "
        f"`{payload['search']['walk_forward_risk_eligible_count']}`.",
        f"- Development-selected strict result: "
        f"`{str(audit['development_selected_strict']).lower()}`.",
        f"- Confirmation-diagnostic strict configurations: `{audit['strict_pass_count']}`.",
        f"- Best reused-confirmation coverage: `{audit['best_complete_month_count']}/7`.",
        "",
        payload["decision"]["reason"],
        "Partial `2026-08` is excluded from strict counts.",
    ]
    if selected:
        lines.extend(["", "## Development Selected", "", _config_line(selected), ""])
        lines.extend(_monthly_table(selected))
    if best and best["config"]["id"] != selected["config"]["id"]:
        lines.extend(["", "## Best Confirmation Diagnostic", "", _config_line(best), ""])
        lines.extend(_monthly_table(best))
        lines.extend(["", "This diagnostic was identified after viewing 2026 and is not selected."])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _readme(payload: dict[str, Any]) -> str:
    return (
        "# Expanding Calendar-Month Router\n\n"
        "The router is validated year by year using only prior-year month observations, then "
        "refit on 2021-2025 before reused 2026 confirmation.\n\n"
        f"Decision: `{payload['decision']['status']}`; trading approval: `false`.\n"
    )


if __name__ == "__main__":
    main()
