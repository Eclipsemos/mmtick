#!/usr/bin/env python3
"""Search every factor as a marginal sleeve beside the frozen static factor anchor."""

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

from mine_factor_portfolio import (
    CONFIRMATION,
    DISCOVERY,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    SleeveCandidate,
    _candidate_library,
    _evaluate_candidate,
    _event_candidate_library,
)
from train_continuous_factor import SELECTION_2024, SELECTION_2025
from train_walk_forward_factor import (
    ANCHOR_ALLOCATIONS,
    ANCHOR_LEVERAGE,
    _anchor_context,
    _evaluate_anchor,
)

from mastermind_tick.bar_research import ResearchBar, aggregate_bars
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_portfolio import (
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
)

PERIODS = {
    "discovery": DISCOVERY,
    "selection_2024": SELECTION_2024,
    "selection_2025": SELECTION_2025,
}
FACTOR_WEIGHTS = tuple(Decimal(value) for value in ("0.05", "0.1", "0.15", "0.2", "0.25", "0.3"))
OUTER_LEVERAGES = tuple(Decimal(value) for value in ("0.75", "1", "1.25", "1.5"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/marginal_factor_portfolio/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading BTC/ETH history and frozen static anchor", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ("btc_perp", "eth_perp")}
    bars: dict[str, list[ResearchBar]] = {
        asset: aggregate_bars(loaded[asset][0], 240) for asset in loaded
    }
    anchor = _anchor_context(bars, loaded)
    anchor_results = {
        name: _evaluate_anchor(anchor, period, stress=False) for name, period in PERIODS.items()
    }
    candidates = [
        *_candidate_library("btc_perp", loaded["btc_perp"][0], loaded["btc_perp"][1]),
        *_candidate_library("eth_perp", loaded["eth_perp"][0], loaded["eth_perp"][1]),
        *_event_candidate_library(
            bars["btc_perp"],
            bars["eth_perp"],
            loaded["btc_perp"][1],
            loaded["eth_perp"][1],
        ),
    ]
    print(f"evaluating {len(candidates):,} unrestricted marginal factors", flush=True)
    eligible = []
    evaluated_configs = 0
    for index, candidate in enumerate(candidates, start=1):
        candidate_results = {
            name: _evaluate_candidate(candidate, period) for name, period in PERIODS.items()
        }
        if any(
            tuple(label for label, _value in candidate_results[name].daily_returns)
            != tuple(label for label, _value in anchor_results[name].daily_returns)
            for name in PERIODS
        ):
            continue
        for factor_weight in FACTOR_WEIGHTS:
            allocations = {
                "static_anchor": Decimal("1") - factor_weight,
                "marginal_factor": factor_weight,
            }
            for leverage in OUTER_LEVERAGES:
                evaluated_configs += 1
                results = {
                    name: _combine(
                        anchor_results[name],
                        candidate_results[name],
                        allocations,
                        leverage,
                    )
                    for name in PERIODS
                }
                if _eligible(results):
                    eligible.append(
                        {
                            "candidate": candidate,
                            "factor_weight": factor_weight,
                            "leverage": leverage,
                            "results": results,
                            "score": _score(results),
                        }
                    )
        if index % 200 == 0:
            print(
                f"marginal {index}/{len(candidates)}; risk eligible={len(eligible)}",
                flush=True,
            )
    if not eligible:
        raise RuntimeError("no unrestricted marginal factor passed development risk gates")
    ranked = sorted(eligible, key=lambda row: row["score"], reverse=True)
    selected = ranked[0]
    print(f"selected {selected['candidate'].id}", flush=True)
    confirmation_anchor = _evaluate_anchor(anchor, CONFIRMATION, stress=False)
    confirmation_factor = _evaluate_candidate(selected["candidate"], CONFIRMATION)
    stress_anchor = _evaluate_anchor(anchor, CONFIRMATION, stress=True)
    stress_factor = _evaluate_candidate(
        selected["candidate"],
        CONFIRMATION,
        fee_bps=STRESS_FEE_BPS,
        slippage_bps=STRESS_SLIPPAGE_BPS,
    )
    allocations = {
        "static_anchor": Decimal("1") - selected["factor_weight"],
        "marginal_factor": selected["factor_weight"],
    }
    confirmation = _combine(
        confirmation_anchor, confirmation_factor, allocations, selected["leverage"]
    )
    stress = _combine(stress_anchor, stress_factor, allocations, selected["leverage"])
    payload = _report(
        bars,
        candidates,
        evaluated_configs,
        eligible,
        selected,
        ranked[:20],
        confirmation_factor,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"marginal-factor-portfolio-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _combine(
    anchor: PortfolioResult,
    candidate: Any,
    allocations: dict[str, Decimal],
    leverage: Decimal,
) -> PortfolioResult:
    return evaluate_static_portfolio(
        {
            "static_anchor": anchor.daily_returns,
            "marginal_factor": decimal_returns(candidate.daily_returns),
        },
        allocations,
        leverage=leverage,
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
    selections = (results["selection_2024"], results["selection_2025"])
    return (
        min(result.target_month_rate for result in selections),
        sum((result.target_month_rate for result in selections), Decimal("0")),
        discovery.target_month_rate,
        min(result.positive_month_rate for result in results.values()),
        min(result.worst_month for result in results.values()),
        min(result.net_return for result in results.values()),
        min(result.max_drawdown for result in results.values()),
    )


def _selection_row(row: dict[str, Any]) -> dict[str, Any]:
    candidate: SleeveCandidate = row["candidate"]
    return {
        "candidate": {
            "id": candidate.id,
            "instrument_id": candidate.instrument_id,
            "family": candidate.family,
            "interval_minutes": candidate.interval_minutes,
            "parameters": candidate.parameters,
        },
        "factor_weight": float(row["factor_weight"]),
        "outer_leverage": float(row["leverage"]),
        "score": [float(value) for value in row["score"]],
        **{name: result.as_dict() for name, result in row["results"].items()},
    }


def _report(
    bars: dict[str, list[ResearchBar]],
    candidates: list[SleeveCandidate],
    evaluated_configs: int,
    eligible: list[dict[str, Any]],
    selected: dict[str, Any],
    top_rows: list[dict[str, Any]],
    confirmation_factor: Any,
    confirmation: PortfolioResult,
    stress: PortfolioResult,
) -> dict[str, Any]:
    achieved = bool(
        confirmation.target_month_rate >= Decimal("0.5")
        and confirmation.max_drawdown >= Decimal("-0.35")
        and confirmation.net_return > 0
        and stress.net_return > 0
        and stress.max_drawdown >= Decimal("-0.35")
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "unrestricted marginal factor plus frozen static event anchor",
        "data": {
            "first_bar": _timestamp(max(item[0].start_ms for item in bars.values())),
            "last_bar": _timestamp(min(item[-1].end_ms for item in bars.values())),
        },
        "anchor": {
            "allocations": {name: float(value) for name, value in ANCHOR_ALLOCATIONS.items()},
            "internal_leverage": float(ANCHOR_LEVERAGE),
            "frozen_before_marginal_search": True,
        },
        "selection": {
            "candidate_count": len(candidates),
            "configuration_count": evaluated_configs,
            "risk_eligible_count": len(eligible),
            "confirmation_used_for_selection": False,
            "standalone_factor_eligibility_required": False,
            "selected": _selection_row(selected),
            "top_development_candidates": [_selection_row(row) for row in top_rows],
        },
        "confirmation_factor": {
            "net_return": confirmation_factor.net_return,
            "max_drawdown": confirmation_factor.max_drawdown,
            "monthly_returns": [
                {"label": label, "return": value}
                for label, value in confirmation_factor.monthly_returns
            ],
        },
        "confirmation": confirmation.as_dict(include_daily=True),
        "stress_confirmation": stress.as_dict(),
        "target": {"monthly_return": 0.25, "minimum_target_month_rate": 0.5, "achieved": achieved},
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The unrestricted marginal factor met the reused confirmation gates; fresh "
                "forward evidence remains required."
                if achieved
                else "The development-selected marginal factor failed monthly coverage, "
                "drawdown, or stress gates."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The search is limited to one marginal sleeve and fixed initial capital allocations.",
            "Portfolio drawdown is measured at daily closes.",
            "Borrowing cost, liquidation, market impact, and exchange failure are not modeled.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    candidate = selected["candidate"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only unrestricted marginal-factor portfolio.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Evaluated `{payload['selection']['configuration_count']:,}` configurations; "
        f"`{payload['selection']['risk_eligible_count']:,}` passed development risk gates.",
        f"Selected `{candidate['id']}` at `{selected['factor_weight']:.0%}` plus the static "
        f"anchor at outer leverage `{selected['outer_leverage']:.2f}x`.",
        "",
        "| Split | Return | Max DD | Positive months | 25% months |",
        "|---|---:|---:|---:|---:|",
        _metric_row("2021-2023 discovery", selected["discovery"]),
        _metric_row("2024 selection", selected["selection_2024"]),
        _metric_row("2025 selection", selected["selection_2025"]),
        _metric_row("2026 reused confirmation", confirmation),
        _metric_row("2026 stress 10+5 bps", stress),
        "",
        "## 2026 monthly returns",
        "",
        "| Month | Base | Stress |",
        "|---|---:|---:|",
    ]
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
