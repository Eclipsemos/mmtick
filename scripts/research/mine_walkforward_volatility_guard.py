#!/usr/bin/env python3
"""Search volatility-guarded daily MACD routes with a pre-2026 walk-forward protocol."""

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

from mine_defensive_factor_portfolio import (  # noqa: E402
    _development_eligible,
    _period_returns,
    _strict_count,
)
from mine_factor_portfolio import CONFIRMATION, DISCOVERY, VALIDATION  # noqa: E402
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

from mastermind_tick.bar_research import ResearchBar, aggregate_bars  # noqa: E402
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import (  # noqa: E402
    MonthlyRiskConfig,
    evaluate_monthly_risk_overlay,
)
from mastermind_tick.factor_portfolio import DailyReturns, PortfolioResult  # noqa: E402

TRAIN_PERIOD = DISCOVERY
VALIDATION_PERIOD = VALIDATION
CONFIRMATION_PERIOD = CONFIRMATION
LOOKBACKS = (3, 5, 10, 20)
CALIBRATIONS = (60, 120, 252)
QUANTILES = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75"))
CALM_WEIGHTS = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75", "1"))
VOLATILE_WEIGHTS = (Decimal("0"), Decimal("0.25"))
LEVERAGES = tuple(Decimal(value) for value in ("1", "1.5", "2", "3", "4", "5", "6", "8"))
LOSS_LIMITS = tuple(Decimal(value) for value in ("0.10", "0.15", "0.20", "0.25"))
PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.16", "0.18"))
RAW_SHORTLIST_SIZE = 120


