#!/usr/bin/env python3
"""Mine a causal monthly-rotated portfolio of sparse BTC and ETH factor sleeves."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

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
    _positive_month_rate,
    _require_aligned_bars,
    _research_summary,
    _timestamp,
)

from mastermind_tick.bar_research import ResearchBar, ResearchResult, aggregate_bars, funding_by_bar
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_portfolio import (
    AdaptivePortfolioConfig,
    AdaptivePortfolioResult,
    DailyReturns,
    decimal_returns,
    evaluate_adaptive_portfolio,
    monthly_returns,
    return_correlation,
)
from mastermind_tick.lead_lag_factor import (
    causal_shock_scores,
    shock_targets,
    shock_weight_targets,
)


@dataclass(frozen=True)
class DiscoverySleeve:
    candidate: SleeveCandidate
    discovery: ResearchResult
    correlation: Decimal
    score: tuple[float, ...]


@dataclass(frozen=True)
class UniverseSleeve:
    candidate: SleeveCandidate
    discovery: ResearchResult
    validation: ResearchResult
    discovery_correlation: Decimal


UNIVERSE_SIZE = 40
LOOKBACK_DAYS = (60, 90, 180)
TOP_K_VALUES = (1, 3, 5)
SCORING_OPTIONS = ("return", "calmar")
WEIGHTING_OPTIONS = ("equal", "score")
LEVERAGES = tuple(Decimal(value) for value in ("1", "1.5", "2", "2.5", "3"))
MONTHLY_LOSS_LIMITS = (None, Decimal("0.10"), Decimal("0.15"))
ANCHOR_WEIGHTS = tuple(Decimal(value) for value in ("0", "0.25", "0.5"))
REBALANCE_BPS = Decimal("7")
STRESS_REBALANCE_BPS = Decimal("15")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/adaptive_factor_portfolio/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading BTC/ETH bars and constructing the frozen lead-lag sleeve", flush=True)
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
        "discovery": _evaluate_lead(
            eth_4h,
            eth_funding,
            lead_targets,
            DISCOVERY,
            BASE_FEE_BPS,
            BASE_SLIPPAGE_BPS,
        ),
        "validation": _evaluate_lead(
            eth_4h,
            eth_funding,
            lead_targets,
            VALIDATION,
            BASE_FEE_BPS,
            BASE_SLIPPAGE_BPS,
        ),
    }
    lead_discovery_curve = decimal_returns(lead_results["discovery"].daily_returns)
    lead_discovery_monthly = monthly_returns(lead_discovery_curve)

    print("building the factor universe without using validation or confirmation", flush=True)
    candidates = [
        *_candidate_library("btc_perp", btc_source, btc_rates),
        *_candidate_library("eth_perp", eth_source, eth_rates),
        *_event_candidate_library(btc_4h, eth_4h, btc_rates, eth_rates),
    ]
    discovery_pool = []
    for index, candidate in enumerate(candidates, start=1):
        discovery = _evaluate_candidate(candidate, DISCOVERY)
        if _discovery_eligible(discovery):
            curve = decimal_returns(discovery.daily_returns)
            if _labels(curve) == _labels(lead_discovery_curve):
                correlation = return_correlation(monthly_returns(curve), lead_discovery_monthly)
                if abs(correlation) <= Decimal("0.8"):
                    discovery_pool.append(
                        DiscoverySleeve(
                            candidate,
                            discovery,
                            correlation,
                            _discovery_score(discovery, correlation),
                        )
                    )
        if index % 200 == 0:
            print(
                f"discovery sleeve {index}/{len(candidates)}; eligible={len(discovery_pool)}",
                flush=True,
            )
    ranked_pool = sorted(discovery_pool, key=lambda row: row.score, reverse=True)
    frozen_rows = _diverse_universe(ranked_pool, UNIVERSE_SIZE)
    print(
        f"frozen universe={len(frozen_rows)} from discovery pool={len(discovery_pool)}",
        flush=True,
    )
    if len(frozen_rows) < 5:
        raise RuntimeError("discovery produced too few adaptive factor sleeves")

    universe = []
    for row in frozen_rows:
        universe.append(
            UniverseSleeve(
                candidate=row.candidate,
                discovery=row.discovery,
                validation=_evaluate_candidate(row.candidate, VALIDATION),
                discovery_correlation=row.correlation,
            )
        )
    development_curves = _development_curves(universe, lead_results)
    configs = _config_library()
    print(f"selecting {len(configs):,} causal rotation configurations on 2024-2025", flush=True)
    selection_rows = []
    for index, config in enumerate(configs, start=1):
        result = evaluate_adaptive_portfolio(
            development_curves,
            config,
            start="2024-01-01",
            end="2025-12-31",
        )
        selection_rows.append(
            {"config": config, "result": result, "score": _selection_score(result)}
        )
        if index % 250 == 0:
            print(f"rotation config {index}/{len(configs)}", flush=True)
    eligible = [row for row in selection_rows if _selection_eligible(row["result"])]
    ranked = sorted(eligible or selection_rows, key=lambda row: row["score"], reverse=True)
    selected = ranked[0]
    print(
        f"selection eligible={len(eligible)}; selected={_config_id(selected['config'])}",
        flush=True,
    )

    confirmation_lead = _evaluate_lead(
        eth_4h,
        eth_funding,
        lead_targets,
        CONFIRMATION,
        BASE_FEE_BPS,
        BASE_SLIPPAGE_BPS,
    )
    stress_lead = _evaluate_lead(
        eth_4h,
        eth_funding,
        lead_targets,
        CONFIRMATION,
        STRESS_FEE_BPS,
        STRESS_SLIPPAGE_BPS,
    )
    base_confirmation_results = {
        row.candidate.id: _evaluate_candidate(row.candidate, CONFIRMATION) for row in universe
    }
    stress_confirmation_results = {
        row.candidate.id: _evaluate_candidate(
            row.candidate,
            CONFIRMATION,
            fee_bps=STRESS_FEE_BPS,
            slippage_bps=STRESS_SLIPPAGE_BPS,
        )
        for row in universe
    }
    confirmation_curves = _confirmation_curves(
        universe,
        lead_results["validation"],
        confirmation_lead,
        base_confirmation_results,
    )
    stress_curves = _confirmation_curves(
        universe,
        lead_results["validation"],
        stress_lead,
        stress_confirmation_results,
    )
    confirmation = evaluate_adaptive_portfolio(
        confirmation_curves,
        selected["config"],
        start="2026-01-01",
        end="2026-08-10",
    )
    stress_config = replace(selected["config"], rebalance_bps=STRESS_REBALANCE_BPS)
    stress = evaluate_adaptive_portfolio(
        stress_curves,
        stress_config,
        start="2026-01-01",
        end="2026-08-10",
    )
    payload = _report(
        btc_source,
        eth_source,
        candidates,
        discovery_pool,
        universe,
        lead_results,
        configs,
        eligible,
        ranked[:20],
        selected,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"adaptive-factor-portfolio-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _discovery_eligible(result: ResearchResult) -> bool:
    return bool(
        result.net_return > 0
        and result.max_drawdown >= -0.50
        and result.completed_trades >= 10
        and _positive_month_rate(result) >= 0.35
        and not result.bankrupt
    )


def _discovery_score(result: ResearchResult, correlation: Decimal) -> tuple[float, ...]:
    return (
        _target_month_rate(result),
        _positive_month_rate(result),
        result.net_return,
        result.max_drawdown,
        -float(abs(correlation)),
    )


def _target_month_rate(result: ResearchResult) -> float:
    return sum(value >= 0.25 for _label, value in result.monthly_returns) / len(
        result.monthly_returns
    )


def _diverse_universe(ranked: list[DiscoverySleeve], limit: int) -> list[DiscoverySleeve]:
    selected = []
    group_counts: dict[tuple[str, ...], int] = {}
    for row in ranked:
        group = _candidate_group(row.candidate)
        if group_counts.get(group, 0) >= 5:
            continue
        selected.append(row)
        group_counts[group] = group_counts.get(group, 0) + 1
        if len(selected) == limit:
            break
    return selected


def _candidate_group(candidate: SleeveCandidate) -> tuple[str, ...]:
    if candidate.family == "shock_event":
        return (
            candidate.family,
            str(candidate.parameters["source"]),
            candidate.instrument_id,
            str(candidate.parameters["signal_mode"]),
            str(candidate.parameters["direction"]),
        )
    return (
        candidate.family,
        candidate.instrument_id,
        str(candidate.parameters["direction"]),
    )


def _development_curves(
    universe: list[UniverseSleeve],
    lead_results: dict[str, ResearchResult],
) -> dict[str, DailyReturns]:
    curves = {
        "lead_lag": (
            *decimal_returns(lead_results["discovery"].daily_returns),
            *decimal_returns(lead_results["validation"].daily_returns),
        )
    }
    for row in universe:
        curves[row.candidate.id] = (
            *decimal_returns(row.discovery.daily_returns),
            *decimal_returns(row.validation.daily_returns),
        )
    return curves


def _confirmation_curves(
    universe: list[UniverseSleeve],
    validation_lead: ResearchResult,
    confirmation_lead: ResearchResult,
    confirmation_results: dict[str, ResearchResult],
) -> dict[str, DailyReturns]:
    curves = {
        "lead_lag": (
            *decimal_returns(validation_lead.daily_returns),
            *decimal_returns(confirmation_lead.daily_returns),
        )
    }
    for row in universe:
        curves[row.candidate.id] = (
            *decimal_returns(row.validation.daily_returns),
            *decimal_returns(confirmation_results[row.candidate.id].daily_returns),
        )
    return curves


def _config_library() -> tuple[AdaptivePortfolioConfig, ...]:
    return tuple(
        AdaptivePortfolioConfig(
            lookback_days=lookback,
            top_k=top_k,
            scoring=scoring,
            weighting=weighting,
            leverage=leverage,
            rebalance_bps=REBALANCE_BPS,
            monthly_loss_limit=monthly_loss_limit,
            anchor_name="lead_lag" if anchor_weight else None,
            anchor_weight=anchor_weight,
        )
        for lookback in LOOKBACK_DAYS
        for top_k in TOP_K_VALUES
        for scoring in SCORING_OPTIONS
        for weighting in WEIGHTING_OPTIONS
        for leverage in LEVERAGES
        for monthly_loss_limit in MONTHLY_LOSS_LIMITS
        for anchor_weight in ANCHOR_WEIGHTS
    )


def _selection_eligible(result: AdaptivePortfolioResult) -> bool:
    return bool(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and not result.bankrupt
    )


def _selection_score(result: AdaptivePortfolioResult) -> tuple[Decimal, ...]:
    return (
        result.target_month_rate,
        result.positive_month_rate,
        result.worst_month,
        result.net_return,
        result.max_drawdown,
    )


def _report(
    btc_source: list[ResearchBar],
    eth_source: list[ResearchBar],
    candidates: list[SleeveCandidate],
    discovery_pool: list[DiscoverySleeve],
    universe: list[UniverseSleeve],
    lead_results: dict[str, ResearchResult],
    configs: tuple[AdaptivePortfolioConfig, ...],
    eligible: list[dict[str, Any]],
    top_rows: list[dict[str, Any]],
    selected: dict[str, Any],
    confirmation: AdaptivePortfolioResult,
    stress: AdaptivePortfolioResult,
) -> dict[str, Any]:
    achieved = bool(
        confirmation.target_month_rate >= Decimal("0.5")
        and confirmation.max_drawdown >= Decimal("-0.35")
        and confirmation.net_return > 0
        and stress.net_return > 0
        and stress.max_drawdown >= Decimal("-0.35")
        and not confirmation.bankrupt
        and not stress.bankrupt
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "causal monthly-rotated BTC/ETH factor portfolio",
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
            "signal_timing": "causal component signals on closed bars",
            "component_fill_timing": "next component bar open",
            "component_fee_bps_per_fill": float(BASE_FEE_BPS),
            "component_slippage_bps_per_fill": float(BASE_SLIPPAGE_BPS),
            "monthly_rebalance_bps": float(REBALANCE_BPS),
            "stress_monthly_rebalance_bps": float(STRESS_REBALANCE_BPS),
            "funding": "historical instrument funding while positioned",
            "rotation": "calendar-month open using returns strictly before allocation day",
            "liquidation_modeled": False,
        },
        "universe": {
            "candidate_count": len(candidates),
            "discovery_eligible_count": len(discovery_pool),
            "frozen_size": len(universe) + 1,
            "includes_lead_lag": True,
            "confirmation_used_for_universe": False,
            "sleeves": [_universe_row(row) for row in universe],
        },
        "lead_sleeve": {
            "candidate": LEAD_CANDIDATE.as_dict(),
            "sizing": LEAD_SIZING.as_dict(),
            **{name: _research_summary(result) for name, result in lead_results.items()},
        },
        "selection": {
            "configuration_count": len(configs),
            "eligible_count": len(eligible),
            "used_fallback_diagnostic": not eligible,
            "confirmation_used_for_selection": False,
            "rule": (
                "on 2024-2025 require positive return, at least half of months positive, and "
                "daily-close max drawdown no worse than 35%; rank by 25% month coverage, positive "
                "months, worst month, return, and drawdown"
            ),
            "selected": {
                "id": _config_id(selected["config"]),
                "config": _config_dict(selected["config"]),
                "result": selected["result"].as_dict(include_daily=True),
            },
            "top_configurations": [_selection_row(row) for row in top_rows],
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
                "The adaptive portfolio reached the research return gate, but 2026 is a reused "
                "holdout and forward evidence is required."
                if achieved
                else "The development-selected adaptive portfolio did not pass confirmation "
                "monthly return, drawdown, and cost-stress gates."
            ),
        },
        "limitations": [
            "2026 has been viewed in prior studies and is not a fresh independent holdout.",
            "Joint portfolio drawdown is measured at daily closes, not every component bar.",
            "Monthly allocation turnover is modeled, but cross-margin liquidation is not.",
            "Market impact and exchange failure are not modeled.",
        ],
    }


def _universe_row(row: UniverseSleeve) -> dict[str, Any]:
    return {
        "id": row.candidate.id,
        "instrument_id": row.candidate.instrument_id,
        "family": row.candidate.family,
        "interval_minutes": row.candidate.interval_minutes,
        "parameters": row.candidate.parameters,
        "discovery_correlation": float(row.discovery_correlation),
        "discovery": _research_summary(row.discovery),
        "validation": _research_summary(row.validation),
    }


def _config_dict(config: AdaptivePortfolioConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload["leverage"] = float(config.leverage)
    payload["rebalance_bps"] = float(config.rebalance_bps)
    payload["monthly_loss_limit"] = (
        float(config.monthly_loss_limit) if config.monthly_loss_limit is not None else None
    )
    payload["anchor_weight"] = float(config.anchor_weight)
    return payload


def _config_id(config: AdaptivePortfolioConfig) -> str:
    loss = (
        f"{config.monthly_loss_limit:g}".replace(".", "p")
        if config.monthly_loss_limit is not None
        else "none"
    )
    anchor = f"{config.anchor_weight:g}".replace(".", "p")
    leverage = f"{config.leverage:g}".replace(".", "p")
    return (
        f"adaptive-{config.lookback_days}d-top-{config.top_k}-{config.scoring}-"
        f"{config.weighting}-leverage-{leverage}-loss-{loss}-anchor-{anchor}"
    )


def _selection_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _config_id(row["config"]),
        "config": _config_dict(row["config"]),
        "score": [float(value) for value in row["score"]],
        "result": row["result"].as_dict(),
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    selection = selected["result"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only causal monthly-rotated factor portfolio.",
        "",
        f"Decision: `{payload['decision']['status']}`.",
        f"Universe candidates: `{payload['universe']['candidate_count']:,}`; discovery eligible: "
        f"`{payload['universe']['discovery_eligible_count']:,}`; frozen sleeves including "
        f"lead-lag: `{payload['universe']['frozen_size']}`.",
        "",
        f"Selected: `{selected['id']}`.",
        "",
        "| Split | Return | Daily-close max DD | Positive months | 25% months | Rebalance costs |",
        "|---|---:|---:|---:|---:|---:|",
        _markdown_row("selection", selection),
        _markdown_row("confirmation", confirmation),
        _markdown_row("stress confirmation", stress),
        "",
        "## Confirmation monthly returns",
        "",
        "| Month | Return |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {row['label']} | {row['return']:.2%} |" for row in confirmation["monthly_returns"]
    )
    lines.extend(
        [
            "",
            "## Confirmation allocations",
            "",
            "| Month | Sleeves | Turnover | Cost |",
            "|---|---|---:|---:|",
        ]
    )
    for row in confirmation["allocation_history"]:
        sleeves = ", ".join(f"{name} {weight:.0%}" for name, weight in row["weights"].items())
        lines.append(
            f"| {row['month']} | {sleeves or 'cash'} | {row['turnover']:.2f}x | {row['cost']:.2f} |"
        )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _markdown_row(label: str, row: dict[str, Any]) -> str:
    return (
        f"| {label} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | "
        f"{row['positive_month_rate']:.2%} | {row['target_25pct_month_rate']:.2%} | "
        f"{row['rebalance_costs']:.2f} |"
    )


if __name__ == "__main__":
    main()
