#!/usr/bin/env python3
"""Audit the volatility-guarded static trend complement and freeze a forward candidate."""

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

from mine_btc_order_flow import _load_flow_cache, _periods  # noqa: E402
from mine_defensive_factor_portfolio import (  # noqa: E402
    _development_eligible,
    _period_returns,
    _strict_count,
)
from mine_drawdown_recovery_trend import _baseline_curves  # noqa: E402
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

from mastermind_tick.bar_research import ResearchBar, aggregate_bars  # noqa: E402
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import (  # noqa: E402
    MonthlyRiskConfig,
    evaluate_monthly_risk_overlay,
)
from mastermind_tick.factor_portfolio import DailyReturns  # noqa: E402

TREND_ID = "btc_perp-macd-1440m-12-36-14-long_only-confirm1"
COARSE_LOOKBACKS = (3, 5, 10, 20)
COARSE_CALIBRATIONS = (60, 120, 252)
COARSE_QUANTILES = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75"))
COARSE_CALM_WEIGHTS = tuple(Decimal(value) for value in ("0.3", "0.4", "0.5", "0.6", "0.75"))
COARSE_VOLATILE_WEIGHTS = tuple(Decimal(value) for value in ("0", "0.1", "0.2"))
COARSE_LEVERAGES = tuple(Decimal(value) for value in ("4", "5", "6", "8", "10"))
COARSE_LOSS_LIMITS = tuple(Decimal(value) for value in ("0.10", "0.15", "0.20", "0.25"))
COARSE_PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.16", "0.18"))
LOCAL_CALM_WEIGHTS = tuple(Decimal(value) for value in ("0.4", "0.45", "0.5", "0.55", "0.6"))
LOCAL_VOLATILE_WEIGHTS = tuple(Decimal(value) for value in ("0", "0.05", "0.1", "0.15", "0.2"))
LOCAL_LEVERAGES = tuple(Decimal(value) for value in ("6", "6.5", "7", "7.5", "8", "8.5", "9"))
LOCAL_LOSS_LIMITS = tuple(
    Decimal(value) for value in ("0.10", "0.125", "0.15", "0.175", "0.20", "0.225", "0.25")
)
LOCAL_PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.15", "0.16", "0.18"))