@dataclass(frozen=True)
class WalkForwardConfig:
    candidate_id: str
    lookback: int
    calibration: int
    quantile: Decimal
    calm_weight: Decimal
    volatile_weight: Decimal
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"walkforward-{self.candidate_id}-look{self.lookback}-cal{self.calibration}-"
            f"q{self.quantile}-calm{self.calm_weight}-volatile{self.volatile_weight}-"
            f"lev{self.leverage}-loss{self.loss_limit}-profit{self.profit_target}"
        )

    def risk(self, turnover_bps: Decimal) -> MonthlyRiskConfig:
        return MonthlyRiskConfig(self.leverage, self.loss_limit, self.profit_target, turnover_bps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "lookback_days": self.lookback,
            "calibration_days": self.calibration,
            "quantile": float(self.quantile),
            "calm_trend_weight": float(self.calm_weight),
            "calm_state_weight": float(Decimal("1") - self.calm_weight),
            "volatile_trend_weight": float(self.volatile_weight),
            "volatile_state_weight": float(Decimal("1") - self.volatile_weight),
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
        default=Path("reports/experiments/walkforward_volatility_guard/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state = _state_curves(loaded, args.metrics_dir)
    volatility = _daily_realized_volatility(aggregate_bars(loaded["btc_perp"][0], 240))
    candidates = [c for c in _macd_candidates(loaded) if c.interval_minutes == 1440]
    print(f"replaying {len(candidates)} daily MACD candidates", flush=True)
    rows = [_evaluate_macd(candidate) for candidate in candidates]
    raw = _raw_search(state, rows, volatility)
    print(f"raw development shortlist: {len(raw)}", flush=True)
    eligible = _risk_search(raw, state, rows, volatility)
    print(f"development-risk-eligible configurations: {len(eligible)}", flush=True)
    audit = _confirmation_audit(eligible)
    payload = _report(loaded, candidates, raw, eligible, audit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"walkforward-volatility-guard-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    (args.output_dir / f"{report_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / f"{report_id}.md").write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)


def _route_returns(
    state: DailyReturns,
    trend: DailyReturns,
    regimes: dict[str, bool],
    calm_weight: Decimal,
    volatile_weight: Decimal,
    turnover_bps: Decimal,
) -> DailyReturns:
    state_by_label = dict(state)
    trend_by_label = dict(trend)
    labels = sorted(set(state_by_label) & set(trend_by_label) & set(regimes))
    previous = Decimal("0")
    rate = turnover_bps / Decimal("10000")
    return tuple(
        (
            label,
            (Decimal("1") - (weight := (calm_weight if regimes[label] else volatile_weight)))
            * state_by_label[label]
            + weight * trend_by_label[label]
            - abs(weight - previous) * rate,
            previous := weight,
        )[0:2]
        for label in labels
    )


def _raw_eligible(results: dict[str, dict[str, PortfolioResult]]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and not result.bankrupt
        for costs in results.values()
        for result in costs.values()
    )


def _raw_search(
    state: dict[str, DailyReturns],
    rows: list[dict[str, Any]],
    volatility: DailyReturns,
) -> list[dict[str, Any]]:
    raw = []
    periods = {"train": TRAIN_PERIOD, "validation": VALIDATION_PERIOD}
    for row in rows:
        for lookback in LOOKBACKS:
            for calibration in CALIBRATIONS:
                for quantile in QUANTILES:
                    regimes = _prior_day_volatility_regimes(
                        volatility, lookback, calibration, quantile
                    )
                    for calm_weight in CALM_WEIGHTS:
                        for volatile_weight in VOLATILE_WEIGHTS:
                            if volatile_weight >= calm_weight:
                                continue
                            curves = {
                                cost: _route_returns(
                                    state[cost],
                                    row["returns"][cost],
                                    regimes,
                                    calm_weight,
                                    volatile_weight,
                                    BASE_OVERLAY_TURNOVER_BPS
                                    if cost == "base"
                                    else STRESS_OVERLAY_TURNOVER_BPS,
                                )
                                for cost in ("base", "stress")
                            }
                            results = {
                                cost: {
                                    split: _unlocked_result(_period_returns(values, period))
                                    for split, period in periods.items()
                                }
                                for cost, values in curves.items()
                            }
                            if _raw_eligible(results):
                                raw.append(
                                    {
                                        "candidate": row["candidate"],
                                        "returns": curves,
                                        "params": (
                                            lookback,
                                            calibration,
                                            quantile,
                                            calm_weight,
                                            volatile_weight,
                                        ),
                                        "results": results,
                                        "score": _raw_score(results),
                                    }
                                )
    return sorted(raw, key=lambda row: row["score"], reverse=True)[:RAW_SHORTLIST_SIZE]


def _raw_score(results: dict[str, dict[str, PortfolioResult]]) -> tuple[Decimal, ...]:
    values = tuple(result for costs in results.values() for result in costs.values())
    return (
        min(result.positive_month_rate for result in values),
        min(result.worst_month for result in values),
        min(result.net_return for result in values),
        min(result.max_drawdown for result in values),
    )


def _risk_search(
    raw: list[dict[str, Any]],
    state: dict[str, DailyReturns],
    rows: list[dict[str, Any]],
    volatility: DailyReturns,
) -> list[dict[str, Any]]:
    del state, rows, volatility
    eligible = []
    periods = {"train": TRAIN_PERIOD, "validation": VALIDATION_PERIOD}
    for item in raw:
        candidate_id = item["candidate"].id
        lookback, calibration, quantile, calm_weight, volatile_weight = item["params"]
        for leverage in LEVERAGES:
            for loss_limit in LOSS_LIMITS:
                for profit_target in PROFIT_TARGETS:
                    config = WalkForwardConfig(
                        candidate_id,
                        lookback,
                        calibration,
                        quantile,
                        calm_weight,
                        volatile_weight,
                        leverage,
                        loss_limit,
                        profit_target,
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
                            for split in periods
                        }
                        for cost, values in item["returns"].items()
                    }
                    if _development_eligible(results):
                        eligible.append(
                            {
                                "config": config,
                                "returns": item["returns"],
                                "results": results,
                                "score": _risk_score(results),
                            }
                        )
    return sorted(eligible, key=lambda row: row["score"], reverse=True)


def _confirmation_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    audited = []
    for row in rows:
        config: WalkForwardConfig = row["config"]
        results = {
            cost: evaluate_monthly_risk_overlay(
                _period_returns(values, CONFIRMATION_PERIOD),
                config.risk(
                    BASE_OVERLAY_TURNOVER_BPS if cost == "base" else STRESS_OVERLAY_TURNOVER_BPS
                ),
            )
            for cost, values in row["returns"].items()
        }
        counts = {cost: _strict_count(result) for cost, result in results.items()}
        audited.append({"row": row, "results": results, "counts": counts})
    ranked = sorted(
        audited,
        key=lambda row: (
            min(row["counts"].values()),
            sum(row["counts"].values()),
            row["row"]["score"],
        ),
        reverse=True,
    )
    return {
        "configuration_count": len(audited),
        "strict_pass_count": sum(row["counts"] == {"base": 7, "stress": 7} for row in ranked),
        "development_selected": audited[0] if audited else None,
        "best_confirmation": ranked[0] if ranked else None,
    }


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    candidates: list[Any],
    raw: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    audit: dict[str, Any],
) -> dict[str, Any]:
    best = audit["best_confirmation"]
    strict = audit["strict_pass_count"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "walk-forward volatility-guarded daily MACD around frozen state",
        "data": {
            "btc_first_bar": _timestamp(loaded["btc_perp"][0][0].start_ms),
            "btc_last_bar": _timestamp(loaded["btc_perp"][0][-1].end_ms),
        },
        "protocol": {
            "train": _period_payload(TRAIN_PERIOD),
            "validation": _period_payload(VALIDATION_PERIOD),
            "confirmation": _period_payload(CONFIRMATION_PERIOD),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "candidate_count": len(candidates),
            "selection_window": "2021-01 through 2025-12 only",
        },
        "search": {
            "raw_development_shortlist_count": len(raw),
            "development_risk_eligible_count": len(eligible),
            "best_development_score": (
                [float(value) for value in eligible[0]["score"]] if eligible else None
            ),
        },
        "selection": {
            "development_selected": _audit_payload(audit["development_selected"]),
        },
        "confirmation_audit": {
            "configuration_count": audit["configuration_count"],
            "strict_pass_count": strict,
            "best_complete_month_count": (min(best["counts"].values()) if best else 0),
            "best_confirmation_diagnostic": _audit_payload(best),
        },
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "required_complete_months": 7,
            "achieved_in_reused_confirmation": strict > 0,
        },
        "decision": {
            "status": (
                "rejected_no_walkforward_strict_solution"
                if not strict
                else "reused_confirmation_candidate"
            ),
            "approved_for_trading": False,
            "reason": (
                "A walk-forward development-selected configuration reached 7/7 in reused "
                "confirmation; fresh forward evidence remains required."
                if strict
                else "No configuration selected without 2026 data reached +15% in all seven "
                "complete 2026 months under both cost models."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The frozen state strategy itself was selected in earlier research.",
            "Daily-close drawdown omits intraday liquidation and borrowing costs.",
        ],
    }


