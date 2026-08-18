#!/usr/bin/env python3
"""Test static development-selected defensive MACD complements around the frozen ensemble."""

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
from mine_drawdown_recovery_trend import (  # noqa: E402
    BASELINE_REPORT_ID,
    PAIR_SHORTLIST_SIZE,
    PAIR_SOURCE_SHORTLIST_SIZE,
    _baseline_curves,
    _baseline_development_results,
    _conditional_pairs,
    _conditional_singles,
    _loss_months,
    _sleeve_payload,
)
from mine_monthly_robust_ensemble import (  # noqa: E402
    _blend_returns,
    _result_payload,
    _risk_score,
)
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
from mastermind_tick.factor_portfolio import DailyReturns, PortfolioResult  # noqa: E402

TREND_WEIGHTS = tuple(
    Decimal(value) for value in ("0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.75")
)
LEVERAGES = tuple(Decimal(value) for value in ("1", "1.5", "2", "3", "4", "5", "6", "8"))
LOSS_LIMITS = tuple(Decimal(value) for value in ("0.10", "0.15", "0.20", "0.25"))
PROFIT_TARGETS = tuple(Decimal(value) for value in ("0.16", "0.18"))


@dataclass(frozen=True)
class StaticTrendConfig:
    sleeve_id: str
    trend_weight: Decimal
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal

    @property
    def id(self) -> str:
        return (
            f"static-defense-{self.sleeve_id}-weight{self.trend_weight}-lev{self.leverage}-"
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
            "sleeve_id": self.sleeve_id,
            "trend_weight": float(self.trend_weight),
            "baseline_weight": float(Decimal("1") - self.trend_weight),
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
        default=Path("reports/experiments/static_defensive_trend/2026-08-15"),
    )
    parser.add_argument("--report-id", help="optional stable output id")
    args = parser.parse_args()

    print("reconstructing the frozen monthly-robust baseline", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    state_curves = _state_curves(loaded, args.metrics_dir)
    periods = _periods()
    flow = _load_flow_cache(args.flow_cache)
    if flow is None:
        raise FileNotFoundError(f"missing version-3 order-flow cache: {args.flow_cache}")
    baseline = _baseline_curves(loaded, state_curves, flow, periods)
    baseline_results = _baseline_development_results(baseline, periods)
    loss_months = _loss_months(baseline_results)

    print("replaying and conditionally screening the predefined MACD library", flush=True)
    macd_rows = [_evaluate_macd(candidate) for candidate in _macd_candidates(loaded)]
    singles = _conditional_singles(macd_rows, loss_months)
    pair_rows = _conditional_pairs(singles[:PAIR_SOURCE_SHORTLIST_SIZE], loss_months)
    selected_pairs = pair_rows[:PAIR_SHORTLIST_SIZE]
    sleeves = [*singles, *selected_pairs]

    print(f"searching static controls for {len(sleeves)} defensive sleeves", flush=True)
    configs = _search_controls(baseline, sleeves, periods)
    print(f"auditing {len(configs):,} development-eligible controls", flush=True)
    audit = _confirmation_audit(configs, periods)

    payload = _report(
        loaded,
        flow,
        baseline_results,
        loss_months,
        macd_rows,
        singles,
        pair_rows,
        selected_pairs,
        configs,
        audit,
        periods,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = args.report_id or (
        f"static-defensive-trend-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    )
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    (args.output_dir / "README.md").write_text(_readme(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _search_controls(
    baseline: dict[str, DailyReturns],
    sleeves: list[dict[str, Any]],
    periods: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    rows = []
    for index, sleeve in enumerate(sleeves, start=1):
        for trend_weight in TREND_WEIGHTS:
            curves = {
                cost: _blend_returns(baseline[cost], sleeve["returns"][cost], trend_weight)
                for cost in ("base", "stress")
            }
            for leverage in LEVERAGES:
                for loss_limit in LOSS_LIMITS:
                    for profit_target in PROFIT_TARGETS:
                        config = StaticTrendConfig(
                            sleeve["id"],
                            trend_weight,
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
                            for cost, values in curves.items()
                        }
                        if _development_eligible(results):
                            rows.append(
                                {
                                    "sleeve": sleeve,
                                    "config": config,
                                    "returns": curves,
                                    "results": results,
                                    "score": _risk_score(results),
                                }
                            )
        if index % 20 == 0:
            print(f"static sleeve {index}/{len(sleeves)}; eligible={len(rows)}", flush=True)
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _confirmation_audit(
    rows: list[dict[str, Any]], periods: dict[str, tuple[int, int]]
) -> dict[str, Any]:
    audited = []
    for row in rows:
        config: StaticTrendConfig = row["config"]
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
    baseline_results: dict[str, dict[str, PortfolioResult]],
    loss_months: dict[str, dict[str, tuple[str, ...]]],
    macd_rows: list[dict[str, Any]],
    singles: list[dict[str, Any]],
    pair_rows: list[dict[str, Any]],
    selected_pairs: list[dict[str, Any]],
    configs: list[dict[str, Any]],
    audit: dict[str, Any],
    periods: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    strict_count = audit["strict_pass_count"]
    best_count = (
        min(audit["best_confirmation"]["counts"].values()) if audit["best_confirmation"] else 0
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "static development-selected defensive MACD complement",
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
            "baseline_report_id": BASELINE_REPORT_ID,
            "conditional_selection": (
                "MACD return must be positive under base and stress costs in every negative "
                "baseline month of both development years"
            ),
        },
        "baseline": {
            "development_loss_months": {
                cost: {split: list(months) for split, months in values.items()}
                for cost, values in loss_months.items()
            },
            "development": {
                cost: {split: _result_payload(result) for split, result in values.items()}
                for cost, values in baseline_results.items()
            },
        },
        "search": {
            "macd_candidate_count": len(macd_rows),
            "conditional_single_count": len(singles),
            "conditional_pair_count": len(pair_rows),
            "pair_shortlist_size": len(selected_pairs),
            "defensive_sleeve_count": len(singles) + len(selected_pairs),
            "control_grid_per_sleeve": (
                len(TREND_WEIGHTS) * len(LEVERAGES) * len(LOSS_LIMITS) * len(PROFIT_TARGETS)
            ),
            "development_risk_eligible_count": len(configs),
            "top_conditional_singles": [_sleeve_payload(row) for row in singles[:20]],
            "top_conditional_pairs": [_sleeve_payload(row) for row in selected_pairs[:20]],
            "top_development_controls": [_development_payload(row) for row in configs[:20]],
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
                "At least one development-selected static defensive sleeve reached +15% in all "
                "seven complete reused-confirmation months under base and stress costs; fresh "
                "forward evidence remains required."
                if strict_count
                else "No development-selected static defensive sleeve reached +15% in all seven "
                "complete 2026 months under both base and stress costs."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The static complement study follows observed prior 2026 failures.",
            "The conditional screen has only one frozen-baseline loss month per development year.",
            "Static sleeve rebalancing turnover beyond embedded component costs is not modeled.",
            "Drawdown is daily-close only; liquidation and borrowing costs are not modeled.",
        ],
    }


def _development_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sleeve": _sleeve_payload(row["sleeve"]),
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
        "sleeve": _sleeve_payload(row["row"]["sleeve"]),
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
        "Static single and paired MACD complements selected on frozen-baseline development",
        "loss months.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "",
        "## Search Result",
        "",
        f"- Conditional single MACD sleeves: `{payload['search']['conditional_single_count']}`.",
        f"- Development-selected MACD pairs: `{payload['search']['pair_shortlist_size']}`.",
        f"- Development risk-eligible controls: "
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
            "# Static Defensive Trend",
            "",
            "This study holds development-selected single or paired MACD sleeves alongside the",
            "frozen monthly-robust baseline. Selection uses 2024/2025 only; January-July 2026",
            "is reused confirmation and partial August is excluded.",
            "",
            f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
            f"Best strict coverage: `{audit['best_complete_month_count']}/7`; base-and-stress "
            f"7/7 configurations: `{audit['strict_pass_count']}`.",
            "",
            "Reproduce from the repository root:",
            "",
            "```bash",
            ".venv/bin/python scripts/research/mine_static_defensive_trend.py \\",
            "  --report-id static-defensive-trend-20260815",
            "```",
            "",
        ]
    )


def _config_line(row: dict[str, Any]) -> str:
    config = row["config"]
    return (
        f"`{row['sleeve']['id']}`; trend/baseline `{config['trend_weight']:.0%}/"
        f"{config['baseline_weight']:.0%}`; leverage `{config['leverage']:.2f}x`; monthly "
        f"loss/profit locks `{config['monthly_loss_limit']:.0%}/"
        f"{config['monthly_profit_target']:.0%}`."
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
