#!/usr/bin/env python3
"""Search development-selected ensembles for stable monthly returns."""

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

from mine_btc_order_flow import _load_flow_cache, _periods  # noqa: E402
from mine_defensive_factor_portfolio import (  # noqa: E402
    _development_eligible,
    _period_returns,
    _strict_count,
)
from mine_fast_trend_complement import _unlocked_result  # noqa: E402
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
from mine_order_flow_complement import (  # noqa: E402
    _candidate_replays,
    _pair_shortlist,
)

from mastermind_tick.bar_research import ResearchBar, aggregate_bars, funding_by_bar  # noqa: E402
from mastermind_tick.factor_mining import load_market  # noqa: E402
from mastermind_tick.factor_overlay import (  # noqa: E402
    MonthlyRiskConfig,
    evaluate_monthly_risk_overlay,
)
from mastermind_tick.factor_portfolio import DailyReturns, PortfolioResult  # noqa: E402
from mastermind_tick.order_flow import (  # noqa: E402
    candidate_library,
    causal_flow_features,
)

SCREEN_WEIGHTS = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75"))
ADDITION_WEIGHTS = tuple(Decimal(value) for value in ("0.15", "0.25", "0.4"))
COMPONENT_SHORTLIST_SIZE = 80
BEAM_SIZE = 100
MAX_FACTOR_COUNT = 3
PORTFOLIO_SHORTLIST_SIZE = 120
LEVERAGES = tuple(Decimal(value) for value in ("1", "1.5", "2", "3", "4", "5", "6", "8"))
LOSS_LIMITS = tuple(Decimal(value) for value in ("0.10", "0.15", "0.20", "0.25"))
PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.16", "0.18"))