def _audit_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "config": row["row"]["config"].as_dict(),
        "counts": row["counts"],
        "development": {
            cost: {split: _result_payload(result) for split, result in values.items()}
            for cost, values in row["row"]["results"].items()
        },
        "confirmation": {cost: _result_payload(result) for cost, result in row["results"].items()},
    }


def _markdown(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    best = audit["best_confirmation_diagnostic"]
    lines = [
        f"# {payload['id']}",
        "",
        "Walk-forward volatility-guarded daily MACD search with no 2026 parameter selection.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        f"- Raw development shortlist: `{payload['search']['raw_development_shortlist_count']}`.",
        f"- Development-risk-eligible controls: "
        f"`{payload['search']['development_risk_eligible_count']}`.",
        f"- Best reused-confirmation coverage: `{audit['best_complete_month_count']}/7`.",
        f"- Strict base-and-stress 7/7 configurations: `{audit['strict_pass_count']}`.",
        "",
        payload["decision"]["reason"],
        "Partial `2026-08` is excluded.",
    ]
    if best:
        lines.extend(["", "## Best Confirmation", "", _config_line(best), ""])
        lines.extend(_monthly_table(best))
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _readme(payload: dict[str, Any]) -> str:
    return (
        "# Walk-Forward Volatility Guard\n\n"
        "Training is 2021-2023, validation is 2024-2025, and 2026 is confirmation only.\n\n"
        f"Decision: `{payload['decision']['status']}`; trading approval: `false`.\n"
    )


def _config_line(row: dict[str, Any]) -> str:
    config = row["config"]
    return (
        f"`{config['candidate_id']}`; volatility `{config['lookback_days']}d/"
        f"{config['calibration_days']}d/q{config['quantile']:.2f}`; calm/volatile trend "
        f"`{config['calm_trend_weight']:.0%}/{config['volatile_trend_weight']:.0%}`; "
        f"leverage `{config['leverage']:.2f}x`; locks "
        f"`{config['monthly_loss_limit']:.0%}/{config['monthly_profit_target']:.0%}`."
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
