#!/usr/bin/env python3
"""Search conditional complements for the frozen expanding calendar baseline."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_calendar_month_router import (  # noqa: E402
    LEVERAGES,
    LOSS_LIMITS,
    PROFIT_TARGETS,
    _candidate_monthly,
    _monthly_table,
)
from mine_defensive_factor_portfolio import _development_eligible, _strict_count  # noqa: E402
from mine_expanding_calendar_router import (  # noqa: E402
    FINAL_TRAIN_END_YEAR,
    FIRST_TRAIN_YEAR,
    VALIDATION_YEARS,
    _training_years,
    _year_returns,
)
from mine_factor_portfolio import CONFIRMATION  # noqa: E402
from mine_fast_trend_complement import _unlocked_result  # noqa: E402
from mine_momentum_calendar_router import _direction_mapping  # noqa: E402
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
from mastermind_tick.factor_portfolio import DailyReturns, monthly_returns  # noqa: E402

BASELINE_SCORING = "mean"
BASELINE_LOOKBACK_YEARS = 3
BASELINE_TOP_K = 3
BASELINE_STATE_WEIGHT = Decimal("0.5")
WEAK_MONTH_MODES = ("negative", "below_3pct", "bottom_3")
COMPLEMENT_SCORINGS = ("mean", "worst", "hit")
COMPLEMENT_LOOKBACK_YEARS = (2, 3, 5)
COMPLEMENT_DIRECTIONS = ("all", "long_short", "short_only")
COMPLEMENT_TOP_K = (1, 3, 5)
COMPLEMENT_WEIGHTS = tuple(Decimal(value) for value in ("0.10", "0.25", "0.5", "0.75", "0.9"))


@dataclass(frozen=True)
class ConditionalComplementConfig:
    weak_month_mode: str
    complement_scoring: str
    complement_lookback_years: int
    complement_direction: str
    complement_top_k: int
    complement_weight: Decimal
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"conditional-calendar-{self.weak_month_mode}-{self.complement_scoring}-"
            f"years{self.complement_lookback_years}-{self.complement_direction}-"
            f"top{self.complement_top_k}-weight{self.complement_weight}-"
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
            "weak_month_mode": self.weak_month_mode,
            "complement_scoring": self.complement_scoring,
            "complement_lookback_years": self.complement_lookback_years,
            "complement_direction": self.complement_direction,
            "complement_top_k": self.complement_top_k,
            "complement_weight": float(self.complement_weight),
            "baseline_weight": float(Decimal("1") - self.complement_weight),
            "leverage": float(self.leverage),
            "monthly_loss_limit": float(self.loss_limit),
            "monthly_profit_target": float(self.profit_target),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/conditional_calendar_complement/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state = _state_curves(loaded, args.metrics_dir)
    base_candidates = [
        candidate
        for candidate in _macd_candidates(loaded)
        if candidate.interval_minutes == 1440
    ]
    candidates = [*base_candidates, *_short_only_candidates(base_candidates)]
    print(f"replaying {len(candidates)} daily MACD candidates", flush=True)
    rows = [_evaluate_macd(candidate) for candidate in candidates]
    contexts, candidate_returns = _baseline_contexts(state, rows)
    routes = _route_search(rows, contexts, candidate_returns)
    print(f"conditional complement routes: {len(routes)}", flush=True)
    eligible = _risk_search(routes)
    print(f"conditional-risk-eligible configurations: {len(eligible)}", flush=True)
    audit = _confirmation_audit(eligible)
    payload = _report(loaded, candidates, routes, eligible, audit)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"conditional-calendar-complement-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    (args.output_dir / f"{report_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / f"{report_id}.md").write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)


def _short_only_targets(targets: tuple[int | None, ...]) -> tuple[int | None, ...]:
    """Retain only confirmed short states from an existing long/short signal."""
    return tuple(None if target is None else -1 if target < 0 else 0 for target in targets)


def _short_only_candidates(candidates: list[Any]) -> list[Any]:
    return [
        replace(candidate, direction="short_only", targets=_short_only_targets(candidate.targets))
        for candidate in candidates
        if candidate.direction == "long_short"
    ]


def _baseline_contexts(
    state: dict[str, DailyReturns], rows: list[dict[str, Any]]
) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, dict[str, Decimal]]]]:
    candidate_monthly = _candidate_monthly(rows)
    candidate_returns = {
        cost: {row["candidate"].id: dict(row["returns"][cost]) for row in rows}
        for cost in ("base", "stress")
    }
    contexts = {}
    for evaluation_year in (*VALIDATION_YEARS, FINAL_TRAIN_END_YEAR + 1):
        mapping = _direction_mapping(
            rows,
            candidate_monthly,
            evaluation_year,
            BASELINE_SCORING,
            BASELINE_LOOKBACK_YEARS,
            "long_only",
            BASELINE_TOP_K,
        )
        available_years = tuple(range(FIRST_TRAIN_YEAR, evaluation_year))
        history = {
            cost: {
                year: _year_returns(
                    state[cost],
                    candidate_returns[cost],
                    mapping,
                    BASELINE_STATE_WEIGHT,
                    BASE_OVERLAY_TURNOVER_BPS
                    if cost == "base"
                    else STRESS_OVERLAY_TURNOVER_BPS,
                    year,
                )
                for year in available_years
            }
            for cost in ("base", "stress")
        }
        evaluation = {
            cost: _year_returns(
                state[cost],
                candidate_returns[cost],
                mapping,
                BASELINE_STATE_WEIGHT,
                BASE_OVERLAY_TURNOVER_BPS
                if cost == "base"
                else STRESS_OVERLAY_TURNOVER_BPS,
                evaluation_year,
            )
            for cost in ("base", "stress")
        }
        contexts[evaluation_year] = {
            "mapping": mapping,
            "history": history,
            "evaluation": evaluation,
        }
    return contexts, candidate_returns


def _weak_labels(
    history: dict[str, dict[int, DailyReturns]],
    training_years: tuple[int, ...],
    mode: str,
) -> tuple[str, ...]:
    worst_by_label = {}
    for year in training_years:
        cost_monthly = {
            cost: dict(monthly_returns(yearly[year])) for cost, yearly in history.items()
        }
        labels = sorted(set(cost_monthly["base"]) & set(cost_monthly["stress"]))
        for label in labels:
            worst_by_label[label] = min(
                cost_monthly["base"][label], cost_monthly["stress"][label]
            )
    if mode == "negative":
        return tuple(label for label, value in worst_by_label.items() if value < 0)
    if mode == "below_3pct":
        return tuple(label for label, value in worst_by_label.items() if value < Decimal("0.03"))
    if mode == "bottom_3":
        selected = []
        for year in training_years:
            rows = sorted(
                (
                    (value, label)
                    for label, value in worst_by_label.items()
                    if label.startswith(f"{year}-")
                )
            )
            selected.extend(label for _value, label in rows[:3])
        return tuple(selected)
    raise ValueError(f"unsupported weak-month mode: {mode}")


def _complement_score(
    monthly: dict[str, dict[str, Decimal]],
    labels: tuple[str, ...],
    scoring: str,
) -> tuple[Decimal, ...] | None:
    values = tuple(
        monthly[cost][label]
        for cost in ("base", "stress")
        for label in labels
        if label in monthly[cost]
    )
    if not labels or len(values) != 2 * len(labels):
        return None
    average = sum(values, Decimal("0")) / Decimal(len(values))
    worst = min(values)
    hit = Decimal(sum(value > 0 for value in values)) / Decimal(len(values))
    if scoring == "mean":
        return average, worst, hit
    if scoring == "worst":
        return worst, average, hit
    if scoring == "hit":
        return hit, worst, average
    raise ValueError(f"unsupported complement scoring: {scoring}")


def _select_complements(
    rows: list[dict[str, Any]],
    candidate_monthly: dict[str, dict[str, dict[str, Decimal]]],
    labels: tuple[str, ...],
    scoring: str,
    direction: str,
    top_k: int,
) -> tuple[str, ...] | None:
    ranked = []
    for row in rows:
        candidate = row["candidate"]
        if direction != "all" and candidate.direction != direction:
            continue
        score = _complement_score(candidate_monthly[candidate.id], labels, scoring)
        if score is not None:
            ranked.append((score, candidate.id))
    ranked.sort(reverse=True)
    if len(ranked) < top_k:
        return None
    return tuple(candidate_id for _score, candidate_id in ranked[:top_k])


def _composite_returns(
    baseline: DailyReturns,
    candidate_returns: dict[str, dict[str, Decimal]],
    complement_ids: tuple[str, ...],
    complement_weight: Decimal,
    turnover_bps: Decimal,
) -> DailyReturns:
    baseline_weight = Decimal("1") - complement_weight
    rate = turnover_bps / Decimal("10000")
    result = []
    for index, (label, baseline_return) in enumerate(baseline):
        if any(label not in candidate_returns[candidate_id] for candidate_id in complement_ids):
            continue
        complement_return = sum(
            (candidate_returns[candidate_id][label] for candidate_id in complement_ids),
            Decimal("0"),
        ) / Decimal(len(complement_ids))
        value = baseline_weight * baseline_return + complement_weight * complement_return
        if index == 0:
            value -= complement_weight * rate
        result.append((label, value))
    return tuple(result)


def _route_search(
    rows: list[dict[str, Any]],
    contexts: dict[int, dict[str, Any]],
    candidate_returns: dict[str, dict[str, dict[str, Decimal]]],
) -> list[dict[str, Any]]:
    candidate_monthly = _candidate_monthly(rows)
    routes = []
    for weak_mode in WEAK_MONTH_MODES:
        for scoring in COMPLEMENT_SCORINGS:
            for lookback_years in COMPLEMENT_LOOKBACK_YEARS:
                for direction in COMPLEMENT_DIRECTIONS:
                    for top_k in COMPLEMENT_TOP_K:
                        selections = {}
                        valid = True
                        for year, context in contexts.items():
                            training_years = _training_years(year, lookback_years)
                            weak_labels = _weak_labels(
                                context["history"], training_years, weak_mode
                            )
                            complement_ids = _select_complements(
                                rows,
                                candidate_monthly,
                                weak_labels,
                                scoring,
                                direction,
                                top_k,
                            )
                            if complement_ids is None:
                                valid = False
                                break
                            selections[year] = {
                                "training_years": training_years,
                                "weak_labels": weak_labels,
                                "complement_ids": complement_ids,
                            }
                        if not valid:
                            continue
                        for complement_weight in COMPLEMENT_WEIGHTS:
                            development = {
                                cost: {
                                    year: _composite_returns(
                                        contexts[year]["evaluation"][cost],
                                        candidate_returns[cost],
                                        selections[year]["complement_ids"],
                                        complement_weight,
                                        BASE_OVERLAY_TURNOVER_BPS
                                        if cost == "base"
                                        else STRESS_OVERLAY_TURNOVER_BPS,
                                    )
                                    for year in VALIDATION_YEARS
                                }
                                for cost in ("base", "stress")
                            }
                            raw_results = {
                                cost: {
                                    str(year): _unlocked_result(values)
                                    for year, values in yearly.items()
                                }
                                for cost, yearly in development.items()
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
                            final_year = FINAL_TRAIN_END_YEAR + 1
                            confirmation_returns = {
                                cost: _composite_returns(
                                    contexts[final_year]["evaluation"][cost],
                                    candidate_returns[cost],
                                    selections[final_year]["complement_ids"],
                                    complement_weight,
                                    BASE_OVERLAY_TURNOVER_BPS
                                    if cost == "base"
                                    else STRESS_OVERLAY_TURNOVER_BPS,
                                )
                                for cost in ("base", "stress")
                            }
                            routes.append(
                                {
                                    "route": (
                                        weak_mode,
                                        scoring,
                                        lookback_years,
                                        direction,
                                        top_k,
                                        complement_weight,
                                    ),
                                    "selections": selections,
                                    "development": development,
                                    "confirmation_returns": confirmation_returns,
                                }
                            )
    return routes


def _risk_search(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = []
    for route in routes:
        weak_mode, scoring, lookback, direction, top_k, weight = route["route"]
        for leverage in LEVERAGES:
            for loss_limit in LOSS_LIMITS:
                for profit_target in PROFIT_TARGETS:
                    config = ConditionalComplementConfig(
                        weak_mode,
                        scoring,
                        lookback,
                        direction,
                        top_k,
                        weight,
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
                            for year, values in yearly.items()
                        }
                        for cost, yearly in route["development"].items()
                    }
                    if _development_eligible(results):
                        eligible.append(
                            {
                                "config": config,
                                "selections": route["selections"],
                                "confirmation_returns": route["confirmation_returns"],
                                "results": results,
                                "score": _risk_score(results),
                            }
                        )
    return sorted(eligible, key=lambda row: row["score"], reverse=True)


def _confirmation_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    audited = []
    for row in rows:
        config: ConditionalComplementConfig = row["config"]
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
) -> dict[str, Any]:
    selected_strict = audit["development_selected_strict"]
    best = audit["best_confirmation"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "conditional complement around frozen expanding calendar baseline",
        "data": {
            "btc_first_bar": _timestamp(loaded["btc_perp"][0][0].start_ms),
            "btc_last_bar": _timestamp(loaded["btc_perp"][0][-1].end_ms),
        },
        "protocol": {
            "frozen_baseline": {
                "scoring": BASELINE_SCORING,
                "lookback_years": BASELINE_LOOKBACK_YEARS,
                "direction": "long_only",
                "top_k": BASELINE_TOP_K,
                "state_weight": float(BASELINE_STATE_WEIGHT),
            },
            "validation_years": list(VALIDATION_YEARS),
            "complement_rule": "select only on prior-year frozen-baseline weak months",
            "confirmation": _period_payload(CONFIRMATION),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "family_extension_after_confirmation_review": True,
            "candidate_count": len(candidates),
        },
        "search": {
            "conditional_route_count": len(routes),
            "conditional_risk_eligible_count": len(eligible),
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
            "status": (
                "reused_confirmation_candidate_post_confirmation_family_extension"
                if selected_strict
                else "rejected_no_development_selected_strict_solution"
            ),
            "approved_for_trading": False,
            "reason": (
                "The development-selected conditional complement reached 7/7 only after a "
                "family extension proposed from prior confirmation review; fresh evidence is "
                "still required."
                if selected_strict
                else "No conditional complement selected without 2026 parameters reached +15% "
                "in all seven complete months under both cost models."
            ),
        },
        "limitations": [
            "The complement family was proposed after viewing reused 2026 confirmation.",
            "The frozen baseline was selected on the same 2023-2025 development years.",
            "Weak-month conditional samples are small and the final fit is in-sample through 2025.",
            "Daily-close drawdown omits intraday liquidation and borrowing costs.",
        ],
    }


def _audit_payload(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    row = item["row"]
    final_selection = row["selections"][FINAL_TRAIN_END_YEAR + 1]
    return {
        "config": row["config"].as_dict(),
        "final_selection": {
            "training_years": list(final_selection["training_years"]),
            "weak_labels": list(final_selection["weak_labels"]),
            "complement_ids": list(final_selection["complement_ids"]),
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
        "Prior-year weak-month complements around the frozen expanding calendar baseline.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        f"- Development route variants: `{payload['search']['conditional_route_count']}`.",
        f"- Development-risk-eligible controls: "
        f"`{payload['search']['conditional_risk_eligible_count']}`.",
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


def _config_line(row: dict[str, Any]) -> str:
    config = row["config"]
    return (
        f"`{config['id']}`; baseline/complement `"
        f"{config['baseline_weight']:.0%}/{config['complement_weight']:.0%}`; "
        f"leverage `{config['leverage']:.2f}x`; "
        f"locks `{config['monthly_loss_limit']:.0%}/{config['monthly_profit_target']:.0%}`."
    )


def _readme(payload: dict[str, Any]) -> str:
    return (
        "# Conditional Calendar Complement\n\n"
        "Complement sleeves are selected only from weak months of the frozen calendar baseline "
        "in years before each walk-forward validation year.\n\n"
        f"Decision: `{payload['decision']['status']}`; trading approval: `false`.\n"
    )


if __name__ == "__main__":
    main()
