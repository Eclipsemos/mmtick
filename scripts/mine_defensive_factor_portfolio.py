#!/usr/bin/env python3
"""Search development-selected defensive factors for the strict +15% monthly target."""

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

from mine_factor_portfolio import (  # noqa: E402
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    CONFIRMATION,
    DISCOVERY,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    VALIDATION,
    SleeveCandidate,
    _candidate_library,
    _evaluate_candidate,
    _event_candidate_library,
    _require_aligned_bars,
)
from mine_monthly_target_regime_router import (  # noqa: E402
    ASSETS,
    BASE_OVERLAY_TURNOVER_BPS,
    COMPLETE_CONFIRMATION_END,
    DEVELOPMENT_PERIOD,
    MAX_DEVELOPMENT_DRAWDOWN,
    STRESS_OVERLAY_TURNOVER_BPS,
    TARGET_MONTHLY_RETURN,
    _complete_months,
    _period_payload,
    _state_curves,
    _timestamp,
)

from mastermind_tick.bar_research import ResearchBar, aggregate_bars  # noqa: E402
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import (  # noqa: E402
    MonthlyRiskConfig,
    evaluate_monthly_risk_overlay,
)
from mastermind_tick.factor_portfolio import (  # noqa: E402
    DailyReturns,
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
    monthly_returns,
)

SHORTLIST_SIZE = 120
MIN_CONDITIONAL_HIT_RATE = Decimal("0.4")
MIN_DEVELOPMENT_TARGET_RATE = Decimal("0.15")
STATE_WEIGHTS = tuple(Decimal(value) for value in ("0.5", "0.65", "0.75", "0.85", "0.9"))
LEVERAGES = tuple(Decimal(value) for value in ("1.5", "2", "2.5", "3", "4", "5", "6"))
LOSS_LIMITS = tuple(Decimal(value) for value in ("0.10", "0.15", "0.20"))
PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.16", "0.18"))


