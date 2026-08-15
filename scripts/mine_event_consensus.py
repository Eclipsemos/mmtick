#!/usr/bin/env python3
"""Mine de-duplicated BTC/ETH shock-event consensus and crowd-fade factors."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mine_factor_portfolio import (
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    CONFIRMATION,
    DISCOVERY,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    VALIDATION,
    SleeveCandidate,
    _evaluate_candidate,
    _event_candidate_library,
    _period,
    _positive_month_rate,
    _research_summary,
    _timestamp,
)

from mastermind_tick.bar_research import ResearchBar, ResearchResult, aggregate_bars, funding_by_bar
from mastermind_tick.event_consensus import ConsensusConfig, consensus_targets
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_portfolio import (
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
)
from mastermind_tick.lead_lag_factor import evaluate_weighted_targets
from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class Representative:
    candidate: SleeveCandidate
    discovery: ResearchResult
    group: tuple[str, ...]


@dataclass(frozen=True)
class ConsensusReplayConfig:
    consensus: ConsensusConfig
    monthly_loss_limit: Decimal

    @property
    def id(self) -> str:
        agreement = f"{self.consensus.minimum_agreement:g}".replace(".", "p")
        exposure = f"{self.consensus.exposure:g}".replace(".", "p")
        loss = f"{self.monthly_loss_limit:g}".replace(".", "p")
        return (
            f"{self.consensus.mode}-active-{self.consensus.minimum_active}-agreement-"
            f"{agreement}-exposure-{exposure}-loss-{loss}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            **self.consensus.as_dict(),
            "monthly_loss_limit": float(self.monthly_loss_limit),
        }


AGREEMENTS = tuple(Decimal(value) for value in ("0.5", "0.67", "0.8", "1"))
EXPOSURES = tuple(Decimal(value) for value in ("0.5", "1", "1.5", "2", "3", "4", "5"))
MONTHLY_LOSS_LIMITS = tuple(Decimal(value) for value in ("0.05", "0.10", "0.15"))
PORTFOLIO_ALLOCATIONS = tuple(Decimal(value) for value in ("0", "0.25", "0.5", "0.75", "1"))
PORTFOLIO_LEVERAGES = tuple(Decimal(value) for value in ("1", "1.25", "1.5", "2"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/event_consensus/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading aligned BTC/ETH 4h bars and funding", flush=True)
    btc_source, btc_rates = load_market(args.database, "btc_perp")
    eth_source, eth_rates = load_market(args.database, "eth_perp")
    bars = {
        "btc_perp": aggregate_bars(btc_source, 240),
        "eth_perp": aggregate_bars(eth_source, 240),
    }
    _require_aligned(bars["btc_perp"], bars["eth_perp"])
    funding = {
        "btc_perp": funding_by_bar(bars["btc_perp"], btc_rates),
        "eth_perp": funding_by_bar(bars["eth_perp"], eth_rates),
    }
    event_candidates = _event_candidate_library(
        bars["btc_perp"], bars["eth_perp"], btc_rates, eth_rates
    )
    print(f"selecting group representatives from {len(event_candidates):,} events", flush=True)
    grouped: dict[tuple[str, ...], list[tuple[SleeveCandidate, ResearchResult]]] = {}
    for candidate in event_candidates:
        discovery = _evaluate_candidate(candidate, DISCOVERY)
        grouped.setdefault(_event_group(candidate), []).append((candidate, discovery))
    representatives = []
    rejected_groups = []
    for group, rows in sorted(grouped.items()):
        candidate, result = max(rows, key=lambda row: _representative_score(row[1]))
        if _representative_eligible(result):
            representatives.append(Representative(candidate, result, group))
        else:
            rejected_groups.append(group)
    by_instrument = {
        instrument: [row for row in representatives if row.candidate.instrument_id == instrument]
        for instrument in ("btc_perp", "eth_perp")
    }
    print(
        "representatives "
        + ", ".join(f"{name}={len(rows)}" for name, rows in by_instrument.items()),
        flush=True,
    )
    if any(len(rows) < 3 for rows in by_instrument.values()):
        raise RuntimeError("too few positive event groups for consensus research")

    component_search = {}
    for instrument, members in by_instrument.items():
        configs = _config_library(len(members))
        print(f"searching {len(configs):,} {instrument} consensus configurations", flush=True)
        rows = _component_search(
            bars[instrument],
            funding[instrument],
            members,
            configs,
        )
        eligible = [row for row in rows if _component_eligible(row["result"])]
        ranked = sorted(eligible or rows, key=lambda row: row["score"], reverse=True)
        component_search[instrument] = {
            "configs": configs,
            "rows": rows,
            "eligible": eligible,
            "ranked": ranked,
            "selected": ranked[0],
        }
        print(
            f"{instrument} eligible={len(eligible)} selected={ranked[0]['config'].id}",
            flush=True,
        )

    portfolio_rows = _portfolio_search(component_search)
    eligible_portfolios = [row for row in portfolio_rows if _portfolio_eligible(row["result"])]
    ranked_portfolios = sorted(
        eligible_portfolios or portfolio_rows,
        key=lambda row: row["score"],
        reverse=True,
    )
    selected_portfolio = ranked_portfolios[0]
    print(
        "portfolio selected "
        f"btc={selected_portfolio['btc_allocation']} "
        f"leverage={selected_portfolio['leverage']}",
        flush=True,
    )

    confirmation_components = {}
    stress_components = {}
    for instrument in ("btc_perp", "eth_perp"):
        selected = component_search[instrument]["selected"]
        confirmation_components[instrument] = _evaluate_consensus(
            bars[instrument],
            funding[instrument],
            selected["targets"],
            selected["config"],
            CONFIRMATION,
            BASE_FEE_BPS,
            BASE_SLIPPAGE_BPS,
        )
        stress_components[instrument] = _evaluate_consensus(
            bars[instrument],
            funding[instrument],
            selected["targets"],
            selected["config"],
            CONFIRMATION,
            STRESS_FEE_BPS,
            STRESS_SLIPPAGE_BPS,
        )
    confirmation = _combine_components(
        confirmation_components,
        selected_portfolio["btc_allocation"],
        selected_portfolio["leverage"],
    )
    stress = _combine_components(
        stress_components,
        selected_portfolio["btc_allocation"],
        selected_portfolio["leverage"],
    )
    payload = _report(
        btc_source,
        eth_source,
        event_candidates,
        grouped,
        rejected_groups,
        by_instrument,
        component_search,
        portfolio_rows,
        eligible_portfolios,
        ranked_portfolios[:20],
        selected_portfolio,
        confirmation_components,
        stress_components,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"event-consensus-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
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


def _event_group(candidate: SleeveCandidate) -> tuple[str, ...]:
    parameters = candidate.parameters
    return (
        str(parameters["source"]),
        candidate.instrument_id,
        str(parameters["signal_mode"]),
        str(parameters["direction"]),
        str(parameters["response_gate"]),
    )


def _representative_eligible(result: ResearchResult) -> bool:
    # A weak standalone sleeve can still contribute useful disagreement information.
    # Discovery filtering therefore enforces sample sufficiency, not profitability.
    return bool(result.completed_trades >= 8 and not result.bankrupt)


def _representative_score(result: ResearchResult) -> tuple[float, ...]:
    return (
        _target_month_rate(result),
        _positive_month_rate(result),
        result.net_return,
        result.max_drawdown,
    )


def _config_library(member_count: int) -> tuple[ConsensusReplayConfig, ...]:
    return tuple(
        ConsensusReplayConfig(
            ConsensusConfig(minimum_active, agreement, mode, exposure),
            monthly_loss_limit,
        )
        for minimum_active in range(1, min(6, member_count) + 1)
        for agreement in AGREEMENTS
        for mode in ("follow", "fade")
        for exposure in EXPOSURES
        for monthly_loss_limit in MONTHLY_LOSS_LIMITS
    )


def _component_search(
    bars: list[ResearchBar],
    funding: list[list[FundingRate]],
    members: list[Representative],
    configs: tuple[ConsensusReplayConfig, ...],
) -> list[dict[str, Any]]:
    target_cache = {}
    rows = []
    member_targets = tuple(row.candidate.targets for row in members)
    for config in configs:
        key = config.consensus
        targets = target_cache.setdefault(
            key,
            consensus_targets(member_targets, config.consensus),
        )
        result = _evaluate_consensus(
            bars,
            funding,
            targets,
            config,
            VALIDATION,
            BASE_FEE_BPS,
            BASE_SLIPPAGE_BPS,
        )
        rows.append(
            {
                "config": config,
                "targets": targets,
                "result": result,
                "score": _component_score(result),
            }
        )
    return rows


def _evaluate_consensus(
    bars: list[ResearchBar],
    funding: list[list[FundingRate]],
    targets: tuple[Decimal | None, ...],
    config: ConsensusReplayConfig,
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
        monthly_loss_limit=config.monthly_loss_limit,
    )


def _component_eligible(result: ResearchResult) -> bool:
    return bool(
        result.net_return > 0
        and result.max_drawdown >= -0.35
        and result.completed_trades >= 8
        and _positive_month_rate(result) >= 0.5
        and not result.bankrupt
    )


def _component_score(result: ResearchResult) -> tuple[float, ...]:
    monthly = [value for _label, value in result.monthly_returns]
    return (
        _target_month_rate(result),
        _positive_month_rate(result),
        min(monthly),
        result.net_return,
        result.max_drawdown,
    )


def _portfolio_search(component_search: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    results = {
        instrument: component_search[instrument]["selected"]["result"]
        for instrument in ("btc_perp", "eth_perp")
    }
    rows = []
    for btc_allocation in PORTFOLIO_ALLOCATIONS:
        for leverage in PORTFOLIO_LEVERAGES:
            result = _combine_components(results, btc_allocation, leverage)
            rows.append(
                {
                    "btc_allocation": btc_allocation,
                    "leverage": leverage,
                    "result": result,
                    "score": _portfolio_score(result),
                }
            )
    return rows


def _combine_components(
    results: dict[str, ResearchResult],
    btc_allocation: Decimal,
    leverage: Decimal,
) -> PortfolioResult:
    return evaluate_static_portfolio(
        {
            "btc_perp": decimal_returns(results["btc_perp"].daily_returns),
            "eth_perp": decimal_returns(results["eth_perp"].daily_returns),
        },
        {
            "btc_perp": btc_allocation,
            "eth_perp": Decimal("1") - btc_allocation,
        },
        leverage=leverage,
    )


def _portfolio_eligible(result: PortfolioResult) -> bool:
    return bool(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and not result.bankrupt
    )


def _portfolio_score(result: PortfolioResult) -> tuple[Decimal, ...]:
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
    event_candidates: list[SleeveCandidate],
    grouped: dict[tuple[str, ...], list[tuple[SleeveCandidate, ResearchResult]]],
    rejected_groups: list[tuple[str, ...]],
    representatives: dict[str, list[Representative]],
    component_search: dict[str, dict[str, Any]],
    portfolio_rows: list[dict[str, Any]],
    eligible_portfolios: list[dict[str, Any]],
    top_portfolios: list[dict[str, Any]],
    selected_portfolio: dict[str, Any],
    confirmation_components: dict[str, ResearchResult],
    stress_components: dict[str, ResearchResult],
    confirmation: PortfolioResult,
    stress: PortfolioResult,
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
        "strategy": "de-duplicated simultaneous shock-event consensus",
        "data": {
            "first_bar": _timestamp(max(btc_source[0].start_ms, eth_source[0].start_ms)),
            "last_bar": _timestamp(min(btc_source[-1].end_ms, eth_source[-1].end_ms)),
            "btc_bars_15m": len(btc_source),
            "eth_bars_15m": len(eth_source),
        },
        "periods": {
            "representative_discovery": _period(DISCOVERY),
            "configuration_selection": _period(VALIDATION),
            "confirmation": _period(CONFIRMATION),
        },
        "execution": {
            "signal_timing": "closed 4h bar",
            "fill_timing": "next 4h open",
            "fee_bps_per_fill": float(BASE_FEE_BPS),
            "slippage_bps_per_fill": float(BASE_SLIPPAGE_BPS),
            "funding": "historical instrument funding while positioned",
            "liquidation_modeled": False,
        },
        "representative_selection": {
            "event_candidate_count": len(event_candidates),
            "group_count": len(grouped),
            "rejected_group_count": len(rejected_groups),
            "eligibility": "at least 8 completed discovery trades and not bankrupt",
            "confirmation_used": False,
            "representatives": {
                instrument: [_representative_row(row) for row in rows]
                for instrument, rows in representatives.items()
            },
        },
        "component_selection": {
            instrument: {
                "configuration_count": len(search["configs"]),
                "eligible_count": len(search["eligible"]),
                "used_fallback_diagnostic": not search["eligible"],
                "selected": _component_row(search["selected"]),
                "top_configurations": [_component_row(row) for row in search["ranked"][:20]],
            }
            for instrument, search in component_search.items()
        },
        "portfolio_selection": {
            "candidate_count": len(portfolio_rows),
            "eligible_count": len(eligible_portfolios),
            "used_fallback_diagnostic": not eligible_portfolios,
            "confirmation_used": False,
            "selected": _portfolio_row(selected_portfolio),
            "top_portfolios": [_portfolio_row(row) for row in top_portfolios],
        },
        "confirmation_components": {
            instrument: _research_summary(result)
            for instrument, result in confirmation_components.items()
        },
        "stress_components": {
            instrument: _research_summary(result)
            for instrument, result in stress_components.items()
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
                "The event consensus reached the research return gate, but 2026 is a reused "
                "holdout and forward evidence is required."
                if achieved
                else "The development-selected event consensus did not pass confirmation monthly "
                "return, drawdown, and cost-stress gates."
            ),
        },
        "limitations": [
            "2026 has been viewed in prior studies and is not a fresh independent holdout.",
            "Portfolio drawdown is measured at daily closes; components retain 4h-bar drawdown.",
            "Liquidation, market impact, exchange failure, and shared margin are not modeled.",
        ],
    }


def _representative_row(row: Representative) -> dict[str, Any]:
    return {
        "group": list(row.group),
        "id": row.candidate.id,
        "parameters": row.candidate.parameters,
        "discovery": _research_summary(row.discovery),
    }


def _component_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "config": row["config"].as_dict(),
        "score": list(row["score"]),
        "result": _research_summary(row["result"]),
    }


def _portfolio_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "btc_allocation": float(row["btc_allocation"]),
        "eth_allocation": float(Decimal("1") - row["btc_allocation"]),
        "leverage": float(row["leverage"]),
        "score": [float(value) for value in row["score"]],
        "result": row["result"].as_dict(),
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["portfolio_selection"]["selected"]
    validation = selected["result"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only de-duplicated simultaneous event consensus.",
        "",
        f"Decision: `{payload['decision']['status']}`.",
        f"Event candidates: `{payload['representative_selection']['event_candidate_count']}`; "
        f"groups: `{payload['representative_selection']['group_count']}`; rejected groups: "
        f"`{payload['representative_selection']['rejected_group_count']}`.",
        "",
        f"Portfolio: BTC `{selected['btc_allocation']:.0%}`, ETH "
        f"`{selected['eth_allocation']:.0%}`, leverage `{selected['leverage']:.2f}x`.",
        "",
        "| Split | Return | Daily-close max DD | Positive months | 25% months |",
        "|---|---:|---:|---:|---:|",
        _markdown_row("selection", validation),
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
            "## Component configurations",
            "",
            "| Instrument | Configuration | Validation | Confirmation |",
            "|---|---|---:|---:|",
        ]
    )
    for instrument, selection in payload["component_selection"].items():
        config = selection["selected"]["config"]
        lines.append(
            f"| {instrument} | {config['id']} | "
            f"{selection['selected']['result']['net_return']:.2%} | "
            f"{payload['confirmation_components'][instrument]['net_return']:.2%} |"
        )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _markdown_row(label: str, row: dict[str, Any]) -> str:
    return (
        f"| {label} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | "
        f"{row['positive_month_rate']:.2%} | {row['target_25pct_month_rate']:.2%} |"
    )


def _target_month_rate(result: ResearchResult) -> float:
    return sum(value >= 0.25 for _label, value in result.monthly_returns) / len(
        result.monthly_returns
    )


def _require_aligned(left: list[ResearchBar], right: list[ResearchBar]) -> None:
    if len(left) != len(right) or any(
        first.start_ms != second.start_ms for first, second in zip(left, right, strict=True)
    ):
        raise ValueError("BTC and ETH event-consensus bars are not aligned")


if __name__ == "__main__":
    main()
