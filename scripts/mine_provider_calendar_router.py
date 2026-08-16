#!/usr/bin/env python3
"""Select frozen calendar and volatility-guard providers by prior-year month results."""

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

from mine_calendar_month_router import (  # noqa: E402
    LEVERAGES,
    LOSS_LIMITS,
    PROFIT_TARGETS,
    _monthly_table,
)
from mine_conditional_calendar_complement import _baseline_contexts  # noqa: E402
from mine_defensive_factor_portfolio import _development_eligible, _strict_count  # noqa: E402
from mine_expanding_calendar_router import (  # noqa: E402
    FINAL_TRAIN_END_YEAR,
    VALIDATION_YEARS,
    _training_years,
)
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
from mine_volatility_order_flow_router import (  # noqa: E402
    _daily_realized_volatility,
    _prior_day_volatility_regimes,
)
from mine_walkforward_volatility_guard import _route_returns  # noqa: E402

from mastermind_tick.bar_research import ResearchBar, aggregate_bars  # noqa: E402
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import (  # noqa: E402
    MonthlyRiskConfig,
    evaluate_monthly_risk_overlay,
)
from mastermind_tick.factor_portfolio import DailyReturns, monthly_returns  # noqa: E402

PROVIDERS = ("calendar", "volatility_guard")
PROVIDER_SCORINGS = ("mean", "worst", "hit")
PROVIDER_LOOKBACK_YEARS = (2, 3, 5)
VOLATILITY_LOOKBACK_DAYS = 20
VOLATILITY_CALIBRATION_DAYS = 252
VOLATILITY_QUANTILE = Decimal("0.5")
CALM_TREND_WEIGHT = Decimal("0.5")
VOLATILE_TREND_WEIGHT = Decimal("0")
VOLATILITY_TREND_ID = "btc_perp-macd-1440m-12-36-14-long_only-confirm1"


@dataclass(frozen=True)
class ProviderCalendarConfig:
    scoring: str
    lookback_years: int
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"provider-calendar-{self.scoring}-years{self.lookback_years}-"
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
        default=Path("reports/experiments/provider_calendar_router/2026-08-15"),
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
    contexts, _candidate_returns = _baseline_contexts(state, rows)
    provider_curves = _provider_curves(loaded, state, rows, contexts)
    routes = _route_search(provider_curves)
    print(f"provider-calendar routes: {len(routes)}", flush=True)
    eligible = _risk_search(routes)
    print(f"provider-calendar-risk-eligible configurations: {len(eligible)}", flush=True)
    audit = _confirmation_audit(eligible)
    payload = _report(loaded, candidates, routes, eligible, audit)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"provider-calendar-router-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    (args.output_dir / f"{report_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / f"{report_id}.md").write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)


def _year_slice(rows: DailyReturns, year: int) -> DailyReturns:
    return tuple((label, value) for label, value in rows if label.startswith(f"{year}-"))


