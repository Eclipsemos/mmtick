#!/usr/bin/env python3
"""Search a development-only calendar-month BTC/ETH trend router."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_defensive_factor_portfolio import (  # noqa: E402
    _development_eligible,
    _period_returns,
    _strict_count,
)
from mine_factor_portfolio import CONFIRMATION, DISCOVERY  # noqa: E402
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
from mastermind_tick.factor_portfolio import DailyReturns, monthly_returns  # noqa: E402

DISCOVERY_YEARS = (2021, 2022, 2023)
VALIDATION_2024 = (
    int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000),
    int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1,
)
VALIDATION_2025 = (
    int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000),
    int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1,
)
SCORING_METHODS = ("worst", "mean", "target")
DISCOVERY_LOOKBACK_YEARS = (1, 2, 3)
DIRECTION_FILTERS = ("all", "long_only", "long_short")
TOP_K_VALUES = (1, 2, 3, 5)
STATE_WEIGHTS = tuple(Decimal(value) for value in ("0", "0.25", "0.5", "0.75", "0.9"))
LEVERAGES = tuple(Decimal(value) for value in ("1", "1.5", "2", "3", "4", "5", "6", "8"))
LOSS_LIMITS = tuple(Decimal(value) for value in ("0.10", "0.15", "0.20", "0.25"))
PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.16", "0.18", "0.20"))


@dataclass(frozen=True)
class CalendarConfig:
    scoring: str
    discovery_lookback_years: int
    direction_filter: str
    top_k: int
    state_weight: Decimal
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"calendar-{self.scoring}-years{self.discovery_lookback_years}-"
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
            "discovery_lookback_years": self.discovery_lookback_years,
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
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/calendar_month_router/2026-08-15"),
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
    route_rows = _route_search(state, rows)
    print(f"development route variants: {len(route_rows)}", flush=True)
    eligible = _risk_search(route_rows)
    print(f"development-risk-eligible configurations: {len(eligible)}", flush=True)
    audit = _confirmation_audit(eligible)
    payload = _report(loaded, candidates, route_rows, eligible, audit)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"calendar-month-router-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    (args.output_dir / f"{report_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / f"{report_id}.md").write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)


def _candidate_monthly(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Decimal]]]:
    return {
        row["candidate"].id: {
            cost: dict(monthly_returns(returns)) for cost, returns in row["returns"].items()
        }
        for row in rows
    }


def _month_score(
    monthly: dict[str, dict[str, Decimal]],
    month: int,
    scoring: str,
    lookback_years: int,
) -> tuple[Decimal, ...] | None:
    years = DISCOVERY_YEARS[-lookback_years:]
    return _month_score_for_years(monthly, month, scoring, years)


def _month_score_for_years(
    monthly: dict[str, dict[str, Decimal]],
    month: int,
    scoring: str,
    years: tuple[int, ...],
) -> tuple[Decimal, ...] | None:
    labels = tuple(f"{year}-{month:02d}" for year in years)
    values = tuple(
        monthly[cost][label]
        for cost in ("base", "stress")
        for label in labels
        if label in monthly[cost]
    )
    if len(values) != 2 * len(labels):
        return None
    average = sum(values, Decimal("0")) / Decimal(len(values))
    worst = min(values)
    positive = Decimal(sum(value > 0 for value in values)) / Decimal(len(values))
    target = Decimal(sum(value >= TARGET_MONTHLY_RETURN for value in values)) / Decimal(
        len(values)
    )
    if scoring == "worst":
        return worst, target, positive, average
    if scoring == "mean":
        return average, worst, target, positive
    if scoring == "target":
        return target, positive, worst, average
    raise ValueError(f"unsupported calendar scoring: {scoring}")


def _calendar_mapping(
    rows: list[dict[str, Any]],
    scoring: str,
    lookback_years: int,
    direction_filter: str,
    top_k: int,
) -> dict[int, tuple[str, ...]]:
    monthly = _candidate_monthly(rows)
    mapping = {}
    for month in range(1, 13):
        ranked = []
        for row in rows:
            candidate = row["candidate"]
            if direction_filter != "all" and candidate.direction != direction_filter:
                continue
            score = _month_score(monthly[candidate.id], month, scoring, lookback_years)
            if score is not None:
                ranked.append((score, candidate.id))
        ranked.sort(reverse=True)
        if len(ranked) < top_k:
            raise RuntimeError(f"calendar month {month} has too few eligible candidates")
        mapping[month] = tuple(candidate_id for _score, candidate_id in ranked[:top_k])
    return mapping


def _seasonal_returns(
    state: DailyReturns,
    candidate_by_id: dict[str, dict[str, Decimal]],
    mapping: dict[int, tuple[str, ...]],
    state_weight: Decimal,
    turnover_bps: Decimal,
) -> DailyReturns:
    state_by_label = dict(state)
    labels = sorted(state_by_label)
    previous_weights: dict[str, Decimal] = {}
    current_month = ""
    rate = turnover_bps / Decimal("10000")
    result = []
    for label in labels:
        month = label[:7]
        candidate_ids = mapping[int(label[5:7])]
        if any(label not in candidate_by_id[candidate_id] for candidate_id in candidate_ids):
            continue
        trend_weight = Decimal("1") - state_weight
        sleeve_weight = trend_weight / Decimal(len(candidate_ids))
        weights = {"state": state_weight}
        weights.update({candidate_id: sleeve_weight for candidate_id in candidate_ids})
        turnover = Decimal("0")
        if month != current_month:
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
            previous_weights = weights
            current_month = month
        value = state_weight * state_by_label[label] + sleeve_weight * sum(
            (candidate_by_id[candidate_id][label] for candidate_id in candidate_ids),
            Decimal("0"),
        )
        result.append((label, value - turnover * rate))
    return tuple(result)


def _route_search(
    state: dict[str, DailyReturns], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    candidate_returns = {
        cost: {row["candidate"].id: dict(row["returns"][cost]) for row in rows}
        for cost in ("base", "stress")
    }
    routes = []
    for scoring in SCORING_METHODS:
        for lookback_years in DISCOVERY_LOOKBACK_YEARS:
            for direction_filter in DIRECTION_FILTERS:
                for top_k in TOP_K_VALUES:
                    mapping = _calendar_mapping(
                        rows, scoring, lookback_years, direction_filter, top_k
                    )
                    for state_weight in STATE_WEIGHTS:
                        returns = {
                            cost: _seasonal_returns(
                                state[cost],
                                candidate_returns[cost],
                                mapping,
                                state_weight,
                                BASE_OVERLAY_TURNOVER_BPS
                                if cost == "base"
                                else STRESS_OVERLAY_TURNOVER_BPS,
                            )
                            for cost in ("base", "stress")
                        }
                        raw_results = {
                            cost: {
                                "train": _unlocked_result(_period_returns(values, DISCOVERY)),
                                "validation_2024": _unlocked_result(
                                    _period_returns(values, VALIDATION_2024)
                                ),
                                "validation_2025": _unlocked_result(
                                    _period_returns(values, VALIDATION_2025)
                                ),
                            }
                            for cost, values in returns.items()
                        }
                        if all(
                            result.net_return > 0
                            and result.max_drawdown >= Decimal("-0.35")
                            and result.positive_month_rate >= Decimal("0.5")
                            and not result.bankrupt
                            for costs in raw_results.values()
                            for result in costs.values()
                        ):
                            routes.append(
                                {
                                    "route": (
                                        scoring,
                                        lookback_years,
                                        direction_filter,
                                        top_k,
                                        state_weight,
                                    ),
                                    "mapping": mapping,
                                    "returns": returns,
                                }
                            )
    return routes


def _risk_search(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    periods = {
        "train": DISCOVERY,
        "validation_2024": VALIDATION_2024,
        "validation_2025": VALIDATION_2025,
    }
    eligible = []
    for route in routes:
        scoring, lookback_years, direction_filter, top_k, state_weight = route["route"]
        for leverage in LEVERAGES:
            for loss_limit in LOSS_LIMITS:
                for profit_target in PROFIT_TARGETS:
                    config = CalendarConfig(
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
                            split: evaluate_monthly_risk_overlay(
                                _period_returns(values, period),
                                config.risk(
                                    BASE_OVERLAY_TURNOVER_BPS
                                    if cost == "base"
                                    else STRESS_OVERLAY_TURNOVER_BPS
                                ),
                            )
                            for split, period in periods.items()
                        }
                        for cost, values in route["returns"].items()
                    }
                    if _development_eligible(results):
                        eligible.append(
                            {
                                "config": config,
                                "mapping": route["mapping"],
                                "returns": route["returns"],
                                "results": results,
                                "score": _risk_score(results),
                            }
                        )
    return sorted(eligible, key=lambda row: row["score"], reverse=True)


def _confirmation_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    audited = []
    for row in rows:
        config: CalendarConfig = row["config"]
        results = {
            cost: evaluate_monthly_risk_overlay(
                _period_returns(values, CONFIRMATION),
                config.risk(
                    BASE_OVERLAY_TURNOVER_BPS
                    if cost == "base"
                    else STRESS_OVERLAY_TURNOVER_BPS
                ),
            )
            for cost, values in row["returns"].items()
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
        "strategy": "development-selected calendar-month BTC/ETH daily MACD router",
        "data": {
            "btc_first_bar": _timestamp(loaded["btc_perp"][0][0].start_ms),
            "btc_last_bar": _timestamp(loaded["btc_perp"][0][-1].end_ms),
        },
        "protocol": {
            "mapping_discovery": _period_payload(DISCOVERY),
            "configuration_validation_2024": _period_payload(VALIDATION_2024),
            "configuration_validation_2025": _period_payload(VALIDATION_2025),
            "confirmation": _period_payload(CONFIRMATION),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "candidate_count": len(candidates),
            "mapping_rule": "fixed month-of-year mapping selected only from 2021-2023",
        },
        "search": {
            "development_route_count": len(routes),
            "development_risk_eligible_count": len(eligible),
        },
        "selection": {
            "development_selected": _audit_payload(audit["development_selected"]),
        },
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
                "reused_confirmation_candidate"
                if selected_strict
                else "rejected_no_development_selected_strict_solution"
            ),
            "approved_for_trading": False,
            "reason": (
                "The development-selected calendar router reached 7/7 in reused confirmation; "
                "fresh forward evidence remains required."
                if selected_strict
                else "The calendar router selected without 2026 data did not reach +15% in all "
                "seven complete months under both cost models."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "Each calendar month has only three discovery-year observations.",
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
        "mapping": {f"{month:02d}": list(ids) for month, ids in row["mapping"].items()},
        "counts": item["counts"],
        "development": {
            cost: {split: _result_payload(result) for split, result in values.items()}
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
        "Development-only calendar-month routing of daily BTC/ETH MACD sleeves.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        f"- Development route variants: `{payload['search']['development_route_count']}`.",
        f"- Development-risk-eligible controls: "
        f"`{payload['search']['development_risk_eligible_count']}`.",
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
        "# Calendar-Month Router\n\n"
        "Month-of-year mappings use 2021-2023 only; configurations use 2024 and 2025. "
        "The 2026 interval is confirmation only.\n\n"
        f"Decision: `{payload['decision']['status']}`; trading approval: `false`.\n"
    )


def _config_line(row: dict[str, Any]) -> str:
    config = row["config"]
    return (
        f"`{config['id']}`; state/trend `{config['state_weight']:.0%}/"
        f"{config['trend_weight']:.0%}`; leverage `{config['leverage']:.2f}x`; "
        f"locks `{config['monthly_loss_limit']:.0%}/{config['monthly_profit_target']:.0%}`."
    )


def _monthly_table(row: dict[str, Any]) -> list[str]:
    base = row["confirmation"]["base"]["monthly_returns"]
    stress = {
        item["label"]: item["return"]
        for item in row["confirmation"]["stress"]["monthly_returns"]
    }
    return [
        "| Month | Base | Stress |",
        "|---|---:|---:|",
        *(
            f"| {item['label']} | {item['return']:.2%} | {stress[item['label']]:.2%} |"
            for item in base
        ),
    ]


if __name__ == "__main__":
    main()