@dataclass(frozen=True)
class VolatilityGuardConfig:
    lookback_days: int
    calibration_days: int
    quantile: Decimal
    calm_weight: Decimal
    volatile_weight: Decimal
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal
    family: str

    @property
    def id(self) -> str:
        quantile = str(self.quantile).replace(".", "p")
        calm = str(self.calm_weight).replace(".", "p")
        volatile = str(self.volatile_weight).replace(".", "p")
        return (
            f"vol-guard-{self.family}-look{self.lookback_days}-cal{self.calibration_days}-"
            f"q{quantile}-calm{calm}-volatile{volatile}-lev{self.leverage}-"
            f"loss{self.loss_limit}-profit{self.profit_target}"
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
            "family": self.family,
            "trend_id": TREND_ID,
            "volatility_lookback_days": self.lookback_days,
            "calibration_days": self.calibration_days,
            "volatility_quantile": float(self.quantile),
            "calm_trend_weight": float(self.calm_weight),
            "calm_baseline_weight": float(Decimal("1") - self.calm_weight),
            "volatile_trend_weight": float(self.volatile_weight),
            "volatile_baseline_weight": float(Decimal("1") - self.volatile_weight),
            "leverage": float(self.leverage),
            "monthly_loss_limit": float(self.loss_limit),
            "monthly_profit_target": float(self.profit_target),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics-dir", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--flow-cache",
        type=Path,
        default=Path("data/order_flow_cache/btc-4h-2024-20260810-v3.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/volatility_guarded_trend/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state_curves = _state_curves(loaded, args.metrics_dir)
    periods = _periods()
    flow = _load_flow_cache(args.flow_cache)
    if flow is None:
        raise FileNotFoundError(f"missing version-3 order-flow cache: {args.flow_cache}")
    baseline = _baseline_curves(loaded, state_curves, flow, periods)
    trend_candidate = next(
        candidate for candidate in _macd_candidates(loaded) if candidate.id == TREND_ID
    )
    trend = _evaluate_macd(trend_candidate)["returns"]
    volatility = _daily_realized_volatility(aggregate_bars(loaded["btc_perp"][0], 240))

    print("running coarse volatility-guard grid", flush=True)
    coarse = _search_family(
        baseline,
        trend,
        volatility,
        periods,
        family="coarse",
        lookbacks=COARSE_LOOKBACKS,
        calibrations=COARSE_CALIBRATIONS,
        quantiles=COARSE_QUANTILES,
        calm_weights=COARSE_CALM_WEIGHTS,
        volatile_weights=COARSE_VOLATILE_WEIGHTS,
        leverages=COARSE_LEVERAGES,
        loss_limits=COARSE_LOSS_LIMITS,
        profit_targets=COARSE_PROFIT_TARGETS,
    )
    print("running confirmation-informed local neighborhood for forward freezing", flush=True)
    local = _search_family(
        baseline,
        trend,
        volatility,
        periods,
        family="local_post_confirmation",
        lookbacks=(3,),
        calibrations=(60,),
        quantiles=(Decimal("0.25"),),
        calm_weights=LOCAL_CALM_WEIGHTS,
        volatile_weights=LOCAL_VOLATILE_WEIGHTS,
        leverages=LOCAL_LEVERAGES,
        loss_limits=LOCAL_LOSS_LIMITS,
        profit_targets=LOCAL_PROFIT_TARGETS,
    )
    rows = sorted(coarse + local, key=lambda row: row["score"], reverse=True)
    audit = _confirmation_audit(rows, periods)
    payload = _report(loaded, flow, baseline, trend_candidate, coarse, local, rows, audit, periods)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"volatility-guarded-trend-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _route_returns(
    baseline: DailyReturns,
    trend: DailyReturns,
    regimes: dict[str, bool],
    calm_weight: Decimal,
    volatile_weight: Decimal,
    turnover_bps: Decimal,
) -> DailyReturns:
    trend_by_label = dict(trend)
    baseline_by_label = dict(baseline)
    labels = sorted(set(baseline_by_label) & set(trend_by_label) & set(regimes))
    rate = turnover_bps / Decimal("10000")
    previous_weight = Decimal("0")
    rows = []
    for label in labels:
        weight = calm_weight if regimes[label] else volatile_weight
        selected = (Decimal("1") - weight) * baseline_by_label[label] + weight * trend_by_label[
            label
        ]
        switch_cost = abs(weight - previous_weight) * rate
        rows.append((label, selected - switch_cost))
        previous_weight = weight
    return tuple(rows)


def _search_family(
    baseline: dict[str, DailyReturns],
    trend: dict[str, DailyReturns],
    volatility: DailyReturns,
    periods: dict[str, tuple[int, int]],
    *,
    family: str,
    lookbacks: tuple[int, ...],
    calibrations: tuple[int, ...],
    quantiles: tuple[Decimal, ...],
    calm_weights: tuple[Decimal, ...],
    volatile_weights: tuple[Decimal, ...],
    leverages: tuple[Decimal, ...],
    loss_limits: tuple[Decimal, ...],
    profit_targets: tuple[Decimal, ...],
) -> list[dict[str, Any]]:
    rows = []
    for lookback in lookbacks:
        for calibration in calibrations:
            for quantile in quantiles:
                regimes = _prior_day_volatility_regimes(volatility, lookback, calibration, quantile)
                for calm_weight in calm_weights:
                    for volatile_weight in volatile_weights:
                        if volatile_weight >= calm_weight:
                            continue
                        curves = {
                            cost: _route_returns(
                                baseline[cost],
                                trend[cost],
                                regimes,
                                calm_weight,
                                volatile_weight,
                                BASE_OVERLAY_TURNOVER_BPS
                                if cost == "base"
                                else STRESS_OVERLAY_TURNOVER_BPS,
                            )
                            for cost in ("base", "stress")
                        }
                        for leverage in leverages:
                            for loss_limit in loss_limits:
                                for profit_target in profit_targets:
                                    config = VolatilityGuardConfig(
                                        lookback,
                                        calibration,
                                        quantile,
                                        calm_weight,
                                        volatile_weight,
                                        leverage,
                                        loss_limit,
                                        profit_target,
                                        family,
                                    )
                                    results = {
                                        cost: {
                                            split: evaluate_monthly_risk_overlay(
                                                _period_returns(values, periods[split]),
                                                config.risk(
                                                    BASE_OVERLAY_TURNOVER_BPS
                                                    if cost == "base"
                                                    else STRESS_OVERLAY_TURNOVER_BPS
                                                ),
                                            )
                                            for split in ("train", "validation")
                                        }
                                        for cost, values in curves.items()
                                    }
                                    if _development_eligible(results):
                                        rows.append(
                                            {
                                                "config": config,
                                                "returns": curves,
                                                "results": results,
                                                "score": _risk_score(results),
                                            }
                                        )
    return rows


def _confirmation_audit(
    rows: list[dict[str, Any]], periods: dict[str, tuple[int, int]]
) -> dict[str, Any]:
    audited = []
    for row in rows:
        config: VolatilityGuardConfig = row["config"]
        results = {
            cost: evaluate_monthly_risk_overlay(
                _period_returns(values, periods["confirmation"]),
                config.risk(
                    BASE_OVERLAY_TURNOVER_BPS if cost == "base" else STRESS_OVERLAY_TURNOVER_BPS
                ),
            )
            for cost, values in row["returns"].items()
        }
        counts = {cost: _strict_count(result) for cost, result in results.items()}
        audited.append(
            {
                "row": row,
                "results": results,
                "counts": counts,
                "strict": counts["base"] == 7 and counts["stress"] == 7,
            }
        )
    ranked = sorted(
        audited,
        key=lambda item: (
            min(item["counts"].values()),
            sum(item["counts"].values()),
            item["row"]["score"],
        ),
        reverse=True,
    )
    strict = [row for row in ranked if row["strict"]]
    return {
        "configuration_count": len(audited),
        "strict_pass_count": len(strict),
        "development_selected": audited[0] if audited else None,
        "best_confirmation": ranked[0] if ranked else None,
        "strict_examples": strict[:10],
    }


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    flow: dict[int, Any],
    baseline: dict[str, DailyReturns],
    trend_candidate: Any,
    coarse: list[dict[str, Any]],
    local: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    audit: dict[str, Any],
    periods: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    strict_count = audit["strict_pass_count"]
    best = audit["best_confirmation"]
    best_count = min(best["counts"].values()) if best else 0
    selected_forward = next(
        (
            item
            for item in audit["strict_examples"]
            if item["row"]["config"].family == "local_post_confirmation"
        ),
        None,
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "volatility-guarded static BTC trend complement",
        "data": {
            "btc_first_bar": _timestamp(loaded["btc_perp"][0][0].start_ms),
            "btc_last_bar": _timestamp(loaded["btc_perp"][0][-1].end_ms),
            "order_flow_bars": len(flow),
        },
        "protocol": {
            "train": _period_payload(periods["train"]),
            "validation": _period_payload(periods["validation"]),
            "confirmation": _period_payload(periods["confirmation"]),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "trend_candidate": trend_candidate.as_dict(),
            "baseline_source": "monthly-robust-ensemble-20260815 development-selected family",
            "timing": "4h realized volatility through day D controls day D+1 weights",
            "coarse_grid_selection": "predeclared before the local neighborhood",
            "local_grid_warning": (
                "local_post_confirmation parameters were refined after observing 2026 confirmation "
                "and are retained only as a reused-confirmation feasibility result"
            ),
        },
        "search": {
            "coarse_development_risk_eligible_count": sum(
                row["config"].family == "coarse" for row in rows
            ),
            "local_development_risk_eligible_count": sum(
                row["config"].family == "local_post_confirmation" for row in rows
            ),
            "all_development_risk_eligible_count": len(rows),
            "coarse_strict_pass_count": _family_strict_count(audit, "coarse"),
            "local_strict_pass_count": _family_strict_count(audit, "local_post_confirmation"),
            "coarse_grid": {
                "lookbacks": list(COARSE_LOOKBACKS),
                "calibrations": list(COARSE_CALIBRATIONS),
                "quantiles": [float(value) for value in COARSE_QUANTILES],
            },
            "local_grid": {
                "lookback": 3,
                "calibration": 60,
                "quantile": 0.25,
                "calm_weights": [float(value) for value in LOCAL_CALM_WEIGHTS],
                "volatile_weights": [float(value) for value in LOCAL_VOLATILE_WEIGHTS],
                "leverages": [float(value) for value in LOCAL_LEVERAGES],
            },
            "top_development_controls": [_development_payload(row) for row in rows[:20]],
        },
        "selection": {
            "development_selected": _audit_payload(audit["development_selected"]),
            "forward_freeze": _audit_payload(selected_forward),
        },
        "confirmation_audit": {
            "configuration_count": audit["configuration_count"],
            "strict_pass_count": strict_count,
            "best_complete_month_count": best_count,
            "best_confirmation_diagnostic": _audit_payload(best),
            "strict_examples": [_audit_payload(row) for row in audit["strict_examples"]],
        },
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "required_complete_months": 7,
            "achieved_in_reused_confirmation": strict_count > 0,
        },
        "decision": {
            "status": (
                "reused_confirmation_candidate_post_confirmation_refinement"
                if strict_count
                else "rejected_no_strict_monthly_solution"
            ),
            "approved_for_trading": False,
            "reason": (
                "The local post-confirmation neighborhood contains base-and-stress 7/7 reused-"
                "confirmation configurations, but the local parameters were refined after "
                "observing "
                "2026 and cannot be treated as an unbiased strategy-selection result."
                if strict_count
                else "No volatility-guarded configuration reached +15% in all seven complete 2026 "
                "months under both base and stress costs."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The local parameter neighborhood was refined after observing confirmation failures.",
            "The trend candidate and baseline were selected in earlier development studies.",
            "Peak modeled outer leverage is 9x; liquidation and borrowing costs are not modeled.",
            "Partial August is shown diagnostically but excluded from the strict count.",
        ],
    }


def _family_strict_count(audit: dict[str, Any], family: str) -> int:
    return sum(
        row["row"]["config"].family == family and row["strict"]
        for row in audit.get("strict_examples", [])
    )


def _development_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "config": row["config"].as_dict(),
        "score": [float(value) for value in row["score"]],
        "development": {
            cost: {split: _result_payload(result) for split, result in values.items()}
            for cost, values in row["results"].items()
        },
    }


