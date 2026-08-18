#!/usr/bin/env python3
"""Search a causal BTC/ETH funding-spread factor and static-anchor hybrid."""

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
from mastermind_tick.funding_spread_factor import (
    FundingSpreadCandidate,
    funding_spread_scores,
    funding_spread_targets,
)
from mastermind_tick.lead_lag_factor import evaluate_weighted_targets

PERIODS = {"discovery": DISCOVERY, "validation": VALIDATION}
PAIR_LEVERAGES = tuple(
    Decimal(value) for value in ("0.5", "0.75", "1", "1.5", "2", "2.5", "3", "4")
)
ANCHOR_WEIGHTS = tuple(Decimal(value) for value in ("0.5", "0.6", "0.7", "0.8", "0.9"))
HYBRID_LEVERAGES = tuple(Decimal(value) for value in ("0.75", "1", "1.25", "1.5", "1.75"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/funding_spread_factor/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading aligned BTC/ETH bars and funding history", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ("btc_perp", "eth_perp")}
    bars: dict[str, list[ResearchBar]] = {
        asset: aggregate_bars(loaded[asset][0], 240) for asset in loaded
    }
    if tuple(bar.start_ms for bar in bars["btc_perp"]) != tuple(
        bar.start_ms for bar in bars["eth_perp"]
    ):
        raise ValueError("funding spread BTC/ETH bars are not aligned")
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in bars}

    print("searching funding-spread signal and pair exposure", flush=True)
    pair_rows = []
    for candidate in _candidate_library():
        scores = funding_spread_scores(
            funding["btc_perp"], funding["eth_perp"], candidate.lookback_bars
        )
        targets = funding_spread_targets(scores, candidate)
        components = {
            split: _evaluate_pair_components(
                bars,
                funding,
                targets,
                period,
                BASE_FEE_BPS,
                BASE_SLIPPAGE_BPS,
            )
            for split, period in PERIODS.items()
        }
        if any(
            any(result.bankrupt for result in values.values())
            or tuple(label for label, _value in values["btc_perp"].daily_returns)
            != tuple(label for label, _value in values["eth_perp"].daily_returns)
            for values in components.values()
        ):
            continue
        for leverage in PAIR_LEVERAGES:
            results = {
                split: _pair_portfolio(values, leverage) for split, values in components.items()
            }
            pair_rows.append(
                {
                    "candidate": candidate,
                    "targets": targets,
                    "leverage": leverage,
                    "components": components,
                    "results": results,
                    "score": _score(results),
                }
            )
    pair_eligible = [row for row in pair_rows if _eligible(row["results"])]
    ranked_pairs = sorted(pair_eligible, key=lambda row: row["score"], reverse=True)

    anchor = _anchor_context(bars, loaded)
    anchor_results = {
        split: _evaluate_anchor(anchor, period, stress=False) for split, period in PERIODS.items()
    }
    print(f"searching anchor hybrids across {len(ranked_pairs)} eligible pair factors", flush=True)
    hybrid_rows = []
    for pair_row in ranked_pairs[:40]:
        for anchor_weight in ANCHOR_WEIGHTS:
            allocations = {
                "static_anchor": anchor_weight,
                "funding_spread": Decimal("1") - anchor_weight,
            }
            for leverage in HYBRID_LEVERAGES:
                results = {
                    split: evaluate_static_portfolio(
                        {
                            "static_anchor": anchor_results[split].daily_returns,
                            "funding_spread": pair_row["results"][split].daily_returns,
                        },
                        allocations,
                        leverage=leverage,
                    )
                    for split in PERIODS
                }
                hybrid_rows.append(
                    {
                        "pair": pair_row,
                        "anchor_weight": anchor_weight,
                        "leverage": leverage,
                        "results": results,
                        "score": _score(results),
                    }
                )
    hybrid_eligible = [row for row in hybrid_rows if _eligible(row["results"])]
    ranked_hybrids = sorted(hybrid_eligible, key=lambda row: row["score"], reverse=True)
    selected = ranked_hybrids[0] if ranked_hybrids else None
    confirmation = None
    stress = None
    pair_confirmation = None
    if selected:
        pair = selected["pair"]
        pair_confirmation_components = _evaluate_pair_components(
            bars,
            funding,
            pair["targets"],
            CONFIRMATION,
            BASE_FEE_BPS,
            BASE_SLIPPAGE_BPS,
        )
        pair_stress_components = _evaluate_pair_components(
            bars,
            funding,
            pair["targets"],
            CONFIRMATION,
            STRESS_FEE_BPS,
            STRESS_SLIPPAGE_BPS,
        )
        pair_confirmation = _pair_portfolio(pair_confirmation_components, pair["leverage"])
        pair_stress = _pair_portfolio(pair_stress_components, pair["leverage"])
        allocations = {
            "static_anchor": selected["anchor_weight"],
            "funding_spread": Decimal("1") - selected["anchor_weight"],
        }
        confirmation = evaluate_static_portfolio(
            {
                "static_anchor": _evaluate_anchor(anchor, CONFIRMATION, stress=False).daily_returns,
                "funding_spread": pair_confirmation.daily_returns,
            },
            allocations,
            leverage=selected["leverage"],
        )
        stress = evaluate_static_portfolio(
            {
                "static_anchor": _evaluate_anchor(anchor, CONFIRMATION, stress=True).daily_returns,
                "funding_spread": pair_stress.daily_returns,
            },
            allocations,
            leverage=selected["leverage"],
        )
    payload = _report(
        bars,
        pair_rows,
        pair_eligible,
        hybrid_rows,
        hybrid_eligible,
        selected,
        pair_confirmation,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"funding-spread-factor-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _candidate_library() -> tuple[FundingSpreadCandidate, ...]:
    return tuple(
        FundingSpreadCandidate(lookback, threshold, mode, hold, confirmation)
        for lookback in (6, 18, 42, 90)
        for threshold in tuple(Decimal(value) for value in ("0", "0.5", "1", "2", "5"))
        for mode in ("carry", "crowding_follow")
        for hold in (6, 18, 42)
        for confirmation in (1, 2)
    )


def _evaluate_pair_components(
    bars: dict[str, list[ResearchBar]],
    funding: dict[str, list[list[Any]]],
    targets: tuple[tuple[Decimal, ...], tuple[Decimal, ...]],
    period: tuple[int, int],
    fee_bps: Decimal,
    slippage_bps: Decimal,
) -> dict[str, ResearchResult]:
    return {
        asset: evaluate_weighted_targets(
            bars[asset],
            targets[index],
            start_ms=period[0],
            end_ms=period[1],
            funding=funding[asset],
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        for index, asset in enumerate(("btc_perp", "eth_perp"))
    }


def _pair_portfolio(components: dict[str, ResearchResult], leverage: Decimal) -> PortfolioResult:
    return evaluate_static_portfolio(
        {name: decimal_returns(result.daily_returns) for name, result in components.items()},
        {"btc_perp": Decimal("0.5"), "eth_perp": Decimal("0.5")},
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
    validation = results["validation"]
    return (
        min(discovery.target_month_rate, validation.target_month_rate),
        discovery.target_month_rate + validation.target_month_rate,
        min(discovery.positive_month_rate, validation.positive_month_rate),
        min(discovery.worst_month, validation.worst_month),
        min(discovery.net_return, validation.net_return),
        min(discovery.max_drawdown, validation.max_drawdown),
    )


def _report(
    bars: dict[str, list[ResearchBar]],
    pair_rows: list[dict[str, Any]],
    pair_eligible: list[dict[str, Any]],
    hybrid_rows: list[dict[str, Any]],
    hybrid_eligible: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    pair_confirmation: PortfolioResult | None,
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
        pair = selected["pair"]
        selected_payload = {
            "candidate": pair["candidate"].as_dict(),
            "pair_leverage": float(pair["leverage"]),
            "anchor_weight": float(selected["anchor_weight"]),
            "outer_leverage": float(selected["leverage"]),
            **{name: result.as_dict() for name, result in selected["results"].items()},
        }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTC/ETH funding-spread factor and static-anchor hybrid",
        "data": {
            "first_bar": _timestamp(max(item[0].start_ms for item in bars.values())),
            "last_bar": _timestamp(min(item[-1].end_ms for item in bars.values())),
        },
        "execution": {
            "signal": "trailing closed-bar BTC-minus-ETH realized funding spread",
            "fill": "next 4h open",
            "historical_funding": True,
            "base_cost_bps": [float(BASE_FEE_BPS), float(BASE_SLIPPAGE_BPS)],
            "stress_cost_bps": [float(STRESS_FEE_BPS), float(STRESS_SLIPPAGE_BPS)],
        },
        "anchor": {
            "allocations": {name: float(value) for name, value in ANCHOR_ALLOCATIONS.items()},
            "internal_leverage": float(ANCHOR_LEVERAGE),
            "frozen_before_search": True,
        },
        "selection": {
            "pair_configuration_count": len(pair_rows),
            "pair_eligible_count": len(pair_eligible),
            "hybrid_configuration_count": len(hybrid_rows),
            "hybrid_eligible_count": len(hybrid_eligible),
            "confirmation_used_for_selection": False,
            "selected": selected_payload,
        },
        "pair_confirmation": pair_confirmation.as_dict() if pair_confirmation else None,
        "confirmation": confirmation.as_dict(include_daily=True) if confirmation else None,
        "stress_confirmation": stress.as_dict() if stress else None,
        "target": {"monthly_return": 0.25, "minimum_target_month_rate": 0.5, "achieved": achieved},
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The funding-spread hybrid met reused confirmation gates; fresh forward evidence "
                "remains required."
                if achieved
                else "No development-selected funding-spread hybrid passed monthly coverage, "
                "drawdown, and stress gates in reused confirmation."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "No spot basis data is available; this is realized funding spread, not cash-and-carry.",
            "Portfolio drawdown is measured at daily closes.",
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
        "Research-only BTC/ETH funding-spread factor and static-anchor hybrid.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Pair eligible: `{payload['selection']['pair_eligible_count']}` / "
        f"`{payload['selection']['pair_configuration_count']}`; hybrid eligible: "
        f"`{payload['selection']['hybrid_eligible_count']}` / "
        f"`{payload['selection']['hybrid_configuration_count']}`.",
    ]
    if selected:
        lines.extend(
            [
                f"Selected `{selected['candidate']['id']}`, pair leverage "
                f"`{selected['pair_leverage']:.2f}x`, anchor weight "
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