def _provider_curves(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    state: dict[str, DailyReturns],
    rows: list[dict[str, Any]],
    contexts: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    trend = next(row for row in rows if row["candidate"].id == VOLATILITY_TREND_ID)
    volatility = _daily_realized_volatility(aggregate_bars(loaded["btc_perp"][0], 240))
    regimes = _prior_day_volatility_regimes(
        volatility,
        VOLATILITY_LOOKBACK_DAYS,
        VOLATILITY_CALIBRATION_DAYS,
        VOLATILITY_QUANTILE,
    )
    def volatility_guard(cost: str, year: int) -> DailyReturns:
        state_year = _year_slice(state[cost], year)
        return _route_returns(
            state_year,
            _year_slice(trend["returns"][cost], year),
            {label: regimes[label] for label, _value in state_year if label in regimes},
            CALM_TREND_WEIGHT,
            VOLATILE_TREND_WEIGHT,
            BASE_OVERLAY_TURNOVER_BPS
            if cost == "base"
            else STRESS_OVERLAY_TURNOVER_BPS,
        )

    provider_curves = {}
    for evaluation_year, context in contexts.items():
        provider_curves[evaluation_year] = {
            "history": {
                cost: {
                    year: {
                        "calendar": calendar_returns,
                        "volatility_guard": volatility_guard(cost, year),
                    }
                    for year, calendar_returns in context["history"][cost].items()
                }
                for cost in ("base", "stress")
            },
            "evaluation": {
                cost: {
                    "calendar": context["evaluation"][cost],
                    "volatility_guard": volatility_guard(cost, evaluation_year),
                }
                for cost in ("base", "stress")
            },
        }
    return provider_curves


def _provider_score(
    history: dict[str, dict[int, dict[str, DailyReturns]]],
    provider: str,
    month: int,
    years: tuple[int, ...],
    scoring: str,
) -> tuple[Decimal, ...] | None:
    values = []
    for cost in ("base", "stress"):
        for year in years:
            monthly = dict(monthly_returns(history[cost][year][provider]))
            label = f"{year}-{month:02d}"
            if label not in monthly:
                return None
            values.append(monthly[label])
    average = sum(values, Decimal("0")) / Decimal(len(values))
    worst = min(values)
    hit = Decimal(sum(value > 0 for value in values)) / Decimal(len(values))
    if scoring == "mean":
        return average, worst, hit
    if scoring == "worst":
        return worst, average, hit
    if scoring == "hit":
        return hit, worst, average
    raise ValueError(f"unsupported provider scoring: {scoring}")


def _provider_mapping(
    history: dict[str, dict[int, dict[str, DailyReturns]]],
    evaluation_year: int,
    lookback_years: int,
    scoring: str,
) -> dict[int, str]:
    years = _training_years(evaluation_year, lookback_years)
    mapping = {}
    for month in range(1, 13):
        ranked = [
            (score, provider)
            for provider in PROVIDERS
            if (score := _provider_score(history, provider, month, years, scoring)) is not None
        ]
        if not ranked:
            raise RuntimeError(f"no provider score for year {evaluation_year}, month {month}")
        mapping[month] = max(ranked)[1]
    return mapping


def _selected_provider_returns(
    providers: dict[str, DailyReturns],
    mapping: dict[int, str],
    turnover_bps: Decimal,
) -> DailyReturns:
    by_provider = {name: dict(values) for name, values in providers.items()}
    labels = sorted(set.intersection(*(set(values) for values in by_provider.values())))
    previous = ""
    rate = turnover_bps / Decimal("10000")
    result = []
    for index, label in enumerate(labels):
        provider = mapping[int(label[5:7])]
        value = by_provider[provider][label]
        if index == 0:
            value -= rate
        elif provider != previous:
            value -= rate
        result.append((label, value))
        previous = provider
    return tuple(result)


def _route_search(
    curves: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    routes = []
    for scoring in PROVIDER_SCORINGS:
        for lookback_years in PROVIDER_LOOKBACK_YEARS:
            mappings = {
                year: _provider_mapping(
                    curves[year]["history"], year, lookback_years, scoring
                )
                for year in curves
            }
            development = {
                cost: {
                    year: _selected_provider_returns(
                        curves[year]["evaluation"][cost],
                        mappings[year],
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
                    str(year): _unlocked_result(values) for year, values in yearly.items()
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
                cost: _selected_provider_returns(
                    curves[final_year]["evaluation"][cost],
                    mappings[final_year],
                    BASE_OVERLAY_TURNOVER_BPS
                    if cost == "base"
                    else STRESS_OVERLAY_TURNOVER_BPS,
                )
                for cost in ("base", "stress")
            }
            routes.append(
                {
                    "route": (scoring, lookback_years),
                    "mappings": mappings,
                    "development": development,
                    "confirmation_returns": confirmation_returns,
                }
            )
    return routes


def _risk_search(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = []
    for route in routes:
        scoring, lookback_years = route["route"]
        for leverage in LEVERAGES:
            for loss_limit in LOSS_LIMITS:
                for profit_target in PROFIT_TARGETS:
                    config = ProviderCalendarConfig(
                        scoring, lookback_years, leverage, loss_limit, profit_target
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
        config: ProviderCalendarConfig = row["config"]
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
        "strategy": (
            "prior-year calendar selection between frozen calendar and volatility providers"
        ),
        "data": {
            "btc_first_bar": _timestamp(loaded["btc_perp"][0][0].start_ms),
            "btc_last_bar": _timestamp(loaded["btc_perp"][0][-1].end_ms),
        },
        "protocol": {
            "providers": {
                "calendar": "expanding long-only calendar, mean/3y/top3/state50%",
                "volatility_guard": (
                    "BTC daily MACD 12/36/14 with prior-day 20d/252d/q50 volatility guard"
                ),
            },
            "validation_years": list(VALIDATION_YEARS),
            "selection_rule": "choose one provider per month from prior-year same-month returns",
            "confirmation": _period_payload(CONFIRMATION),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "family_extension_after_confirmation_review": True,
            "candidate_count": len(candidates),
        },
        "search": {
            "provider_route_count": len(routes),
            "provider_risk_eligible_count": len(eligible),
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
                "The development-selected provider router reached 7/7 after a provider-family "
                "extension inspired by prior confirmation review; fresh evidence is required."
                if selected_strict
                else "No provider router selected without 2026 parameters reached +15% in all "
                "seven complete months under both cost models."
            ),
        },
        "limitations": [
            "The provider family was proposed after viewing reused 2026 confirmation.",
            "Both provider definitions were selected in earlier development research.",
            "Early validation years have only two or three same-month observations.",
            "Daily-close drawdown omits intraday liquidation and borrowing costs.",
        ],
    }


def _audit_payload(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    row = item["row"]
    final_mapping = row["mappings"][FINAL_TRAIN_END_YEAR + 1]
    return {
        "config": row["config"].as_dict(),
        "final_provider_mapping": {
            f"{month:02d}": provider for month, provider in final_mapping.items()
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


def _config_line(row: dict[str, Any]) -> str:
    config = row["config"]
    return (
        f"`{config['id']}`; leverage `{config['leverage']:.2f}x`; "
        f"locks `{config['monthly_loss_limit']:.0%}/{config['monthly_profit_target']:.0%}`."
    )


def _markdown(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    selected = payload["selection"]["development_selected"]
    best = audit["best_confirmation_diagnostic"]
    lines = [
        f"# {payload['id']}",
        "",
        "Prior-year month selection between frozen calendar and volatility-guard providers.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        f"- Development route variants: `{payload['search']['provider_route_count']}`.",
        f"- Development-risk-eligible controls: "
        f"`{payload['search']['provider_risk_eligible_count']}`.",
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
        "# Provider Calendar Router\n\n"
        "One frozen provider is selected per month using only same-month results in earlier years. "
        "The 2026 interval is confirmation only.\n\n"
        f"Decision: `{payload['decision']['status']}`; trading approval: `false`.\n"
    )


if __name__ == "__main__":
    main()
