#!/usr/bin/env python3
"""Search extreme-funding event factors and a static-anchor hybrid."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_factor_portfolio import (
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    CONFIRMATION,
    DISCOVERY,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    VALIDATION,
)
from train_walk_forward_factor import (
    ANCHOR_ALLOCATIONS,
    ANCHOR_LEVERAGE,
    _anchor_context,
    _evaluate_anchor,
)

from mastermind_tick.bar_research import ResearchBar, ResearchResult, aggregate_bars, funding_by_bar
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_portfolio import (
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
)
from mastermind_tick.funding_event_factor import (
    FundingEventCandidate,
    funding_event_scores,
    funding_event_targets,
)
from mastermind_tick.lead_lag_factor import evaluate_weighted_targets

PERIODS = {"discovery": DISCOVERY, "validation": VALIDATION}
EXPOSURES = tuple(
    Decimal(value) for value in ("0.5", "0.75", "1", "1.5", "2", "2.5", "3", "4", "5")
)
ANCHOR_WEIGHTS = tuple(Decimal(value) for value in ("0.5", "0.6", "0.7", "0.8", "0.9"))
HYBRID_LEVERAGES = tuple(Decimal(value) for value in ("0.75", "1", "1.25", "1.5", "1.75"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/funding_event_factor/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading BTC/ETH 4h bars and funding events", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ("btc_perp", "eth_perp")}
    bars: dict[str, list[ResearchBar]] = {
        asset: aggregate_bars(loaded[asset][0], 240) for asset in loaded
    }
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in bars}
    print("searching extreme-funding event factors", flush=True)
    factor_rows = []
    for asset in bars:
        for candidate in _candidate_library():
            scores = funding_event_scores(funding[asset], candidate.lookback_events)
            base_targets = funding_event_targets(scores, candidate)
            for exposure in EXPOSURES:
                targets = tuple(value * exposure for value in base_targets)
                results = {
                    split: _evaluate(
                        bars[asset],
                        funding[asset],
                        targets,
                        period,
                        BASE_FEE_BPS,
                        BASE_SLIPPAGE_BPS,
                    )
                    for split, period in PERIODS.items()
                }
                factor_rows.append(
                    {
                        "asset": asset,
                        "candidate": candidate,
                        "exposure": exposure,
                        "targets": targets,
                        "results": results,
                        "score": _score(results),
                    }
                )
    factor_eligible = [row for row in factor_rows if _eligible(row["results"])]
    ranked_factors = sorted(factor_eligible, key=lambda row: row["score"], reverse=True)

    anchor = _anchor_context(bars, loaded)
    anchor_results = {
        split: _evaluate_anchor(anchor, period, stress=False) for split, period in PERIODS.items()
    }
    print(
        f"searching anchor hybrids across {len(ranked_factors)} eligible event factors", flush=True
    )
    hybrid_rows = []
    for factor in ranked_factors[:60]:
        for anchor_weight in ANCHOR_WEIGHTS:
            allocations = {
                "static_anchor": anchor_weight,
                "funding_event": Decimal("1") - anchor_weight,
            }
            for leverage in HYBRID_LEVERAGES:
                results = {
                    split: evaluate_static_portfolio(
                        {
                            "static_anchor": anchor_results[split].daily_returns,
                            "funding_event": decimal_returns(
                                factor["results"][split].daily_returns
                            ),
                        },
                        allocations,
                        leverage=leverage,
                    )
                    for split in PERIODS
                }
                hybrid_rows.append(
                    {
                        "factors": ((factor, Decimal("1") - anchor_weight),),
                        "anchor_weight": anchor_weight,
                        "leverage": leverage,
                        "results": results,
                        "score": _score(results),
                    }
                )
    print("searching two-event-factor anchor hybrids", flush=True)
    for left, right in combinations(ranked_factors[:30], 2):
        for anchor_weight in (Decimal("0.5"), Decimal("0.6"), Decimal("0.7"), Decimal("0.8")):
            remainder = Decimal("1") - anchor_weight
            for left_share in (Decimal("0.3333333333"), Decimal("0.5"), Decimal("0.6666666667")):
                left_weight = remainder * left_share
                right_weight = remainder - left_weight
                allocations = {
                    "static_anchor": anchor_weight,
                    "funding_event_1": left_weight,
                    "funding_event_2": right_weight,
                }
                for leverage in HYBRID_LEVERAGES:
                    results = {
                        split: evaluate_static_portfolio(
                            {
                                "static_anchor": anchor_results[split].daily_returns,
                                "funding_event_1": decimal_returns(
                                    left["results"][split].daily_returns
                                ),
                                "funding_event_2": decimal_returns(
                                    right["results"][split].daily_returns
                                ),
                            },
                            allocations,
                            leverage=leverage,
                        )
                        for split in PERIODS
                    }
                    hybrid_rows.append(
                        {
                            "factors": ((left, left_weight), (right, right_weight)),
                            "anchor_weight": anchor_weight,
                            "leverage": leverage,
                            "results": results,
                            "score": _score(results),
                        }
                    )
    hybrid_eligible = [row for row in hybrid_rows if _eligible(row["results"])]
    ranked_hybrids = sorted(hybrid_eligible, key=lambda row: row["score"], reverse=True)
    selected = ranked_hybrids[0] if ranked_hybrids else None
    factor_confirmation = None
    confirmation = None
    stress = None
    if selected:
        factor_confirmation = {}
        base_factors = {}
        stress_factors = {}
        allocations = {"static_anchor": selected["anchor_weight"]}
        for index, (factor, weight) in enumerate(selected["factors"], start=1):
            name = f"funding_event_{index}"
            base_result = _evaluate(
                bars[factor["asset"]],
                funding[factor["asset"]],
                factor["targets"],
                CONFIRMATION,
                BASE_FEE_BPS,
                BASE_SLIPPAGE_BPS,
            )
            stress_result = _evaluate(
                bars[factor["asset"]],
                funding[factor["asset"]],
                factor["targets"],
                CONFIRMATION,
                STRESS_FEE_BPS,
                STRESS_SLIPPAGE_BPS,
            )
            factor_confirmation[name] = base_result
            base_factors[name] = decimal_returns(base_result.daily_returns)
            stress_factors[name] = decimal_returns(stress_result.daily_returns)
            allocations[name] = weight
        confirmation = evaluate_static_portfolio(
            {
                "static_anchor": _evaluate_anchor(anchor, CONFIRMATION, stress=False).daily_returns,
                **base_factors,
            },
            allocations,
            leverage=selected["leverage"],
        )
        stress = evaluate_static_portfolio(
            {
                "static_anchor": _evaluate_anchor(anchor, CONFIRMATION, stress=True).daily_returns,
                **stress_factors,
            },
            allocations,
            leverage=selected["leverage"],
        )
    payload = _report(
        bars,
        factor_rows,
        factor_eligible,
        hybrid_rows,
        hybrid_eligible,
        selected,
        factor_confirmation,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"funding-event-factor-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _candidate_library() -> tuple[FundingEventCandidate, ...]:
    return tuple(
        FundingEventCandidate(lookback, threshold, hold, mode, direction)
        for lookback in (30, 90, 180)
        for threshold in tuple(Decimal(value) for value in ("1", "1.5", "2", "2.5", "3"))
        for hold in (1, 2, 4, 8, 12)
        for mode in ("reversal", "continuation")
        for direction in ("long_only", "long_short")
    )


def _evaluate(
    bars: list[ResearchBar],
    funding: list[list[Any]],
    targets: tuple[Decimal, ...],
    period: tuple[int, int],
    fee_bps: Decimal,
    slippage_bps: Decimal,
) -> ResearchResult:
    return evaluate_weighted_targets(
        bars,
        targets,
        start_ms=period[0],
        end_ms=period[1],
        funding=funding,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )


def _eligible(results: dict[str, Any]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= -0.35
        and getattr(result, "completed_trades", 8) >= 8
        and sum(value > 0 for _label, value in result.monthly_returns)
        >= len(result.monthly_returns) / 2
        and not result.bankrupt
        for result in results.values()
    )


def _score(results: dict[str, Any]) -> tuple[Decimal, ...]:
    def target_rate(result: Any) -> Decimal:
        return Decimal(sum(value >= 0.25 for _label, value in result.monthly_returns)) / Decimal(
            len(result.monthly_returns)
        )

    def positive_rate(result: Any) -> Decimal:
        return Decimal(sum(value > 0 for _label, value in result.monthly_returns)) / Decimal(
            len(result.monthly_returns)
        )

    discovery = results["discovery"]
    validation = results["validation"]
    return (
        min(target_rate(discovery), target_rate(validation)),
        target_rate(discovery) + target_rate(validation),
        min(positive_rate(discovery), positive_rate(validation)),
        min(Decimal(str(discovery.net_return)), Decimal(str(validation.net_return))),
        min(Decimal(str(discovery.max_drawdown)), Decimal(str(validation.max_drawdown))),
    )


def _result(result: Any) -> dict[str, Any]:
    if isinstance(result, PortfolioResult):
        return result.as_dict()
    months = [{"label": label, "return": value} for label, value in result.monthly_returns]
    return {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "positive_month_rate": sum(row["return"] > 0 for row in months) / len(months),
        "target_25pct_month_rate": sum(row["return"] >= 0.25 for row in months) / len(months),
        "monthly_returns": months,
    }


def _report(
    bars: dict[str, list[ResearchBar]],
    factor_rows: list[dict[str, Any]],
    factor_eligible: list[dict[str, Any]],
    hybrid_rows: list[dict[str, Any]],
    hybrid_eligible: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    factor_confirmation: dict[str, ResearchResult] | None,
    confirmation: PortfolioResult | None,
    stress: PortfolioResult | None,
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
    selected_payload = None
    if selected:
        selected_payload = {
            "factors": [
                {
                    "asset": factor["asset"],
                    "candidate": factor["candidate"].as_dict(),
                    "factor_exposure": float(factor["exposure"]),
                    "portfolio_weight": float(weight),
                }
                for factor, weight in selected["factors"]
            ],
            "anchor_weight": float(selected["anchor_weight"]),
            "outer_leverage": float(selected["leverage"]),
            **{name: result.as_dict() for name, result in selected["results"].items()},
        }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "extreme funding-event factor and static-anchor hybrid",
        "data": {
            "first_bar": _timestamp(max(item[0].start_ms for item in bars.values())),
            "last_bar": _timestamp(min(item[-1].end_ms for item in bars.values())),
        },
        "anchor": {
            "allocations": {name: float(value) for name, value in ANCHOR_ALLOCATIONS.items()},
            "internal_leverage": float(ANCHOR_LEVERAGE),
            "frozen_before_search": True,
        },
        "selection": {
            "factor_configuration_count": len(factor_rows),
            "factor_eligible_count": len(factor_eligible),
            "hybrid_configuration_count": len(hybrid_rows),
            "hybrid_eligible_count": len(hybrid_eligible),
            "confirmation_used_for_selection": False,
            "selected": selected_payload,
        },
        "factor_confirmation": (
            {name: _result(result) for name, result in factor_confirmation.items()}
            if factor_confirmation
            else None
        ),
        "confirmation": confirmation.as_dict(include_daily=True) if confirmation else None,
        "stress_confirmation": stress.as_dict() if stress else None,
        "target": {"monthly_return": 0.25, "minimum_target_month_rate": 0.5, "achieved": achieved},
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The funding-event hybrid met reused confirmation gates; fresh forward evidence "
                "remains required."
                if achieved
                else "No development-selected funding-event hybrid passed monthly coverage, "
                "drawdown, and stress gates in reused confirmation."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "Funding-event z-scores use only events preceding the current settlement.",
            "Signals act at the next 4h open and include historical funding and explicit costs.",
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
        "Research-only extreme-funding event factor and static-anchor hybrid.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Factor eligible: `{payload['selection']['factor_eligible_count']}` / "
        f"`{payload['selection']['factor_configuration_count']}`; hybrid eligible: "
        f"`{payload['selection']['hybrid_eligible_count']}` / "
        f"`{payload['selection']['hybrid_configuration_count']}`.",
    ]
    if selected:
        factors = ", ".join(
            f"{row['asset']} `{row['candidate']['id']}` at {row['portfolio_weight']:.0%}"
            for row in selected["factors"]
        )
        lines.extend(
            [
                f"Selected {factors}; anchor weight "
                f"`{selected['anchor_weight']:.0%}`, outer leverage "
                f"`{selected['outer_leverage']:.2f}x`.",
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
