#!/usr/bin/env python3
"""Search development-selected static three- and four-sleeve BTC/ETH portfolios."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from itertools import combinations, permutations
from math import comb
from pathlib import Path
from typing import Any

from mine_adaptive_factor_portfolio import (
    UNIVERSE_SIZE,
    DiscoverySleeve,
    UniverseSleeve,
    _candidate_group,
    _discovery_eligible,
    _discovery_score,
    _diverse_universe,
)
from mine_factor_portfolio import (
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    CONFIRMATION,
    DISCOVERY,
    LEAD_CANDIDATE,
    LEAD_SIZING,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    VALIDATION,
    SleeveCandidate,
    _candidate_library,
    _evaluate_candidate,
    _evaluate_lead,
    _event_candidate_library,
    _labels,
    _period,
    _require_aligned_bars,
    _research_summary,
    _timestamp,
)

from mastermind_tick.bar_research import ResearchBar, ResearchResult, aggregate_bars, funding_by_bar
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_portfolio import (
    DailyReturns,
    decimal_returns,
    monthly_returns,
    return_correlation,
)
from mastermind_tick.lead_lag_factor import causal_shock_scores, shock_targets, shock_weight_targets
from mastermind_tick.static_factor_portfolio import (
    StaticPortfolioConfig,
    development_eligible,
    development_score,
    evaluate_static_config,
    static_weight_grid,
)

LEAD_NAME = "lead_lag"
SHORTLIST_BY_SIZE = {3: 120, 4: 40}
PRELIMINARY_LEVERAGES = tuple(Decimal(value) for value in ("3", "3.5", "4", "4.5"))
DETAILED_LEVERAGES = tuple(
    Decimal(value) for value in ("3", "3.25", "3.5", "3.75", "4", "4.25", "4.5")
)
LEAD_WEIGHTS = tuple(Decimal(value) for value in ("0.35", "0.4", "0.5", "0.6"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/static_factor_portfolio/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading BTC/ETH history and constructing the frozen lead-lag sleeve", flush=True)
    btc_source, btc_rates = load_market(args.database, "btc_perp")
    eth_source, eth_rates = load_market(args.database, "eth_perp")
    btc_4h = aggregate_bars(btc_source, 240)
    eth_4h = aggregate_bars(eth_source, 240)
    _require_aligned_bars(btc_4h, eth_4h)
    eth_funding = funding_by_bar(eth_4h, eth_rates)
    btc_scores, eth_scores = causal_shock_scores(btc_4h, eth_4h, 15 * 6)
    lead_targets = shock_weight_targets(
        shock_targets(btc_scores, eth_scores, LEAD_CANDIDATE),
        btc_scores,
        LEAD_SIZING,
    )
    lead_results = {
        split: _evaluate_lead(
            eth_4h,
            eth_funding,
            lead_targets,
            period,
            BASE_FEE_BPS,
            BASE_SLIPPAGE_BPS,
        )
        for split, period in (("discovery", DISCOVERY), ("validation", VALIDATION))
    }

    print("freezing the 40-sleeve universe with 2021-2023 data only", flush=True)
    candidates = [
        *_candidate_library("btc_perp", btc_source, btc_rates),
        *_candidate_library("eth_perp", eth_source, eth_rates),
        *_event_candidate_library(btc_4h, eth_4h, btc_rates, eth_rates),
    ]
    frozen_discovery, discovery_eligible_count = _freeze_universe(
        candidates, lead_results["discovery"]
    )
    if len(frozen_discovery) != UNIVERSE_SIZE:
        raise RuntimeError(f"expected {UNIVERSE_SIZE} frozen sleeves, got {len(frozen_discovery)}")
    universe = tuple(
        UniverseSleeve(
            candidate=row.candidate,
            discovery=row.discovery,
            validation=_evaluate_candidate(row.candidate, VALIDATION),
            discovery_correlation=row.correlation,
        )
        for row in frozen_discovery
    )
    curves = _development_curves(universe, lead_results)
    monthly_curves = {
        split: {name: monthly_returns(rows) for name, rows in split_curves.items()}
        for split, split_curves in curves.items()
    }

    names = tuple(row.candidate.id for row in universe)
    shortlists = {}
    for sleeve_count in (3, 4):
        print(f"screening all {sleeve_count}-sleeve combinations on development data", flush=True)
        shortlists[sleeve_count] = _shortlist_combinations(
            monthly_curves,
            names,
            sleeve_count=sleeve_count,
            limit=SHORTLIST_BY_SIZE[sleeve_count],
        )
        print(
            f"retained {len(shortlists[sleeve_count])} {sleeve_count}-sleeve combinations",
            flush=True,
        )

    print("searching detailed weights and leverage with daily-close risk gates", flush=True)
    eligible = []
    evaluated_configs = 0
    for sleeve_count, combos in shortlists.items():
        for index, names_for_combo in enumerate(combos, start=1):
            for config in _detailed_configs(names_for_combo):
                evaluated_configs += 1
                results = {
                    split: evaluate_static_config(split_curves, config)
                    for split, split_curves in curves.items()
                }
                if development_eligible(results):
                    eligible.append(
                        {
                            "sleeve_count": sleeve_count,
                            "config": config,
                            "results": results,
                            "score": development_score(results),
                        }
                    )
            if index % 10 == 0:
                print(
                    f"detailed {sleeve_count}-sleeve {index}/{len(combos)}; "
                    f"risk-eligible={len(eligible)}",
                    flush=True,
                )
    if not eligible:
        raise RuntimeError("no detailed static portfolio passed both development risk gates")
    ranked = sorted(eligible, key=lambda row: row["score"], reverse=True)
    selected = ranked[0]
    selected_config: StaticPortfolioConfig = selected["config"]
    print(f"selected {selected_config.id}", flush=True)

    candidate_by_id = {row.candidate.id: row.candidate for row in universe}
    best_by_size = {
        sleeve_count: next(row for row in ranked if row["sleeve_count"] == sleeve_count)
        for sleeve_count in (3, 4)
    }
    confirmations_by_size = {
        sleeve_count: _confirm_configuration(
            row["config"],
            candidate_by_id,
            eth_4h,
            eth_funding,
            lead_targets,
        )
        for sleeve_count, row in best_by_size.items()
    }
    selected_confirmation = confirmations_by_size[selected["sleeve_count"]]
    confirmation_components = selected_confirmation["components"]
    confirmation = selected_confirmation["base"]
    stress = selected_confirmation["stress"]
    payload = _report(
        btc_source,
        eth_source,
        candidates,
        discovery_eligible_count,
        frozen_discovery,
        universe,
        shortlists,
        evaluated_configs,
        eligible,
        selected,
        ranked[:20],
        best_by_size,
        confirmations_by_size,
        confirmation_components,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"static-factor-portfolio-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _freeze_universe(
    candidates: list[SleeveCandidate], lead_discovery: ResearchResult
) -> tuple[tuple[DiscoverySleeve, ...], int]:
    lead_curve = decimal_returns(lead_discovery.daily_returns)
    lead_monthly = monthly_returns(lead_curve)
    pool = []
    for index, candidate in enumerate(candidates, start=1):
        discovery = _evaluate_candidate(candidate, DISCOVERY)
        if _discovery_eligible(discovery):
            curve = decimal_returns(discovery.daily_returns)
            if _labels(curve) == _labels(lead_curve):
                correlation = return_correlation(monthly_returns(curve), lead_monthly)
                if abs(correlation) <= Decimal("0.8"):
                    pool.append(
                        DiscoverySleeve(
                            candidate,
                            discovery,
                            correlation,
                            _discovery_score(discovery, correlation),
                        )
                    )
        if index % 200 == 0:
            print(f"discovery sleeve {index}/{len(candidates)}; eligible={len(pool)}", flush=True)
    ranked = sorted(pool, key=lambda row: row.score, reverse=True)
    return tuple(_diverse_universe(ranked, UNIVERSE_SIZE)), len(pool)


def _development_curves(
    universe: tuple[UniverseSleeve, ...], lead_results: dict[str, ResearchResult]
) -> dict[str, dict[str, DailyReturns]]:
    result = {
        split: {LEAD_NAME: decimal_returns(lead_results[split].daily_returns)}
        for split in ("discovery", "validation")
    }
    for row in universe:
        result["discovery"][row.candidate.id] = decimal_returns(row.discovery.daily_returns)
        result["validation"][row.candidate.id] = decimal_returns(row.validation.daily_returns)
    return result


def _shortlist_combinations(
    curves: dict[str, dict[str, DailyReturns]],
    names: tuple[str, ...],
    *,
    sleeve_count: int,
    limit: int,
) -> tuple[tuple[str, ...], ...]:
    rows = []
    secondary_count = sleeve_count - 1
    pattern = (Decimal("1"),) * secondary_count
    for index, secondary_names in enumerate(combinations(names, secondary_count), start=1):
        best_score = None
        for config in static_weight_grid(
            LEAD_NAME,
            secondary_names,
            lead_weights=(Decimal("0.5"),),
            secondary_patterns=(pattern,),
            leverages=PRELIMINARY_LEVERAGES,
        ):
            results = {
                split: evaluate_static_config(split_curves, config)
                for split, split_curves in curves.items()
            }
            if all(not result.bankrupt and result.net_return > 0 for result in results.values()):
                score = development_score(results)
                best_score = score if best_score is None else max(best_score, score)
        if best_score is not None:
            rows.append((best_score, secondary_names))
        if index % 1000 == 0:
            print(f"screened {index} combinations for {sleeve_count} sleeves", flush=True)
    ranked = sorted(rows, key=lambda row: (row[0], row[1]), reverse=True)
    return tuple(names for _score, names in ranked[:limit])


def _detailed_configs(secondary_names: tuple[str, ...]) -> tuple[StaticPortfolioConfig, ...]:
    if len(secondary_names) == 2:
        patterns = tuple(sorted(set(permutations((Decimal("2"), Decimal("1")))))) + (
            (Decimal("1"), Decimal("1")),
        )
    elif len(secondary_names) == 3:
        patterns = (
            (Decimal("1"), Decimal("1"), Decimal("1")),
            *tuple(sorted(set(permutations((Decimal("2"), Decimal("1"), Decimal("1")))))),
            *tuple(sorted(set(permutations((Decimal("3"), Decimal("2"), Decimal("1")))))),
        )
    else:
        raise ValueError("detailed search supports two or three secondary sleeves")
    return static_weight_grid(
        LEAD_NAME,
        secondary_names,
        lead_weights=LEAD_WEIGHTS,
        secondary_patterns=patterns,
        leverages=DETAILED_LEVERAGES,
    )


def _confirmation_components(
    names: tuple[str, ...],
    candidates: dict[str, SleeveCandidate],
    eth_bars: list[ResearchBar],
    eth_funding: list[list[Any]],
    lead_targets: tuple[Decimal | None, ...],
    *,
    fee_bps: Decimal,
    slippage_bps: Decimal,
) -> dict[str, ResearchResult]:
    return {
        LEAD_NAME: _evaluate_lead(
            eth_bars,
            eth_funding,
            lead_targets,
            CONFIRMATION,
            fee_bps,
            slippage_bps,
        ),
        **{
            name: _evaluate_candidate(
                candidates[name],
                CONFIRMATION,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
            for name in names
        },
    }


def _confirm_configuration(
    config: StaticPortfolioConfig,
    candidates: dict[str, SleeveCandidate],
    eth_bars: list[ResearchBar],
    eth_funding: list[list[Any]],
    lead_targets: tuple[Decimal | None, ...],
) -> dict[str, Any]:
    names = tuple(name for name, _weight in config.allocations if name != LEAD_NAME)
    components = _confirmation_components(
        names,
        candidates,
        eth_bars,
        eth_funding,
        lead_targets,
        fee_bps=BASE_FEE_BPS,
        slippage_bps=BASE_SLIPPAGE_BPS,
    )
    stress_components = _confirmation_components(
        names,
        candidates,
        eth_bars,
        eth_funding,
        lead_targets,
        fee_bps=STRESS_FEE_BPS,
        slippage_bps=STRESS_SLIPPAGE_BPS,
    )
    return {
        "components": components,
        "base": evaluate_static_config(
            {name: decimal_returns(result.daily_returns) for name, result in components.items()},
            config,
        ),
        "stress": evaluate_static_config(
            {
                name: decimal_returns(result.daily_returns)
                for name, result in stress_components.items()
            },
            config,
        ),
    }


def _report(
    btc_source: list[ResearchBar],
    eth_source: list[ResearchBar],
    candidates: list[SleeveCandidate],
    discovery_eligible_count: int,
    frozen_discovery: tuple[DiscoverySleeve, ...],
    universe: tuple[UniverseSleeve, ...],
    shortlists: dict[int, tuple[tuple[str, ...], ...]],
    evaluated_configs: int,
    eligible: list[dict[str, Any]],
    selected: dict[str, Any],
    top_rows: list[dict[str, Any]],
    best_by_size: dict[int, dict[str, Any]],
    confirmations_by_size: dict[int, dict[str, Any]],
    confirmation_components: dict[str, ResearchResult],
    confirmation: Any,
    stress: Any,
) -> dict[str, Any]:
    config: StaticPortfolioConfig = selected["config"]
    achieved = bool(
        confirmation.target_month_rate >= Decimal("0.5")
        and confirmation.max_drawdown >= Decimal("-0.35")
        and confirmation.net_return > 0
        and stress.net_return > 0
        and stress.max_drawdown >= Decimal("-0.35")
        and not confirmation.bankrupt
        and not stress.bankrupt
    )
    frozen_by_id = {row.candidate.id: row for row in universe}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "development-selected static three/four-sleeve BTC/ETH factor portfolio",
        "data": {
            "first_bar": _timestamp(max(btc_source[0].start_ms, eth_source[0].start_ms)),
            "last_bar": _timestamp(min(btc_source[-1].end_ms, eth_source[-1].end_ms)),
            "btc_bars_15m": len(btc_source),
            "eth_bars_15m": len(eth_source),
        },
        "periods": {
            "universe_discovery": _period(DISCOVERY),
            "configuration_selection": _period(VALIDATION),
            "confirmation": _period(CONFIRMATION),
        },
        "execution": {
            "signal_timing": "causal signals on closed component bars",
            "fill_timing": "next component bar open",
            "base_fee_bps_per_fill": float(BASE_FEE_BPS),
            "base_slippage_bps_per_fill": float(BASE_SLIPPAGE_BPS),
            "stress_fee_bps_per_fill": float(STRESS_FEE_BPS),
            "stress_slippage_bps_per_fill": float(STRESS_SLIPPAGE_BPS),
            "funding": "historical instrument funding while positioned",
            "portfolio_model": "fixed initial sleeve capital; no daily rebalancing",
            "liquidation_modeled": False,
        },
        "universe": {
            "candidate_count": len(candidates),
            "discovery_eligible_count": discovery_eligible_count,
            "frozen_size_excluding_lead": len(universe),
            "confirmation_used_for_universe": False,
            "family_counts": _family_counts(universe),
        },
        "selection": {
            "combination_counts": {
                "three_sleeve": comb(len(universe), 2),
                "four_sleeve": comb(len(universe), 3),
            },
            "shortlist_by_size": {str(key): value for key, value in SHORTLIST_BY_SIZE.items()},
            "detailed_config_count": evaluated_configs,
            "risk_eligible_count": len(eligible),
            "confirmation_used_for_selection": False,
            "rule": (
                "freeze a family-capped 40-factor universe on 2021-2023; screen every equal-weight "
                "three/four-sleeve combination and shortlist on development monthly endpoints; "
                "search explicit weights and leverage on daily 2021-2025 curves; require positive "
                "returns and daily-close drawdown no worse than 35% in both development splits"
            ),
            "shortlisted_counts": {str(key): len(value) for key, value in shortlists.items()},
        },
        "selected": {
            "sleeve_count": selected["sleeve_count"],
            "config": config.as_dict(),
            "components": [
                {
                    "id": name,
                    "allocation": float(weight),
                    "family": "lead_lag"
                    if name == LEAD_NAME
                    else frozen_by_id[name].candidate.family,
                    "instrument_id": (
                        "eth_perp"
                        if name == LEAD_NAME
                        else frozen_by_id[name].candidate.instrument_id
                    ),
                    "parameters": (
                        LEAD_CANDIDATE.as_dict()
                        if name == LEAD_NAME
                        else frozen_by_id[name].candidate.parameters
                    ),
                }
                for name, weight in config.allocations
            ],
            "discovery": selected["results"]["discovery"].as_dict(),
            "validation": selected["results"]["validation"].as_dict(),
        },
        "best_by_sleeve_count": {
            str(sleeve_count): {
                **_selection_row(row),
                "confirmation": confirmations_by_size[sleeve_count]["base"].as_dict(),
                "stress_confirmation": confirmations_by_size[sleeve_count]["stress"].as_dict(),
            }
            for sleeve_count, row in best_by_size.items()
        },
        "top_development_portfolios": [_selection_row(row) for row in top_rows],
        "confirmation_components": {
            name: _research_summary(result) for name, result in confirmation_components.items()
        },
        "confirmation": confirmation.as_dict(include_daily=True),
        "stress_confirmation": stress.as_dict(),
        "target": {
            "monthly_return": 0.25,
            "minimum_confirmation_target_month_rate": 0.5,
            "achieved": achieved,
        },
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The development-selected static portfolio met the reused confirmation gates; it "
                "still requires genuinely unseen forward evidence before any trading use."
                if achieved
                else "The development-selected static portfolio did not reach 25% in at least four "
                "of eight reused confirmation months while retaining the drawdown and stress gates."
            ),
        },
        "limitations": [
            "2026 has been viewed repeatedly and is confirmation evidence, not a fresh holdout.",
            "Combination shortlisting uses development monthly endpoints before daily risk checks.",
            "Portfolio drawdown is measured at daily closes, not synchronized component bars.",
            "Borrowing cost, cross-margin liquidation, market impact, and exchange failure are "
            "not modeled.",
        ],
    }


def _family_counts(universe: tuple[UniverseSleeve, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in universe:
        group = "/".join(_candidate_group(row.candidate))
        counts[group] = counts.get(group, 0) + 1
    return dict(sorted(counts.items()))


def _selection_row(row: dict[str, Any]) -> dict[str, Any]:
    config: StaticPortfolioConfig = row["config"]
    return {
        "sleeve_count": row["sleeve_count"],
        "config": config.as_dict(),
        "score": [float(value) for value in row["score"]],
        "discovery": row["results"]["discovery"].as_dict(),
        "validation": row["results"]["validation"].as_dict(),
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only development-selected static BTC/ETH factor portfolio.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Selected `{selected['sleeve_count']}` sleeves at "
        f"`{selected['config']['leverage']:.2f}x` portfolio leverage.",
        "",
        "| Sleeve | Weight | Market | Family |",
        "|---|---:|---|---|",
    ]
    lines.extend(
        f"| `{row['id']}` | {row['allocation']:.2%} | {row['instrument_id']} | {row['family']} |"
        for row in selected["components"]
    )
    lines.extend(
        [
            "",
            "| Split | Return | Daily-close max DD | Positive months | 25% months |",
            "|---|---:|---:|---:|---:|",
            _metric_row("2021-2023 discovery", selected["discovery"]),
            _metric_row("2024-2025 validation", selected["validation"]),
            _metric_row("2026 reused confirmation", confirmation),
            _metric_row("2026 stress 10+5 bps", stress),
            "",
            "## Development-selected size comparison",
            "",
            "| Sleeves | Leverage | Discovery | Validation | Confirmation | Max DD | 25% months |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for sleeve_count in (3, 4):
        row = payload["best_by_sleeve_count"][str(sleeve_count)]
        size_confirmation = row["confirmation"]
        target_months = sum(item["return"] >= 0.25 for item in size_confirmation["monthly_returns"])
        lines.append(
            f"| {sleeve_count} | {row['config']['leverage']:.2f}x | "
            f"{row['discovery']['net_return']:.2%} | {row['validation']['net_return']:.2%} | "
            f"{size_confirmation['net_return']:.2%} | {size_confirmation['max_drawdown']:.2%} | "
            f"{target_months}/{len(size_confirmation['monthly_returns'])} |"
        )
    lines.extend(
        [
            "",
            "## 2026 monthly returns",
            "",
            "| Month | Base | Stress |",
            "|---|---:|---:|",
        ]
    )
    stress_by_month = {row["label"]: row["return"] for row in stress["monthly_returns"]}
    lines.extend(
        f"| {row['label']} | {row['return']:.2%} | {stress_by_month[row['label']]:.2%} |"
        for row in confirmation["monthly_returns"]
    )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _metric_row(label: str, row: dict[str, Any]) -> str:
    target_months = sum(item["return"] >= 0.25 for item in row["monthly_returns"])
    total_months = len(row["monthly_returns"])
    return (
        f"| {label} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | "
        f"{row['positive_month_rate']:.2%} | {target_months}/{total_months} |"
    )


if __name__ == "__main__":
    main()