@dataclass(frozen=True)
class DefensiveConfig:
    candidate_id: str
    state_weight: Decimal
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"defensive-{self.candidate_id}-state{self.state_weight}-lev{self.leverage}-"
            f"loss{self.loss_limit}-profit{self.profit_target}"
        )

    def risk(self, turnover_bps: Decimal) -> MonthlyRiskConfig:
        return MonthlyRiskConfig(
            leverage=self.leverage,
            loss_limit=self.loss_limit,
            profit_target=self.profit_target,
            turnover_bps=turnover_bps,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "state_weight": float(self.state_weight),
            "factor_weight": float(Decimal("1") - self.state_weight),
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
        default=Path("reports/experiments/defensive_factor_portfolio/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    print("loading frozen state baseline and BTC/ETH factor inputs", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state_curves = _state_curves(loaded, args.metrics_dir)
    candidates = _candidates(loaded)

    print(f"screening {len(candidates):,} factors on development loss months", flush=True)
    screening = _screen_candidates(candidates, state_curves["base"])
    shortlist = screening[:SHORTLIST_SIZE]
    print(f"replaying {len(shortlist)} defensive factors under base and stress costs", flush=True)
    replays = _replay_shortlist(shortlist)

    print("searching weights, leverage, and monthly locks on development only", flush=True)
    eligible = _search_development(state_curves, replays)
    print(f"auditing {len(eligible):,} development-eligible configurations", flush=True)
    audit = _confirmation_audit(state_curves, replays, eligible)

    payload = _report(loaded, candidates, screening, shortlist, eligible, audit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"defensive-factor-portfolio-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _candidates(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
) -> list[SleeveCandidate]:
    btc_4h = aggregate_bars(loaded["btc_perp"][0], 240)
    eth_4h = aggregate_bars(loaded["eth_perp"][0], 240)
    _require_aligned_bars(btc_4h, eth_4h)
    return [
        *_candidate_library("btc_perp", *loaded["btc_perp"]),
        *_candidate_library("eth_perp", *loaded["eth_perp"]),
        *_event_candidate_library(
            btc_4h,
            eth_4h,
            loaded["btc_perp"][1],
            loaded["eth_perp"][1],
        ),
    ]


def _screen_candidates(
    candidates: list[SleeveCandidate], state_returns: DailyReturns
) -> list[dict[str, Any]]:
    state_monthly = {
        name: dict(monthly_returns(_period_returns(state_returns, period)))
        for name, period in (("discovery", DISCOVERY), ("validation", VALIDATION))
    }
    rows = []
    for index, candidate in enumerate(candidates, start=1):
        replay = _evaluate_candidate(candidate, DEVELOPMENT_PERIOD)
        factor_returns = decimal_returns(replay.daily_returns)
        conditional = {
            name: _conditional_summary(
                state_monthly[name],
                dict(monthly_returns(_period_returns(factor_returns, period))),
            )
            for name, period in (("discovery", DISCOVERY), ("validation", VALIDATION))
        }
        if all(
            result["average_return"] > 0 and result["positive_rate"] >= MIN_CONDITIONAL_HIT_RATE
            for result in conditional.values()
        ):
            rows.append(
                {
                    "candidate": candidate,
                    "conditional": conditional,
                    "score": _conditional_score(conditional),
                }
            )
        if index % 200 == 0:
            print(f"screen {index}/{len(candidates)}; eligible={len(rows)}", flush=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _conditional_summary(
    state_monthly: dict[str, Decimal], factor_monthly: dict[str, Decimal]
) -> dict[str, Decimal | int]:
    labels = tuple(label for label, value in state_monthly.items() if value < 0)
    if not labels or any(label not in factor_monthly for label in labels):
        return {
            "month_count": 0,
            "positive_rate": Decimal("0"),
            "average_return": Decimal("0"),
            "worst_return": Decimal("0"),
        }
    values = tuple(factor_monthly[label] for label in labels)
    return {
        "month_count": len(values),
        "positive_rate": Decimal(sum(value > 0 for value in values)) / Decimal(len(values)),
        "average_return": sum(values, Decimal("0")) / Decimal(len(values)),
        "worst_return": min(values),
    }


def _conditional_score(
    summaries: dict[str, dict[str, Decimal | int]],
) -> tuple[Decimal, ...]:
    discovery = summaries["discovery"]
    validation = summaries["validation"]
    return (
        min(Decimal(discovery["positive_rate"]), Decimal(validation["positive_rate"])),
        min(Decimal(discovery["average_return"]), Decimal(validation["average_return"])),
        Decimal(discovery["average_return"]) + Decimal(validation["average_return"]),
        min(Decimal(discovery["worst_return"]), Decimal(validation["worst_return"])),
    )


def _replay_shortlist(shortlist: list[dict[str, Any]]) -> dict[str, dict[str, DailyReturns]]:
    result = {}
    for index, row in enumerate(shortlist, start=1):
        candidate = row["candidate"]
        curves = {}
        for cost_name, fee, slippage in (
            ("base", BASE_FEE_BPS, BASE_SLIPPAGE_BPS),
            ("stress", STRESS_FEE_BPS, STRESS_SLIPPAGE_BPS),
        ):
            period_returns = []
            for period in (DEVELOPMENT_PERIOD, CONFIRMATION):
                replay = _evaluate_candidate(
                    candidate,
                    period,
                    fee_bps=fee,
                    slippage_bps=slippage,
                )
                period_returns.extend(decimal_returns(replay.daily_returns))
            curves[cost_name] = tuple(period_returns)
        result[candidate.id] = curves
        if index % 20 == 0:
            print(f"replay {index}/{len(shortlist)}", flush=True)
    return result


def _search_development(
    state_curves: dict[str, DailyReturns],
    replays: dict[str, dict[str, DailyReturns]],
) -> list[dict[str, Any]]:
    rows = []
    for index, (candidate_id, curves) in enumerate(replays.items(), start=1):
        for state_weight in STATE_WEIGHTS:
            composites = {
                cost_name: {
                    split: _composite_returns(
                        state_curves[cost_name],
                        curves[cost_name],
                        state_weight,
                        period,
                    )
                    for split, period in (
                        ("discovery", DISCOVERY),
                        ("validation", VALIDATION),
                    )
                }
                for cost_name in ("base", "stress")
            }
            for leverage in LEVERAGES:
                for loss_limit in LOSS_LIMITS:
                    for profit_target in PROFIT_TARGETS:
                        config = DefensiveConfig(
                            candidate_id,
                            state_weight,
                            leverage,
                            loss_limit,
                            profit_target,
                        )
                        results = {
                            cost_name: {
                                split: evaluate_monthly_risk_overlay(
                                    returns,
                                    config.risk(
                                        BASE_OVERLAY_TURNOVER_BPS
                                        if cost_name == "base"
                                        else STRESS_OVERLAY_TURNOVER_BPS
                                    ),
                                )
                                for split, returns in split_curves.items()
                            }
                            for cost_name, split_curves in composites.items()
                        }
                        if _development_eligible(results):
                            rows.append(
                                {
                                    "config": config,
                                    "results": results,
                                    "score": _development_score(results),
                                }
                            )
        if index % 10 == 0:
            print(f"config search {index}/{len(replays)}; eligible={len(rows)}", flush=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _composite_returns(
    state_returns: DailyReturns,
    factor_returns: DailyReturns,
    state_weight: Decimal,
    period: tuple[int, int],
) -> DailyReturns:
    state = _period_returns(state_returns, period)
    factor = _period_returns(factor_returns, period)
    return evaluate_static_portfolio(
        {"state": state, "defense": factor},
        {"state": state_weight, "defense": Decimal("1") - state_weight},
    ).daily_returns


def _period_returns(rows: DailyReturns, period: tuple[int, int]) -> DailyReturns:
    values = _period_payload(period)
    return tuple(
        (label, value) for label, value in rows if values["start"] <= label <= values["end"]
    )


def _development_eligible(results: dict[str, dict[str, PortfolioResult]]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= MAX_DEVELOPMENT_DRAWDOWN
        and result.positive_month_rate >= Decimal("0.5")
        and _target_rate(result) >= MIN_DEVELOPMENT_TARGET_RATE
        and not result.bankrupt
        for cost_results in results.values()
        for result in cost_results.values()
    )


def _development_score(
    results: dict[str, dict[str, PortfolioResult]],
) -> tuple[Decimal, ...]:
    values = tuple(result for costs in results.values() for result in costs.values())
    return (
        min(_target_rate(result) for result in values),
        sum((_target_rate(result) for result in values), Decimal("0")),
        min(result.positive_month_rate for result in values),
        min(result.worst_month for result in values),
        min(result.net_return for result in values),
        min(result.max_drawdown for result in values),
    )


def _confirmation_audit(
    state_curves: dict[str, DailyReturns],
    replays: dict[str, dict[str, DailyReturns]],
    eligible: list[dict[str, Any]],
) -> dict[str, Any]:
    audited = []
    composites: dict[tuple[str, str, Decimal], DailyReturns] = {}
    for row in eligible:
        config: DefensiveConfig = row["config"]
        results = {}
        for cost_name in ("base", "stress"):
            key = (cost_name, config.candidate_id, config.state_weight)
            if key not in composites:
                composites[key] = _composite_returns(
                    state_curves[cost_name],
                    replays[config.candidate_id][cost_name],
                    config.state_weight,
                    CONFIRMATION,
                )
            results[cost_name] = evaluate_monthly_risk_overlay(
                composites[key],
                config.risk(
                    BASE_OVERLAY_TURNOVER_BPS
                    if cost_name == "base"
                    else STRESS_OVERLAY_TURNOVER_BPS
                ),
            )
        counts = {name: _strict_count(result) for name, result in results.items()}
        audited.append(
            {
                "row": row,
                "results": results,
                "counts": counts,
                "strict": counts["base"] == 7 and counts["stress"] == 7,
            }
        )
    confirmation_ranked = sorted(
        audited,
        key=lambda item: (
            min(item["counts"].values()),
            sum(item["counts"].values()),
            item["row"]["score"],
        ),
        reverse=True,
    )
    strict = [row for row in confirmation_ranked if row["strict"]]
    return {
        "configuration_count": len(audited),
        "strict_pass_count": len(strict),
        "development_selected": audited[0] if audited else None,
        "best_confirmation": confirmation_ranked[0] if confirmation_ranked else None,
        "strict_examples": strict[:10],
    }


def _strict_count(result: PortfolioResult) -> int:
    return sum(
        value >= TARGET_MONTHLY_RETURN for _label, value in _complete_months(result.monthly_returns)
    )


def _target_rate(result: PortfolioResult) -> Decimal:
    if not result.monthly_returns:
        return Decimal("0")
    return Decimal(
        sum(value >= TARGET_MONTHLY_RETURN for _label, value in result.monthly_returns)
    ) / Decimal(len(result.monthly_returns))


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    candidates: list[SleeveCandidate],
    screening: list[dict[str, Any]],
    shortlist: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    audit: dict[str, Any],
) -> dict[str, Any]:
    strict_count = audit["strict_pass_count"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "development-selected defensive factor with causal monthly risk locks",
        "data": {
            asset: {
                "first_bar": _timestamp(values[0][0].start_ms),
                "last_bar": _timestamp(values[0][-1].end_ms),
            }
            for asset, values in loaded.items()
        },
        "protocol": {
            "discovery": _period_payload(DISCOVERY),
            "validation": _period_payload(VALIDATION),
            "confirmation": _period_payload(CONFIRMATION),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "factor_selection": (
                "rank only by factor returns during frozen-baseline negative months in both "
                "development splits"
            ),
        },
        "search": {
            "candidate_count": len(candidates),
            "conditional_eligible_count": len(screening),
            "shortlist_size": len(shortlist),
            "configuration_grid_per_factor": (
                len(STATE_WEIGHTS) * len(LEVERAGES) * len(LOSS_LIMITS) * len(PROFIT_TARGETS)
            ),
            "development_risk_eligible_count": len(eligible),
            "state_weights": [float(value) for value in STATE_WEIGHTS],
            "leverages": [float(value) for value in LEVERAGES],
            "monthly_loss_limits": [float(value) for value in LOSS_LIMITS],
            "monthly_profit_targets": [float(value) for value in PROFIT_TARGETS],
            "top_conditional_factors": [
                {
                    "candidate": _candidate_payload(row["candidate"]),
                    "conditional": _conditional_payload(row["conditional"]),
                }
                for row in shortlist[:20]
            ],
        },
        "selection": {
            "development_selected": _audited_payload(audit["development_selected"]),
            "top_development_configurations": [_development_payload(row) for row in eligible[:20]],
        },
        "confirmation_audit": {
            "configuration_count": audit["configuration_count"],
            "strict_pass_count": strict_count,
            "best_complete_month_count": (
                min(audit["best_confirmation"]["counts"].values())
                if audit["best_confirmation"]
                else 0
            ),
            "best_confirmation_diagnostic": _audited_payload(audit["best_confirmation"]),
            "strict_examples": [_audited_payload(row) for row in audit["strict_examples"]],
        },
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "required_complete_months": 7,
            "achieved_in_reused_confirmation": strict_count > 0,
        },
        "decision": {
            "status": (
                "reused_confirmation_candidate"
                if strict_count
                else "rejected_no_strict_monthly_solution"
            ),
            "approved_for_trading": False,
            "reason": (
                "At least one development-selected configuration reached +15% in all seven "
                "complete reused-confirmation months under base and stress costs; fresh forward "
                "evidence is still required."
                if strict_count
                else "No development-selected defensive factor configuration reached +15% in "
                "all seven complete 2026 months under both base and stress costs."
            ),
        },
        "costs": {
            "base_component_fee_bps": float(BASE_FEE_BPS),
            "base_component_slippage_bps": float(BASE_SLIPPAGE_BPS),
            "base_overlay_turnover_bps": float(BASE_OVERLAY_TURNOVER_BPS),
            "stress_component_fee_bps": float(STRESS_FEE_BPS),
            "stress_component_slippage_bps": float(STRESS_SLIPPAGE_BPS),
            "stress_overlay_turnover_bps": float(STRESS_OVERLAY_TURNOVER_BPS),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The frozen market-state baseline was selected in prior overlapping research.",
            "Conditional factor screening uses only 2021-2025 baseline loss months.",
            "Monthly locks react after a daily close and pay turnover on the next exposure change.",
            "Drawdown is measured at daily closes; liquidation and borrowing costs are not "
            "modeled.",
        ],
    }


def _candidate_payload(candidate: SleeveCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "instrument_id": candidate.instrument_id,
        "family": candidate.family,
        "interval_minutes": candidate.interval_minutes,
        "parameters": candidate.parameters,
    }


def _conditional_payload(
    values: dict[str, dict[str, Decimal | int]],
) -> dict[str, dict[str, float | int]]:
    return {
        split: {
            name: int(value) if name == "month_count" else float(value)
            for name, value in summary.items()
        }
        for split, summary in values.items()
    }


def _development_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "config": row["config"].as_dict(),
        "score": [float(value) for value in row["score"]],
        "development": {
            cost: {split: _public_result(result) for split, result in splits.items()}
            for cost, splits in row["results"].items()
        },
    }


def _audited_payload(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    row = item["row"]
    return {
        **_development_payload(row),
        "confirmation": {
            cost: _public_result(result, complete_only=True)
            for cost, result in item["results"].items()
        },
        "target_month_counts": item["counts"],
        "strict_7_of_7": item["strict"],
    }


def _public_result(result: PortfolioResult, *, complete_only: bool = False) -> dict[str, Any]:
    payload = result.as_dict()
    rows = _complete_months(result.monthly_returns) if complete_only else result.monthly_returns
    payload["monthly_returns"] = [{"label": label, "return": float(value)} for label, value in rows]
    payload["target_15pct_month_rate"] = float(
        Decimal(sum(value >= TARGET_MONTHLY_RETURN for _label, value in rows)) / Decimal(len(rows))
        if rows
        else Decimal("0")
    )
    return payload


def _markdown(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    lines = [
        f"# {payload['id']}",
        "",
        "Development-selected defensive factor search for the strict monthly target.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        "## Search Result",
        "",
        f"- Factor candidates: `{payload['search']['candidate_count']}`.",
        f"- Conditional defensive factors: `{payload['search']['conditional_eligible_count']}`.",
        f"- Development risk-eligible configurations: "
        f"`{payload['search']['development_risk_eligible_count']}`.",
        f"- Best reused-confirmation coverage: `{audit['best_complete_month_count']}/7`.",
        f"- Strict base-and-stress 7/7 configurations: `{audit['strict_pass_count']}`.",
        "",
        payload["decision"]["reason"],
        "Partial `2026-08` is excluded from all strict counts.",
    ]
    for title, section in (
        ("Development-selected Configuration", payload["selection"]["development_selected"]),
        ("Best Reused-Confirmation Diagnostic", audit["best_confirmation_diagnostic"]),
    ):
        if section:
            lines.extend(["", f"## {title}", "", _config_line(section), ""])
            lines.extend(_monthly_table(section))
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _readme(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    return "\n".join(
        [
            "# Defensive Factor Portfolio",
            "",
            "This study selects existing BTC/ETH factors only by their behavior during frozen",
            "market-state baseline loss months in 2021-2025, then searches causal monthly risk",
            "locks. January-July 2026 is reused confirmation; partial August is excluded.",
            "",
            f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
            f"Best strict coverage: `{audit['best_complete_month_count']}/7`; base-and-stress "
            f"7/7 configurations: `{audit['strict_pass_count']}`.",
            "",
            "Reproduce from the repository root:",
            "",
            "```bash",
            ".venv/bin/python scripts/mine_defensive_factor_portfolio.py \\",
            "  --report-id defensive-factor-portfolio-20260815",
            "```",
            "",
        ]
    )


def _config_line(section: dict[str, Any]) -> str:
    config = section["config"]
    return (
        f"`{config['candidate_id']}`; state/factor "
        f"`{config['state_weight']:.0%}/{config['factor_weight']:.0%}`; leverage "
        f"`{config['leverage']:.2f}x`; monthly loss/profit locks "
        f"`{config['monthly_loss_limit']:.0%}/{config['monthly_profit_target']:.0%}`."
    )


def _monthly_table(section: dict[str, Any]) -> list[str]:
    base = section["confirmation"]["base"]["monthly_returns"]
    stress = {
        row["label"]: row["return"] for row in section["confirmation"]["stress"]["monthly_returns"]
    }
    return [
        "| Month | Base | Stress |",
        "|---|---:|---:|",
        *(f"| {row['label']} | {row['return']:.2%} | {stress[row['label']]:.2%} |" for row in base),
    ]


if __name__ == "__main__":
    main()
