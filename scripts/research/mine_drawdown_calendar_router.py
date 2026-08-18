#!/usr/bin/env python3
"""Search a causal monthly-drawdown recovery calendar strategy."""

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
    LEVERAGES,
    LOSS_LIMITS,
    PROFIT_TARGETS,
    SCORING_METHODS,
    _candidate_monthly,
    _config_line,
    _monthly_table,
)
from mine_defensive_factor_portfolio import _development_eligible, _strict_count  # noqa: E402
from mine_expanding_calendar_router import (  # noqa: E402
    FINAL_TRAIN_END_YEAR,
    FIRST_TRAIN_YEAR,
    LOOKBACK_YEARS,
    VALIDATION_YEARS,
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
from mastermind_tick.factor_portfolio import DailyReturns  # noqa: E402

TOP_K_VALUES = (1, 3, 5)
STATE_WEIGHTS = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75"))
RECOVERY_TRIGGERS = tuple(Decimal(value) for value in ("0.02", "0.05", "0.08", "0.10"))


@dataclass(frozen=True)
class DrawdownCalendarConfig:
    scoring: str
    calendar_lookback_years: int
    top_k: int
    state_weight: Decimal
    recovery_trigger: Decimal
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"drawdown-calendar-{self.scoring}-years{self.calendar_lookback_years}-"
            f"top{self.top_k}-state{self.state_weight}-trigger{self.recovery_trigger}-"
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
            "calendar_lookback_years": self.calendar_lookback_years,
            "top_k": self.top_k,
            "state_weight": float(self.state_weight),
            "trend_weight": float(Decimal("1") - self.state_weight),
            "monthly_recovery_trigger": float(self.recovery_trigger),
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
        default=Path("reports/experiments/drawdown_calendar_router/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state = _state_curves(loaded, args.metrics_dir)
    candidates = [
        candidate
        for candidate in _macd_candidates(loaded)
        if candidate.interval_minutes == 1440
    ]
    print(f"replaying {len(candidates)} daily MACD candidates", flush=True)
    rows = [_evaluate_macd(candidate) for candidate in candidates]
    routes = _route_search(state, rows)
    print(f"drawdown-recovery route variants: {len(routes)}", flush=True)
    eligible = _risk_search(routes)
    print(f"drawdown-recovery-risk-eligible configurations: {len(eligible)}", flush=True)
    audit = _confirmation_audit(eligible)
    payload = _report(loaded, candidates, routes, eligible, audit)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"drawdown-calendar-router-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    (args.output_dir / f"{report_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / f"{report_id}.md").write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)


def _drawdown_recovery_returns(
    state: DailyReturns,
    candidate_returns: dict[str, dict[str, Decimal]],
    long_mapping: dict[int, tuple[str, ...]],
    recovery_mapping: dict[int, tuple[str, ...]],
    state_weight: Decimal,
    recovery_trigger: Decimal,
    turnover_bps: Decimal,
    year: int,
) -> DailyReturns:
    prefix = f"{year}-"
    state_by_label = {
        label: value for label, value in state if label.startswith(prefix)
    }
    previous_weights: dict[str, Decimal] = {}
    current_month = ""
    month_equity = Decimal("1")
    recovery = False
    rate = turnover_bps / Decimal("10000")
    result = []
    for label in sorted(state_by_label):
        month = label[:7]
        if month != current_month:
            current_month = month
            month_equity = Decimal("1")
            recovery = False
        mapping = recovery_mapping if recovery else long_mapping
        candidate_ids = mapping[int(label[5:7])]
        if any(label not in candidate_returns[candidate_id] for candidate_id in candidate_ids):
            continue
        trend_weight = Decimal("1") - state_weight
        sleeve_weight = trend_weight / Decimal(len(candidate_ids))
        weights = {"state": state_weight}
        weights.update({candidate_id: sleeve_weight for candidate_id in candidate_ids})
        if previous_weights:
            names = set(previous_weights) | set(weights)
            turnover = sum(
                (
                    abs(
                        weights.get(name, Decimal("0"))
                        - previous_weights.get(name, Decimal("0"))
                    )
                    for name in names
                ),
                Decimal("0"),
            ) / Decimal("2")
        else:
            turnover = sum(weights.values(), Decimal("0"))
        value = state_weight * state_by_label[label] + sleeve_weight * sum(
            (candidate_returns[candidate_id][label] for candidate_id in candidate_ids),
            Decimal("0"),
        )
        value -= turnover * rate
        result.append((label, value))
        month_equity *= Decimal("1") + value
        if month_equity - Decimal("1") <= -recovery_trigger:
            recovery = True
        previous_weights = weights
    return tuple(result)


def _route_search(
    state: dict[str, DailyReturns], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    monthly = _candidate_monthly(rows)
    candidate_returns = {
        cost: {row["candidate"].id: dict(row["returns"][cost]) for row in rows}
        for cost in ("base", "stress")
    }
    routes = []
    years = (*VALIDATION_YEARS, FINAL_TRAIN_END_YEAR + 1)
    for scoring in SCORING_METHODS:
        for calendar_lookback in LOOKBACK_YEARS:
            for top_k in TOP_K_VALUES:
                mappings = {
                    year: {
                        direction: _direction_mapping(
                            rows,
                            monthly,
                            year,
                            scoring,
                            calendar_lookback,
                            direction,
                            top_k,
                        )
                        for direction in ("long_only", "long_short")
                    }
                    for year in years
                }
                for state_weight in STATE_WEIGHTS:
                    for recovery_trigger in RECOVERY_TRIGGERS:
                        development = {
                            cost: {
                                year: _drawdown_recovery_returns(
                                    state[cost],
                                    candidate_returns[cost],
                                    mappings[year]["long_only"],
                                    mappings[year]["long_short"],
                                    state_weight,
                                    recovery_trigger,
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
                        final_mapping = mappings[FINAL_TRAIN_END_YEAR + 1]
                        confirmation_returns = {
                            cost: _drawdown_recovery_returns(
                                state[cost],
                                candidate_returns[cost],
                                final_mapping["long_only"],
                                final_mapping["long_short"],
                                state_weight,
                                recovery_trigger,
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
                                    calendar_lookback,
                                    top_k,
                                    state_weight,
                                    recovery_trigger,
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
        scoring, calendar_lookback, top_k, state_weight, recovery_trigger = route["route"]
        for leverage in LEVERAGES:
            for loss_limit in LOSS_LIMITS:
                for profit_target in PROFIT_TARGETS:
                    config = DrawdownCalendarConfig(
                        scoring,
                        calendar_lookback,
                        top_k,
                        state_weight,
                        recovery_trigger,
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
        config: DrawdownCalendarConfig = row["config"]
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
        "strategy": "monthly-drawdown recovery between expanding calendar directions",
        "data": {
            "btc_first_bar": _timestamp(loaded["btc_perp"][0][0].start_ms),
            "btc_last_bar": _timestamp(loaded["btc_perp"][0][-1].end_ms),
        },
        "protocol": {
            "validation_years": list(VALIDATION_YEARS),
            "training_rule": "each validation year uses only prior calendar years",
            "final_mapping_train_years": [FIRST_TRAIN_YEAR, FINAL_TRAIN_END_YEAR],
            "recovery_timing": "activate long/short map on next day after MTD trigger",
            "confirmation": _period_payload(CONFIRMATION),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "family_extension_after_confirmation_review": True,
            "candidate_count": len(candidates),
        },
        "search": {
            "drawdown_route_count": len(routes),
            "drawdown_risk_eligible_count": len(eligible),
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
                "The development-selected drawdown recovery reached 7/7 only after a mechanism "
                "was added in response to prior confirmation review; fresh evidence is required."
                if selected_strict
                else "The drawdown-recovery calendar router selected without 2026 parameters did "
                "not reach +15% in all seven complete months under both cost models."
            ),
        },
        "limitations": [
            "The recovery mechanism was proposed after viewing reused 2026 confirmation.",
            "Early validation years have only two or three same-month training observations.",
            "Recovery begins one daily close after the loss trigger and cannot erase prior loss.",
            "Daily-close drawdown omits intraday liquidation and borrowing costs.",
        ],
    }


def _audit_payload(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    row = item["row"]
    return {
        "config": row["config"].as_dict(),
        "final_mapping": {
            direction: {
                f"{month:02d}": list(ids) for month, ids in mapping.items()
            }
            for direction, mapping in row["mappings"][FINAL_TRAIN_END_YEAR + 1].items()
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
        "Causal monthly-drawdown recovery from long-only to long/short calendar maps.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        f"- Development route variants: `{payload['search']['drawdown_route_count']}`.",
        f"- Development-risk-eligible controls: "
        f"`{payload['search']['drawdown_risk_eligible_count']}`.",
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
        "# Drawdown Calendar Router\n\n"
        "A causal month-to-date trigger persistently switches from long-only to long/short "
        "calendar maps on the next day. All parameters are selected before replaying 2026.\n\n"
        f"Decision: `{payload['decision']['status']}`; trading approval: `false`.\n"
    )


if __name__ == "__main__":
    main()
