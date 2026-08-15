#!/usr/bin/env python3
"""Search static three-to-five-sleeve portfolios across every discovery-eligible factor."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from itertools import permutations
from pathlib import Path
from typing import Any

from mine_adaptive_factor_portfolio import (
    DiscoverySleeve,
    UniverseSleeve,
    _candidate_group,
    _discovery_eligible,
    _discovery_score,
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
from mine_static_factor_portfolio import LEAD_NAME

from mastermind_tick.bar_research import ResearchBar, ResearchResult, aggregate_bars, funding_by_bar
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_portfolio import (
    DailyReturns,
    PortfolioResult,
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

BEAM_WIDTH_BY_SLEEVE_COUNT = {2: 60, 3: 60, 4: 40, 5: 30}
DETAILED_SET_COUNT = 20
PRELIMINARY_LEAD_WEIGHTS = tuple(Decimal(value) for value in ("0.3", "0.4", "0.5", "0.6"))
PRELIMINARY_LEVERAGES = tuple(Decimal(value) for value in ("2.5", "3", "3.5", "4", "4.5"))
DETAILED_LEAD_WEIGHTS = tuple(Decimal(value) for value in ("0.3", "0.35", "0.4", "0.5", "0.6"))
DETAILED_LEVERAGES = tuple(
    Decimal(value) for value in ("3", "3.25", "3.5", "3.75", "4", "4.25", "4.5", "4.75", "5")
)
THREE_SLEEVE_BASELINE = (
    "event-eth_perp-to-eth_perp-continuation-60d-threshold-2p5-hold-12x4h-none-long_only",
    "event-btc_perp-to-btc_perp-continuation-15d-threshold-2-hold-4x4h-none-long_short",
)
FOUR_SLEEVE_BASELINE = (
    *THREE_SLEEVE_BASELINE,
    "event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-hold-12x4h-underreaction-long_short",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/expanded_factor_portfolio/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading BTC/ETH history and constructing the lead-lag anchor", flush=True)
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

    candidates = [
        *_candidate_library("btc_perp", btc_source, btc_rates),
        *_candidate_library("eth_perp", eth_source, eth_rates),
        *_event_candidate_library(btc_4h, eth_4h, btc_rates, eth_rates),
    ]
    print("selecting the full discovery-eligible universe", flush=True)
    discovery_pool = _discovery_pool(candidates, lead_results["discovery"])
    universe = tuple(
        UniverseSleeve(
            candidate=row.candidate,
            discovery=row.discovery,
            validation=_evaluate_candidate(row.candidate, VALIDATION),
            discovery_correlation=row.correlation,
        )
        for row in discovery_pool
    )
    print(f"expanded universe contains {len(universe)} sleeves", flush=True)
    curves = _development_curves(universe, lead_results)
    monthly_curves = {
        split: {name: monthly_returns(rows) for name, rows in split_curves.items()}
        for split, split_curves in curves.items()
    }

    beam_by_size = _beam_search(monthly_curves, tuple(row.candidate.id for row in universe))
    detailed_sets = {
        sleeve_count: _with_baseline(
            tuple(row["secondary_names"] for row in rows[:DETAILED_SET_COUNT]),
            sleeve_count,
        )
        for sleeve_count, rows in beam_by_size.items()
        if sleeve_count >= 3
    }
    print("running daily-close development risk checks", flush=True)
    eligible = []
    detailed_config_count = 0
    for sleeve_count, candidate_sets in detailed_sets.items():
        for index, secondary_names in enumerate(candidate_sets, start=1):
            for config in _detailed_configs(secondary_names):
                detailed_config_count += 1
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
            if index % 5 == 0:
                print(
                    f"daily {sleeve_count}-sleeve {index}/{len(candidate_sets)}; "
                    f"eligible={len(eligible)}",
                    flush=True,
                )
    if not eligible:
        raise RuntimeError("expanded search produced no development risk-eligible portfolio")
    ranked = sorted(eligible, key=lambda row: row["score"], reverse=True)
    selected = ranked[0]
    selected_config: StaticPortfolioConfig = selected["config"]
    print(f"selected {selected_config.id}", flush=True)

    candidates_by_id = {row.candidate.id: row.candidate for row in universe}
    confirmation_components = _confirmation_components(
        selected_config,
        candidates_by_id,
        eth_4h,
        eth_funding,
        lead_targets,
        BASE_FEE_BPS,
        BASE_SLIPPAGE_BPS,
    )
    stress_components = _confirmation_components(
        selected_config,
        candidates_by_id,
        eth_4h,
        eth_funding,
        lead_targets,
        STRESS_FEE_BPS,
        STRESS_SLIPPAGE_BPS,
    )
    confirmation = evaluate_static_config(
        {
            name: decimal_returns(result.daily_returns)
            for name, result in confirmation_components.items()
        },
        selected_config,
    )
    stress = evaluate_static_config(
        {name: decimal_returns(result.daily_returns) for name, result in stress_components.items()},
        selected_config,
    )
    payload = _report(
        btc_source,
        eth_source,
        candidates,
        universe,
        beam_by_size,
        detailed_sets,
        detailed_config_count,
        eligible,
        selected,
        ranked[:20],
        confirmation_components,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"expanded-factor-portfolio-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _discovery_pool(
    candidates: list[SleeveCandidate], lead_discovery: ResearchResult
) -> tuple[DiscoverySleeve, ...]:
    lead_curve = decimal_returns(lead_discovery.daily_returns)
    lead_monthly = monthly_returns(lead_curve)
    rows = []
    for index, candidate in enumerate(candidates, start=1):
        discovery = _evaluate_candidate(candidate, DISCOVERY)
        if _discovery_eligible(discovery):
            curve = decimal_returns(discovery.daily_returns)
            if _labels(curve) == _labels(lead_curve):
                correlation = return_correlation(monthly_returns(curve), lead_monthly)
                if abs(correlation) <= Decimal("0.8"):
                    rows.append(
                        DiscoverySleeve(
                            candidate,
                            discovery,
                            correlation,
                            _discovery_score(discovery, correlation),
                        )
                    )
        if index % 200 == 0:
            print(f"discovery {index}/{len(candidates)}; eligible={len(rows)}", flush=True)
    return tuple(sorted(rows, key=lambda row: row.score, reverse=True))


def _development_curves(
    universe: tuple[UniverseSleeve, ...], lead_results: dict[str, ResearchResult]
) -> dict[str, dict[str, DailyReturns]]:
    curves = {
        split: {LEAD_NAME: decimal_returns(lead_results[split].daily_returns)}
        for split in ("discovery", "validation")
    }
    for row in universe:
        curves["discovery"][row.candidate.id] = decimal_returns(row.discovery.daily_returns)
        curves["validation"][row.candidate.id] = decimal_returns(row.validation.daily_returns)
    return curves


def _beam_search(
    monthly_curves: dict[str, dict[str, DailyReturns]], names: tuple[str, ...]
) -> dict[int, list[dict[str, Any]]]:
    parents: tuple[tuple[str, ...], ...] = ((),)
    result = {}
    for sleeve_count in range(2, 6):
        candidate_sets = {
            tuple(sorted((*parent, name)))
            for parent in parents
            for name in names
            if name not in parent
        }
        print(
            f"beam screening {len(candidate_sets):,} sets for {sleeve_count} sleeves",
            flush=True,
        )
        rows = []
        for index, secondary_names in enumerate(sorted(candidate_sets), start=1):
            best = _best_preliminary(monthly_curves, secondary_names)
            if best is not None:
                rows.append(
                    {
                        "secondary_names": secondary_names,
                        "config": best["config"],
                        "results": best["results"],
                        "score": best["score"],
                    }
                )
            if index % 2000 == 0:
                print(f"beam {sleeve_count}: {index}/{len(candidate_sets)}", flush=True)
        ranked = sorted(rows, key=lambda row: row["score"], reverse=True)
        result[sleeve_count] = ranked[: BEAM_WIDTH_BY_SLEEVE_COUNT[sleeve_count]]
        parents = tuple(row["secondary_names"] for row in result[sleeve_count])
        print(f"beam retained {len(parents)} sets for {sleeve_count} sleeves", flush=True)
    return result


def _best_preliminary(
    curves: dict[str, dict[str, DailyReturns]], secondary_names: tuple[str, ...]
) -> dict[str, Any] | None:
    pattern = (Decimal("1"),) * len(secondary_names)
    rows = []
    for config in static_weight_grid(
        LEAD_NAME,
        secondary_names,
        lead_weights=PRELIMINARY_LEAD_WEIGHTS,
        secondary_patterns=(pattern,),
        leverages=PRELIMINARY_LEVERAGES,
    ):
        results = {
            split: evaluate_static_config(split_curves, config)
            for split, split_curves in curves.items()
        }
        if all(
            not result.bankrupt
            and result.net_return > 0
            and result.max_drawdown >= Decimal("-0.50")
            for result in results.values()
        ):
            rows.append({"config": config, "results": results, "score": development_score(results)})
    return max(rows, key=lambda row: row["score"]) if rows else None


def _with_baseline(
    candidate_sets: tuple[tuple[str, ...], ...], sleeve_count: int
) -> tuple[tuple[str, ...], ...]:
    baselines = {
        3: THREE_SLEEVE_BASELINE,
        4: FOUR_SLEEVE_BASELINE,
    }
    result = list(candidate_sets)
    baseline = baselines.get(sleeve_count)
    if baseline is not None and baseline not in result:
        result.append(baseline)
    return tuple(result)


def _detailed_configs(secondary_names: tuple[str, ...]) -> tuple[StaticPortfolioConfig, ...]:
    count = len(secondary_names)
    patterns: tuple[tuple[Decimal, ...], ...]
    if count == 2:
        patterns = (
            (Decimal("1"), Decimal("1")),
            *tuple(sorted(set(permutations((Decimal("2"), Decimal("1")))))),
        )
    elif count == 3:
        patterns = (
            (Decimal("1"),) * 3,
            *tuple(sorted(set(permutations((Decimal("2"), Decimal("1"), Decimal("1")))))),
            *tuple(sorted(set(permutations((Decimal("3"), Decimal("2"), Decimal("1")))))),
        )
    elif count == 4:
        patterns = (
            (Decimal("1"),) * 4,
            *tuple(
                sorted(set(permutations((Decimal("2"), Decimal("1"), Decimal("1"), Decimal("1")))))
            ),
        )
    else:
        raise ValueError("expanded detailed search supports two to four secondary sleeves")
    return static_weight_grid(
        LEAD_NAME,
        secondary_names,
        lead_weights=DETAILED_LEAD_WEIGHTS,
        secondary_patterns=patterns,
        leverages=DETAILED_LEVERAGES,
    )


def _confirmation_components(
    config: StaticPortfolioConfig,
    candidates: dict[str, SleeveCandidate],
    eth_bars: list[ResearchBar],
    eth_funding: list[list[Any]],
    lead_targets: tuple[Decimal | None, ...],
    fee_bps: Decimal,
    slippage_bps: Decimal,
) -> dict[str, ResearchResult]:
    names = tuple(name for name, _weight in config.allocations if name != LEAD_NAME)
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


def _report(
    btc_source: list[ResearchBar],
    eth_source: list[ResearchBar],
    candidates: list[SleeveCandidate],
    universe: tuple[UniverseSleeve, ...],
    beam_by_size: dict[int, list[dict[str, Any]]],
    detailed_sets: dict[int, tuple[tuple[str, ...], ...]],
    detailed_config_count: int,
    eligible: list[dict[str, Any]],
    selected: dict[str, Any],
    top_rows: list[dict[str, Any]],
    confirmation_components: dict[str, ResearchResult],
    confirmation: PortfolioResult,
    stress: PortfolioResult,
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
    universe_by_id = {row.candidate.id: row for row in universe}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "expanded-universe static BTC/ETH factor portfolio",
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
            "discovery_eligible_count": len(universe),
            "confirmation_used_for_universe": False,
            "family_counts": _family_counts(universe),
        },
        "selection": {
            "method": "development-only beam search followed by daily-close exact replay",
            "beam_width_by_sleeve_count": BEAM_WIDTH_BY_SLEEVE_COUNT,
            "beam_retained_counts": {str(key): len(value) for key, value in beam_by_size.items()},
            "detailed_set_counts": {str(key): len(value) for key, value in detailed_sets.items()},
            "detailed_config_count": detailed_config_count,
            "risk_eligible_count": len(eligible),
            "confirmation_used_for_selection": False,
            "rule": (
                "freeze every low-correlation discovery-eligible sleeve on 2021-2023; expand "
                "candidate sets with a development-only beam; select explicit weights and leverage "
                "using positive returns and daily-close drawdown no worse than 35% in both "
                "2021-2023 discovery and 2024-2025 validation"
            ),
        },
        "selected": {
            "sleeve_count": selected["sleeve_count"],
            "config": config.as_dict(),
            "components": [
                {
                    "id": name,
                    "allocation": float(weight),
                    "family": (
                        "lead_lag" if name == LEAD_NAME else universe_by_id[name].candidate.family
                    ),
                    "instrument_id": (
                        "eth_perp"
                        if name == LEAD_NAME
                        else universe_by_id[name].candidate.instrument_id
                    ),
                    "parameters": (
                        LEAD_CANDIDATE.as_dict()
                        if name == LEAD_NAME
                        else universe_by_id[name].candidate.parameters
                    ),
                }
                for name, weight in config.allocations
            ],
            "discovery": selected["results"]["discovery"].as_dict(),
            "validation": selected["results"]["validation"].as_dict(),
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
                "The expanded development-selected portfolio met the reused confirmation gates; "
                "genuinely unseen forward evidence is still required before trading use."
                if achieved
                else "The expanded development-selected portfolio failed the reused confirmation "
                "monthly coverage, drawdown, or stress-cost gate."
            ),
        },
        "limitations": [
            "2026 has been viewed repeatedly and is confirmation evidence, not a fresh holdout.",
            "Beam search is deterministic but does not enumerate every possible multi-sleeve set.",
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
        "Research-only expanded-universe static BTC/ETH factor portfolio.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        "Discovery-eligible universe: "
        f"`{payload['universe']['discovery_eligible_count']}` sleeves.",
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
    return (
        f"| {label} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | "
        f"{row['positive_month_rate']:.2%} | {target_months}/{len(row['monthly_returns'])} |"
    )


if __name__ == "__main__":
    main()