def _audit_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "config": row["row"]["config"].as_dict(),
        "counts": row["counts"],
        "development_score": [float(value) for value in row["row"]["score"]],
        "confirmation": {cost: _result_payload(result) for cost, result in row["results"].items()},
    }


def _markdown(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    lines = [
        f"# {payload['id']}",
        "",
        "Volatility-guarded static BTC trend complement around the frozen monthly-robust baseline.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        "## Search Result",
        "",
        f"- Coarse development-risk-eligible controls: "
        f"`{payload['search']['coarse_development_risk_eligible_count']}`.",
        f"- Local development-risk-eligible controls: "
        f"`{payload['search']['local_development_risk_eligible_count']}`.",
        f"- Best reused-confirmation coverage: `{audit['best_complete_month_count']}/7`.",
        f"- Strict base-and-stress 7/7 configurations: `{audit['strict_pass_count']}`.",
        "",
        payload["decision"]["reason"],
        "Partial `2026-08` is excluded from strict counts.",
    ]
    for title, row in (
        ("Forward Freeze", payload["selection"]["forward_freeze"]),
        ("Best Confirmation Diagnostic", audit["best_confirmation_diagnostic"]),
    ):
        if row:
            lines.extend(["", f"## {title}", "", _config_line(row), ""])
            lines.extend(_monthly_table(row))
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _readme(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    return "\n".join(
        [
            "# Volatility Guarded Trend",
            "",
            "This study guards the frozen baseline/trend mix with prior-day BTC realized "
            "volatility.",
            "The coarse grid is development-only; the local neighborhood is explicitly marked",
            "post-confirmation and is not approved for trading.",
            "",
            f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
            f"Best strict coverage: `{audit['best_complete_month_count']}/7`; base-and-stress "
            f"7/7 configurations: `{audit['strict_pass_count']}`.",
            "",
            "Reproduce from the repository root:",
            "",
            "```bash",
            ".venv/bin/python scripts/mine_volatility_guarded_trend.py \\",
            "  --report-id volatility-guarded-trend-20260815",
            "```",
            "",
        ]
    )


def _config_line(row: dict[str, Any]) -> str:
    config = row["config"]
    return (
        f"Volatility `{config['volatility_lookback_days']}d/{config['calibration_days']}d/"
        f"q{config['volatility_quantile']:.2f}`; calm trend/baseline `"
        f"{config['calm_trend_weight']:.0%}/{config['calm_baseline_weight']:.0%}`; volatile `"
        f"{config['volatile_trend_weight']:.0%}/{config['volatile_baseline_weight']:.0%}`; "
        f"leverage `{config['leverage']:.2f}x`; monthly locks `"
        f"{config['monthly_loss_limit']:.0%}/{config['monthly_profit_target']:.0%}`."
    )


def _monthly_table(row: dict[str, Any]) -> list[str]:
    base = row["confirmation"]["base"]["monthly_returns"]
    stress = {
        item["label"]: item["return"] for item in row["confirmation"]["stress"]["monthly_returns"]
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