@dataclass(frozen=True)
class EnsembleRiskConfig:
    portfolio_id: str
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"{self.portfolio_id}-lev{self.leverage}-loss{self.loss_limit}-"
            f"profit{self.profit_target}"
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
            "portfolio_id": self.portfolio_id,
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
        default=Path("reports/experiments/monthly_robust_ensemble/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    print("loading frozen state and common market history", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state_curves = _state_curves(loaded, args.metrics_dir)
    periods = _periods()

    print("replaying the full predefined MACD library", flush=True)
    macd_rows = [_evaluate_macd(candidate) for candidate in _macd_candidates(loaded)]
    components = {
        row["candidate"].id: {
            "source": "macd",
            "metadata": row["candidate"].as_dict(),
            "returns": row["returns"],
        }
        for row in macd_rows
    }

    print("replaying development-eligible order-flow sleeves", flush=True)
    bars = aggregate_bars(loaded["btc_perp"][0], 240)
    funding = funding_by_bar(bars, loaded["btc_perp"][1])
    flow = _load_flow_cache(args.flow_cache)
    if flow is None:
        raise FileNotFoundError(f"missing version-3 order-flow cache: {args.flow_cache}")
    flow_candidates = candidate_library()
    features = {
        window: causal_flow_features(bars, flow, window)
        for window in {candidate.window for candidate in flow_candidates}
    }
    eligible_flow = _candidate_replays(flow_candidates, features, bars, funding, periods)
    flow_replays = {row["candidate"].id: row["returns"] for row in eligible_flow}
    pair_rows = _pair_shortlist(flow_replays, periods)
    for row in eligible_flow:
        candidate = row["candidate"]
        components[candidate.id] = {
            "source": "order_flow",
            "metadata": candidate.as_dict(),
            "returns": row["returns"],
        }
    for row in pair_rows:
        components[row["id"]] = {
            "source": "order_flow_pair",
            "metadata": {
                "id": row["id"],
                "left": row["left"],
                "left_weight": float(row["left_weight"]),
                "right": row["right"],
                "right_weight": float(row["right_weight"]),
            },
            "returns": row["returns"],
        }

    print(f"screening {len(components)} sleeves for monthly stability", flush=True)
    screened = _screen_components(state_curves, components, periods)
    component_shortlist = screened[:COMPONENT_SHORTLIST_SIZE]
    print(f"running beam search with {len(component_shortlist)} components", flush=True)
    portfolios = _beam_search(state_curves, components, component_shortlist, periods)
    portfolio_shortlist = portfolios[:PORTFOLIO_SHORTLIST_SIZE]
    print(f"searching risk controls for {len(portfolio_shortlist)} portfolios", flush=True)
    risk_rows = _search_risk_controls(portfolio_shortlist, periods)
    print(f"auditing {len(risk_rows):,} development-eligible controls", flush=True)
    audit = _confirmation_audit(risk_rows, periods)

    payload = _report(
        loaded,
        flow,
        macd_rows,
        flow_candidates,
        eligible_flow,
        pair_rows,
        components,
        screened,
        component_shortlist,
        portfolios,
        portfolio_shortlist,
        risk_rows,
        audit,
        periods,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"monthly-robust-ensemble-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _blend_returns(left: DailyReturns, right: DailyReturns, right_weight: Decimal) -> DailyReturns:
    if not Decimal("0") <= right_weight <= Decimal("1"):
        raise ValueError("blend weight must be in [0, 1]")
    right_by_label = dict(right)
    left_weight = Decimal("1") - right_weight
    return tuple(
        (label, left_weight * value + right_weight * right_by_label[label])
        for label, value in left
        if label in right_by_label
    )


def _add_component_weights(
    weights: dict[str, Decimal], component_id: str, addition_weight: Decimal
) -> dict[str, Decimal]:
    if component_id in weights or not Decimal("0") < addition_weight < Decimal("1"):
        raise ValueError("ensemble component or addition weight is invalid")
    remaining = Decimal("1") - addition_weight
    return {
        **{name: value * remaining for name, value in weights.items()},
        component_id: addition_weight,
    }


def _portfolio_id(weights: dict[str, Decimal]) -> str:
    encoded = "-".join(
        f"{name}@{str(weight).replace('.', 'p')}" for name, weight in sorted(weights.items())
    )
    return f"monthly-ensemble-{encoded}"


def _development_results(
    curves: dict[str, DailyReturns], periods: dict[str, tuple[int, int]]
) -> dict[str, dict[str, PortfolioResult]]:
    return {
        cost: {
            split: _unlocked_result(_period_returns(values, periods[split]))
            for split in ("train", "validation")
        }
        for cost, values in curves.items()
    }


def _raw_eligible(results: dict[str, dict[str, PortfolioResult]]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and not result.bankrupt
        for costs in results.values()
        for result in costs.values()
    )


def _stability_score(results: dict[str, dict[str, PortfolioResult]]) -> tuple[Decimal, ...]:
    values = tuple(result for costs in results.values() for result in costs.values())
    return (
        min(result.positive_month_rate for result in values),
        min(result.worst_month for result in values),
        min(result.net_return for result in values),
        min(result.max_drawdown for result in values),
    )


def _screen_components(
    state_curves: dict[str, DailyReturns],
    components: dict[str, dict[str, Any]],
    periods: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    rows = []
    for index, (component_id, component) in enumerate(components.items(), start=1):
        best = None
        for factor_weight in SCREEN_WEIGHTS:
            curves = {
                cost: _blend_returns(state_curves[cost], values, factor_weight)
                for cost, values in component["returns"].items()
            }
            results = _development_results(curves, periods)
            if _raw_eligible(results):
                row = {
                    "weights": {
                        "frozen_state": Decimal("1") - factor_weight,
                        component_id: factor_weight,
                    },
                    "component_ids": (component_id,),
                    "returns": curves,
                    "results": results,
                    "score": _stability_score(results),
                }
                if best is None or row["score"] > best["score"]:
                    best = row
        if best is not None:
            rows.append(best)
        if index % 100 == 0:
            print(f"component {index}/{len(components)}; eligible={len(rows)}", flush=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _beam_search(
    state_curves: dict[str, DailyReturns],
    components: dict[str, dict[str, Any]],
    component_shortlist: list[dict[str, Any]],
    periods: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    del state_curves
    component_ids = tuple(row["component_ids"][0] for row in component_shortlist)
    beam = component_shortlist[:BEAM_SIZE]
    all_rows = list(beam)
    for factor_count in range(2, MAX_FACTOR_COUNT + 1):
        candidates: dict[tuple[tuple[str, Decimal], ...], dict[str, Any]] = {}
        for row in beam:
            used = set(row["component_ids"])
            for component_id in component_ids:
                if component_id in used:
                    continue
                for addition_weight in ADDITION_WEIGHTS:
                    weights = _add_component_weights(row["weights"], component_id, addition_weight)
                    key = tuple(sorted(weights.items()))
                    curves = {
                        cost: _blend_returns(
                            row["returns"][cost],
                            components[component_id]["returns"][cost],
                            addition_weight,
                        )
                        for cost in ("base", "stress")
                    }
                    results = _development_results(curves, periods)
                    if not _raw_eligible(results):
                        continue
                    candidate = {
                        "weights": weights,
                        "component_ids": (*row["component_ids"], component_id),
                        "returns": curves,
                        "results": results,
                        "score": _stability_score(results),
                    }
                    current = candidates.get(key)
                    if current is None or candidate["score"] > current["score"]:
                        candidates[key] = candidate
        beam = sorted(candidates.values(), key=lambda row: row["score"], reverse=True)[:BEAM_SIZE]
        all_rows.extend(beam)
        print(
            f"beam depth {factor_count}; candidates={len(candidates)}; retained={len(beam)}",
            flush=True,
        )
        if not beam:
            break
    unique = {tuple(sorted(row["weights"].items())): row for row in all_rows}
    return sorted(unique.values(), key=lambda row: row["score"], reverse=True)


def _search_risk_controls(
    portfolios: list[dict[str, Any]], periods: dict[str, tuple[int, int]]
) -> list[dict[str, Any]]:
    rows = []
    for index, portfolio in enumerate(portfolios, start=1):
        portfolio_id = _portfolio_id(portfolio["weights"])
        for leverage in LEVERAGES:
            for loss_limit in LOSS_LIMITS:
                for profit_target in PROFIT_TARGETS:
                    config = EnsembleRiskConfig(
                        portfolio_id,
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
                            for split in ("train", "validation")
                        }
                        for cost, values in portfolio["returns"].items()
                    }
                    if _development_eligible(results):
                        rows.append(
                            {
                                "weights": portfolio["weights"],
                                "config": config,
                                "returns": portfolio["returns"],
                                "results": results,
                                "score": _risk_score(results),
                            }
                        )
        if index % 20 == 0:
            print(f"risk portfolio {index}/{len(portfolios)}; eligible={len(rows)}", flush=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _risk_score(results: dict[str, dict[str, PortfolioResult]]) -> tuple[Decimal, ...]:
    values = tuple(result for costs in results.values() for result in costs.values())
    return (
        min(result.positive_month_rate for result in values),
        min(
            Decimal(sum(value >= TARGET_MONTHLY_RETURN for _label, value in result.monthly_returns))
            / Decimal(len(result.monthly_returns))
            for result in values
        ),
        min(result.worst_month for result in values),
        min(result.net_return for result in values),
        min(result.max_drawdown for result in values),
    )


def _confirmation_audit(
    rows: list[dict[str, Any]], periods: dict[str, tuple[int, int]]
) -> dict[str, Any]:
    audited = []
    for row in rows:
        config: EnsembleRiskConfig = row["config"]
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
    macd_rows: list[dict[str, Any]],
    flow_candidates: tuple[Any, ...],
    eligible_flow: list[dict[str, Any]],
    pair_rows: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
    screened: list[dict[str, Any]],
    component_shortlist: list[dict[str, Any]],
    portfolios: list[dict[str, Any]],
    portfolio_shortlist: list[dict[str, Any]],
    risk_rows: list[dict[str, Any]],
    audit: dict[str, Any],
    periods: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    strict_count = audit["strict_pass_count"]
    best_count = (
        min(audit["best_confirmation"]["counts"].values()) if audit["best_confirmation"] else 0
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "development-selected monthly-stability ensemble",
        "data": {
            "btc_first_bar": _timestamp(loaded["btc_perp"][0][0].start_ms),
            "btc_last_bar": _timestamp(loaded["btc_perp"][0][-1].end_ms),
            "order_flow_bars": len(flow),
            "order_flow_first": _timestamp(min(flow)),
            "order_flow_last": _timestamp(max(flow) + 4 * 60 * 60 * 1000 - 1),
        },
        "protocol": {
            "train": _period_payload(periods["train"]),
            "validation": _period_payload(periods["validation"]),
            "confirmation": _period_payload(periods["confirmation"]),
            "strict_confirmation_end": COMPLETE_CONFIRMATION_END.isoformat(),
            "partial_august_excluded": True,
            "confirmation_used_for_selection": False,
            "selection_objective": (
                "maximize the minimum positive-month rate, worst month, net return, and "
                "drawdown across train/validation and base/stress costs"
            ),
        },
        "search": {
            "macd_candidate_count": len(macd_rows),
            "order_flow_candidate_count": len(flow_candidates),
            "eligible_order_flow_count": len(eligible_flow),
            "eligible_order_flow_pair_count": len(pair_rows),
            "component_count": len(components),
            "screened_component_count": len(screened),
            "component_shortlist_size": len(component_shortlist),
            "beam_size": BEAM_SIZE,
            "maximum_factor_count": MAX_FACTOR_COUNT,
            "development_portfolio_count": len(portfolios),
            "portfolio_shortlist_size": len(portfolio_shortlist),
            "risk_grid_per_portfolio": (len(LEVERAGES) * len(LOSS_LIMITS) * len(PROFIT_TARGETS)),
            "development_risk_eligible_count": len(risk_rows),
            "component_metadata": {
                name: {"source": row["source"], "metadata": row["metadata"]}
                for name, row in components.items()
                if any(name in portfolio["weights"] for portfolio in portfolio_shortlist[:20])
            },
            "top_development_portfolios": [
                _portfolio_payload(row) for row in portfolio_shortlist[:20]
            ],
            "top_development_controls": [_development_payload(row) for row in risk_rows[:20]],
        },
        "selection": {
            "development_selected": _audit_payload(audit["development_selected"]),
        },
        "confirmation_audit": {
            "configuration_count": audit["configuration_count"],
            "strict_pass_count": strict_count,
            "best_complete_month_count": best_count,
            "best_confirmation_diagnostic": _audit_payload(audit["best_confirmation"]),
            "strict_examples": [_audit_payload(row) for row in audit["strict_examples"]],
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
                "At least one development-selected monthly-stability ensemble reached +15% in "
                "all seven complete reused-confirmation months under base and stress costs; "
                "fresh forward evidence remains required."
                if strict_count
                else "No development-selected monthly-stability ensemble reached +15% in all "
                "seven complete 2026 months under both base and stress costs."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "Order-flow history restricts the common development period to 2024/2025.",
            "The ensemble search was specified after observing prior 2026 failures.",
            "Static sleeve rebalancing turnover beyond embedded component costs is not modeled.",
            "Drawdown is daily-close only; liquidation and borrowing costs are not modeled.",
        ],
    }


def _portfolio_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _portfolio_id(row["weights"]),
        "weights": {name: float(value) for name, value in row["weights"].items()},
        "score": [float(value) for value in row["score"]],
        "development": {
            cost: {split: _result_payload(result) for split, result in values.items()}
            for cost, values in row["results"].items()
        },
    }


def _development_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "portfolio_id": _portfolio_id(row["weights"]),
        "weights": {name: float(value) for name, value in row["weights"].items()},
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
        "portfolio_id": _portfolio_id(row["row"]["weights"]),
        "weights": {name: float(value) for name, value in row["row"]["weights"].items()},
        "config": row["row"]["config"].as_dict(),
        "counts": row["counts"],
        "development_score": [float(value) for value in row["row"]["score"]],
        "confirmation": {cost: _result_payload(result) for cost, result in row["results"].items()},
    }


def _result_payload(result: PortfolioResult) -> dict[str, Any]:
    return {
        "net_return": float(result.net_return),
        "max_drawdown": float(result.max_drawdown),
        "bankrupt": result.bankrupt,
        "monthly_returns": [
            {"label": label, "return": float(value)} for label, value in result.monthly_returns
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    audit = payload["confirmation_audit"]
    lines = [
        f"# {payload['id']}",
        "",
        "Development-selected monthly-stability ensemble of the frozen state strategy,",
        "predefined MACD sleeves, and development-eligible BTC order flow.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        "## Search Result",
        "",
        f"- Components: `{payload['search']['component_count']}`.",
        f"- Development portfolios: `{payload['search']['development_portfolio_count']}`.",
        f"- Development risk-eligible configurations: "
        f"`{payload['search']['development_risk_eligible_count']}`.",
        f"- Best reused-confirmation coverage: `{audit['best_complete_month_count']}/7`.",
        f"- Strict base-and-stress 7/7 configurations: `{audit['strict_pass_count']}`.",
        "",
        payload["decision"]["reason"],
        "Partial `2026-08` is excluded from strict counts.",
    ]
    for title, row in (
        ("Development Selection", payload["selection"]["development_selected"]),
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
            "# Monthly Robust Ensemble",
            "",
            "This study selects fixed-weight multi-sleeve portfolios by monthly stability",
            "across 2024/2025 under base and stress costs. January-July 2026 is reused",
            "confirmation and partial August is excluded from strict counts.",
            "",
            f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
            f"Best strict coverage: `{audit['best_complete_month_count']}/7`; base-and-stress "
            f"7/7 configurations: `{audit['strict_pass_count']}`.",
            "",
            "Reproduce from the repository root:",
            "",
            "```bash",
            ".venv/bin/python scripts/research/mine_monthly_robust_ensemble.py \\",
            "  --report-id monthly-robust-ensemble-20260815",
            "```",
            "",
        ]
    )


def _config_line(row: dict[str, Any]) -> str:
    weights = ", ".join(f"{name}={value:.0%}" for name, value in row["weights"].items())
    config = row["config"]
    return (
        f"Weights `{weights}`; leverage `{config['leverage']:.2f}x`; monthly loss/profit locks "
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
