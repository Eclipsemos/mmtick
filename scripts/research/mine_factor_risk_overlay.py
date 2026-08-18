#!/usr/bin/env python3
"""Search monthly loss and profit locks for the frozen static factor anchor."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_factor_portfolio import CONFIRMATION, DISCOVERY, VALIDATION
from train_walk_forward_factor import (
    ANCHOR_ALLOCATIONS,
    ANCHOR_LEVERAGE,
    _anchor_context,
    _evaluate_anchor,
)

from mastermind_tick.bar_research import ResearchBar, aggregate_bars
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_overlay import MonthlyRiskConfig, evaluate_monthly_risk_overlay
from mastermind_tick.factor_portfolio import PortfolioResult

PERIODS = {
    "discovery": DISCOVERY,
    "validation": VALIDATION,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/factor_risk_overlay/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading frozen static factor anchor", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ("btc_perp", "eth_perp")}
    bars: dict[str, list[ResearchBar]] = {
        asset: aggregate_bars(loaded[asset][0], 240) for asset in loaded
    }
    anchor = _anchor_context(bars, loaded)
    anchor_results = {
        name: _evaluate_anchor(anchor, period, stress=False) for name, period in PERIODS.items()
    }
    rows = []
    for config in _candidate_library():
        results = {
            name: evaluate_monthly_risk_overlay(result.daily_returns, config)
            for name, result in anchor_results.items()
        }
        rows.append({"config": config, "results": results, "score": _score(results)})
    eligible = [row for row in rows if _eligible(row["results"])]
    ranked = sorted(eligible, key=lambda row: row["score"], reverse=True)
    selected = ranked[0] if ranked else None
    confirmation = None
    stress = None
    confirmation_diagnostics = []
    if selected:
        confirmation_anchor = _evaluate_anchor(anchor, CONFIRMATION, stress=False)
        stress_anchor = _evaluate_anchor(anchor, CONFIRMATION, stress=True)
        confirmation = evaluate_monthly_risk_overlay(
            confirmation_anchor.daily_returns, selected["config"]
        )
        stress = evaluate_monthly_risk_overlay(stress_anchor.daily_returns, selected["config"])
        for row in ranked:
            base_result = evaluate_monthly_risk_overlay(
                confirmation_anchor.daily_returns, row["config"]
            )
            stress_result = evaluate_monthly_risk_overlay(
                stress_anchor.daily_returns, row["config"]
            )
            confirmation_diagnostics.append(
                {
                    "config": row["config"].as_dict(),
                    "base": base_result.as_dict(),
                    "stress": stress_result.as_dict(),
                    "meets_confirmation_gates": bool(
                        base_result.target_month_rate >= Decimal("0.5")
                        and base_result.max_drawdown >= Decimal("-0.35")
                        and base_result.net_return > 0
                        and stress_result.net_return > 0
                        and stress_result.max_drawdown >= Decimal("-0.35")
                    ),
                }
            )
    payload = _report(
        bars,
        rows,
        eligible,
        selected,
        ranked[:20],
        confirmation,
        stress,
        confirmation_diagnostics,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"factor-risk-overlay-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _candidate_library() -> tuple[MonthlyRiskConfig, ...]:
    return tuple(
        MonthlyRiskConfig(leverage, loss_limit, profit_target)
        for leverage in tuple(
            Decimal(value)
            for value in (
                "1",
                "1.25",
                "1.5",
                "1.75",
                "2",
                "2.25",
                "2.5",
                "2.75",
                "3",
                "3.5",
                "4",
            )
        )
        for loss_limit in tuple(
            Decimal(value) for value in ("0.10", "0.15", "0.20", "0.25", "0.30")
        )
        for profit_target in (
            Decimal("0.25"),
            Decimal("0.30"),
            Decimal("0.40"),
            Decimal("0.50"),
            None,
        )
    )


def _eligible(results: dict[str, PortfolioResult]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and not result.bankrupt
        for result in results.values()
    )


def _score(results: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
    discovery = results["discovery"]
    validation = results["validation"]
    return (
        min(discovery.target_month_rate, validation.target_month_rate),
        discovery.target_month_rate + validation.target_month_rate,
        min(result.positive_month_rate for result in results.values()),
        min(result.worst_month for result in results.values()),
        min(result.net_return for result in results.values()),
        min(result.max_drawdown for result in results.values()),
    )


def _report(
    bars: dict[str, list[ResearchBar]],
    rows: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    top_rows: list[dict[str, Any]],
    confirmation: PortfolioResult | None,
    stress: PortfolioResult | None,
    confirmation_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    achieved = bool(
        confirmation
        and stress
        and confirmation.target_month_rate >= Decimal("0.5")
        and confirmation.max_drawdown >= Decimal("-0.35")
        and confirmation.net_return > 0
        and stress.net_return > 0
        and stress.max_drawdown >= Decimal("-0.35")
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "monthly loss/profit risk overlay on frozen static factor anchor",
        "data": {
            "first_bar": _timestamp(max(item[0].start_ms for item in bars.values())),
            "last_bar": _timestamp(min(item[-1].end_ms for item in bars.values())),
        },
        "anchor": {
            "allocations": {name: float(value) for name, value in ANCHOR_ALLOCATIONS.items()},
            "internal_leverage": float(ANCHOR_LEVERAGE),
            "frozen_before_risk_search": True,
        },
        "selection": {
            "candidate_count": len(rows),
            "eligible_count": len(eligible),
            "confirmation_used_for_selection": False,
            "selected": (
                {
                    "config": selected["config"].as_dict(),
                    **{name: result.as_dict() for name, result in selected["results"].items()},
                }
                if selected
                else None
            ),
            "top_development_configurations": [
                {
                    "config": row["config"].as_dict(),
                    "score": [float(value) for value in row["score"]],
                    **{name: result.as_dict() for name, result in row["results"].items()},
                }
                for row in top_rows
            ],
        },
        "confirmation": confirmation.as_dict(include_daily=True) if confirmation else None,
        "stress_confirmation": stress.as_dict() if stress else None,
        "confirmation_neighborhood_diagnostic": {
            "used_for_selection": False,
            "configuration_count": len(confirmation_diagnostics),
            "meeting_gate_count": sum(
                row["meets_confirmation_gates"] for row in confirmation_diagnostics
            ),
            "configurations": confirmation_diagnostics,
        },
        "target": {"monthly_return": 0.25, "minimum_target_month_rate": 0.5, "achieved": achieved},
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The development-selected risk overlay met the reused confirmation gates; "
                "fresh forward evidence remains required."
                if achieved
                else "The development-selected risk overlay failed monthly coverage, drawdown, "
                "or stress gates."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "Locks trigger after a daily close and flatten exposure on the next UTC day.",
            "Every UTC month resets the loss and profit lock.",
            "Exposure transitions include 7 bps turnover cost.",
            "Borrowing cost, liquidation, market impact, and exchange failure are not modeled.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only monthly risk overlay on the frozen static factor anchor.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Eligible configurations: `{payload['selection']['eligible_count']}` / "
        f"`{payload['selection']['candidate_count']}`.",
    ]
    if selected:
        config = selected["config"]
        profit = "none" if config["profit_target"] is None else f"{config['profit_target']:.0%}"
        lines.extend(
            [
                f"Selected leverage `{config['leverage']:.2f}x`, monthly loss lock "
                f"`{config['loss_limit']:.0%}`, profit lock `{profit}`.",
                "",
                "| Split | Return | Max DD | Positive months | 25% months |",
                "|---|---:|---:|---:|---:|",
                _metric_row("2021-2023 discovery", selected["discovery"]),
                _metric_row("2024-2025 validation", selected["validation"]),
            ]
        )
    if confirmation and stress:
        lines.extend(
            [
                _metric_row("2026 reused confirmation", confirmation),
                _metric_row("2026 stress 10+5 bps", stress),
                "",
                "## 2026 monthly returns",
                "",
                "| Month | Base | Stress |",
                "|---|---:|---:|",
            ]
        )
        stressed = {row["label"]: row["return"] for row in stress["monthly_returns"]}
        lines.extend(
            f"| {row['label']} | {row['return']:.2%} | {stressed[row['label']]:.2%} |"
            for row in confirmation["monthly_returns"]
        )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _metric_row(label: str, result: dict[str, Any]) -> str:
    reached = sum(row["return"] >= 0.25 for row in result["monthly_returns"])
    return (
        f"| {label} | {result['net_return']:.2%} | {result['max_drawdown']:.2%} | "
        f"{result['positive_month_rate']:.2%} | {reached}/{len(result['monthly_returns'])} |"
    )


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
