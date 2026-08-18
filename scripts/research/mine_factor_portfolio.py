#!/usr/bin/env python3
"""Search a development-selected second factor sleeve for the BTC-to-ETH lead-lag factor."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import (
    ResearchBar,
    ResearchResult,
    aggregate_bars,
    bollinger_reversion_targets,
    donchian_targets,
    ema_targets,
    evaluate_targets,
    funding_by_bar,
    macd_targets,
    momentum_targets,
    rsi_reversion_targets,
)
from mastermind_tick.factor_mining import (
    FactorCandidate,
    causal_features,
    evaluate_formula,
    factor_targets,
    formula_library,
    load_market,
)
from mastermind_tick.factor_portfolio import (
    DailyReturns,
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
    monthly_returns,
    return_correlation,
)
from mastermind_tick.lead_lag_factor import (
    LeadLagCandidate,
    ShockSizing,
    causal_shock_scores,
    evaluate_weighted_targets,
    shock_targets,
    shock_weight_targets,
)
from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class SleeveCandidate:
    id: str
    instrument_id: str
    family: str
    interval_minutes: int
    parameters: dict[str, Any]
    bars: list[ResearchBar]
    funding: list[list[FundingRate]]
    targets: tuple[int | None, ...]


@dataclass(frozen=True)
class CandidateReplay:
    candidate: SleeveCandidate
    discovery: ResearchResult
    validation: ResearchResult
    discovery_correlation: Decimal
    validation_correlation: Decimal


def _day_start(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


INTERVAL_MINUTES = 240
DISCOVERY = (_day_start(date(2021, 1, 1)), _day_end(date(2023, 12, 31)))
VALIDATION = (_day_start(date(2024, 1, 1)), _day_end(date(2025, 12, 31)))
CONFIRMATION = (_day_start(date(2026, 1, 1)), _day_end(date(2026, 8, 10)))
BASE_FEE_BPS = Decimal("5")
BASE_SLIPPAGE_BPS = Decimal("2")
STRESS_FEE_BPS = Decimal("10")
STRESS_SLIPPAGE_BPS = Decimal("5")
LEAD_ALLOCATIONS = tuple(Decimal(value) for value in ("0.25", "0.4", "0.5", "0.6", "0.75", "0.9"))
PORTFOLIO_LEVERAGES = tuple(
    Decimal(value) for value in ("1", "1.25", "1.5", "1.75", "2", "2.25", "2.5")
)
LEAD_CANDIDATE = LeadLagCandidate(15, Decimal("2"), 12, "long_short", "underreaction")
LEAD_SIZING = ShockSizing(Decimal("0.5"), Decimal("1.5"), Decimal("2"))
LEAD_MONTHLY_LOSS_LIMIT = Decimal("0.15")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/factor_portfolio/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading BTC and ETH causal market history", flush=True)
    btc_source, btc_rates = load_market(args.database, "btc_perp")
    eth_source, eth_rates = load_market(args.database, "eth_perp")
    lead_btc = aggregate_bars(btc_source, INTERVAL_MINUTES)
    lead_eth = aggregate_bars(eth_source, INTERVAL_MINUTES)
    _require_aligned_bars(lead_btc, lead_eth)
    lead_funding = funding_by_bar(lead_eth, eth_rates)
    btc_scores, eth_scores = causal_shock_scores(lead_btc, lead_eth, 15 * 6)
    lead_targets = shock_weight_targets(
        shock_targets(btc_scores, eth_scores, LEAD_CANDIDATE),
        btc_scores,
        LEAD_SIZING,
    )
    lead_results = {
        "discovery": _evaluate_lead(
            lead_eth, lead_funding, lead_targets, DISCOVERY, BASE_FEE_BPS, BASE_SLIPPAGE_BPS
        ),
        "validation": _evaluate_lead(
            lead_eth, lead_funding, lead_targets, VALIDATION, BASE_FEE_BPS, BASE_SLIPPAGE_BPS
        ),
    }
    lead_curves = {
        name: decimal_returns(result.daily_returns) for name, result in lead_results.items()
    }
    lead_monthly = {name: monthly_returns(curve) for name, curve in lead_curves.items()}

    print("building independent BTC and ETH factor sleeve library", flush=True)
    candidates = [
        *_candidate_library("btc_perp", btc_source, btc_rates),
        *_candidate_library("eth_perp", eth_source, eth_rates),
        *_event_candidate_library(lead_btc, lead_eth, btc_rates, eth_rates),
    ]
    print(f"evaluating {len(candidates):,} independent sleeves", flush=True)
    eligible: list[CandidateReplay] = []
    evaluated = 0
    for index, candidate in enumerate(candidates, start=1):
        discovery = _evaluate_candidate(candidate, DISCOVERY)
        validation = _evaluate_candidate(candidate, VALIDATION)
        evaluated += 1
        if _standalone_eligible(discovery, validation):
            discovery_curve = decimal_returns(discovery.daily_returns)
            validation_curve = decimal_returns(validation.daily_returns)
            if _labels(discovery_curve) == _labels(lead_curves["discovery"]) and _labels(
                validation_curve
            ) == _labels(lead_curves["validation"]):
                discovery_correlation = return_correlation(
                    monthly_returns(discovery_curve), lead_monthly["discovery"]
                )
                validation_correlation = return_correlation(
                    monthly_returns(validation_curve), lead_monthly["validation"]
                )
                if abs(discovery_correlation) <= Decimal("0.75") and abs(
                    validation_correlation
                ) <= Decimal("0.75"):
                    eligible.append(
                        CandidateReplay(
                            candidate,
                            discovery,
                            validation,
                            discovery_correlation,
                            validation_correlation,
                        )
                    )
        if index % 100 == 0:
            print(
                f"sleeve {index}/{len(candidates)}; low-correlation eligible={len(eligible)}",
                flush=True,
            )

    print(f"searching portfolio weights across {len(eligible):,} eligible sleeves", flush=True)
    portfolio_rows = _portfolio_search(eligible, lead_results, lead_curves)
    if not portfolio_rows:
        raise RuntimeError("no development portfolio passed the risk and return gates")
    selected = portfolio_rows[0]
    selected_replay: CandidateReplay = selected["replay"]
    confirmation_lead = _evaluate_lead(
        lead_eth,
        lead_funding,
        lead_targets,
        CONFIRMATION,
        BASE_FEE_BPS,
        BASE_SLIPPAGE_BPS,
    )
    confirmation_secondary = _evaluate_candidate(selected_replay.candidate, CONFIRMATION)
    confirmation = _combine_results(
        confirmation_lead,
        confirmation_secondary,
        selected["lead_allocation"],
        selected["leverage"],
    )
    stress_lead = _evaluate_lead(
        lead_eth,
        lead_funding,
        lead_targets,
        CONFIRMATION,
        STRESS_FEE_BPS,
        STRESS_SLIPPAGE_BPS,
    )
    stress_secondary = _evaluate_candidate(
        selected_replay.candidate,
        CONFIRMATION,
        fee_bps=STRESS_FEE_BPS,
        slippage_bps=STRESS_SLIPPAGE_BPS,
    )
    stress = _combine_results(
        stress_lead,
        stress_secondary,
        selected["lead_allocation"],
        selected["leverage"],
    )
    payload = _report(
        btc_source,
        eth_source,
        candidates,
        evaluated,
        eligible,
        lead_results,
        selected,
        portfolio_rows[:20],
        confirmation_lead,
        confirmation_secondary,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"factor-portfolio-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
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


def _candidate_library(
    instrument_id: str,
    source_bars: list[ResearchBar],
    rates: list[FundingRate],
) -> list[SleeveCandidate]:
    intervals = (60, 240, 1440)
    bars_by_interval = {interval: aggregate_bars(source_bars, interval) for interval in intervals}
    funding_by_interval = {
        interval: funding_by_bar(bars, rates) for interval, bars in bars_by_interval.items()
    }
    candidates = _expression_candidates(
        instrument_id,
        bars_by_interval,
        funding_by_interval,
    )
    candidates.extend(_bar_factor_candidates(instrument_id, bars_by_interval, funding_by_interval))
    return candidates


def _event_candidate_library(
    btc_bars: list[ResearchBar],
    eth_bars: list[ResearchBar],
    btc_rates: list[FundingRate],
    eth_rates: list[FundingRate],
) -> list[SleeveCandidate]:
    bars_by_instrument = {"btc_perp": btc_bars, "eth_perp": eth_bars}
    funding_by_instrument = {
        "btc_perp": funding_by_bar(btc_bars, btc_rates),
        "eth_perp": funding_by_bar(eth_bars, eth_rates),
    }
    score_cache = {days: causal_shock_scores(btc_bars, eth_bars, days * 6) for days in (15, 30, 60)}
    candidates = []
    for source_id, traded_id in (
        ("btc_perp", "btc_perp"),
        ("eth_perp", "eth_perp"),
        ("eth_perp", "btc_perp"),
    ):
        for days in (15, 30, 60):
            btc_scores, eth_scores = score_cache[days]
            source_scores = btc_scores if source_id == "btc_perp" else eth_scores
            traded_scores = btc_scores if traded_id == "btc_perp" else eth_scores
            gates = (
                ("none",)
                if source_id == traded_id
                else (
                    "none",
                    "underreaction",
                    "opposing",
                    "lag_gap",
                )
            )
            for threshold in (Decimal("1.5"), Decimal("2"), Decimal("2.5")):
                for hold_bars in (2, 4, 8, 12):
                    for gate in gates:
                        base = LeadLagCandidate(
                            days,
                            threshold,
                            hold_bars,
                            "long_short",
                            gate,
                        )
                        raw_targets = shock_targets(source_scores, traded_scores, base)
                        for signal_mode in ("continuation", "reversal"):
                            for direction in ("long_only", "long_short"):
                                targets = _event_targets(raw_targets, signal_mode, direction)
                                threshold_id = f"{threshold:g}".replace(".", "p")
                                event_id = (
                                    f"event-{source_id}-to-{traded_id}-{signal_mode}-{days}d-"
                                    f"threshold-{threshold_id}-hold-{hold_bars}x4h-{gate}-{direction}"
                                )
                                candidates.append(
                                    SleeveCandidate(
                                        id=event_id,
                                        instrument_id=traded_id,
                                        family="shock_event",
                                        interval_minutes=INTERVAL_MINUTES,
                                        parameters={
                                            "source": source_id,
                                            "normalization_days": days,
                                            "threshold": float(threshold),
                                            "hold_bars": hold_bars,
                                            "response_gate": gate,
                                            "signal_mode": signal_mode,
                                            "direction": direction,
                                        },
                                        bars=bars_by_instrument[traded_id],
                                        funding=funding_by_instrument[traded_id],
                                        targets=targets,
                                    )
                                )
    return candidates


def _event_targets(
    targets: tuple[int | None, ...],
    signal_mode: str,
    direction: str,
) -> tuple[int | None, ...]:
    if signal_mode not in {"continuation", "reversal"}:
        raise ValueError("unsupported event signal mode")
    if direction not in {"long_only", "long_short"}:
        raise ValueError("unsupported event direction")
    multiplier = 1 if signal_mode == "continuation" else -1
    return tuple(
        None
        if value is None
        else max(0, value * multiplier)
        if direction == "long_only"
        else value * multiplier
        for value in targets
    )


def _expression_candidates(
    instrument_id: str,
    bars_by_interval: dict[int, list[ResearchBar]],
    funding_by_interval: dict[int, list[list[FundingRate]]],
) -> list[SleeveCandidate]:
    candidates = []
    for interval in (60, 240):
        bars = bars_by_interval[interval]
        grouped_funding = funding_by_interval[interval]
        features = causal_features(bars, grouped_funding)
        for formula in formula_library():
            values = evaluate_formula(formula, features)
            for threshold in (Decimal("0"), Decimal("0.5"), Decimal("1")):
                for direction in ("long_only", "long_short"):
                    factor = FactorCandidate(formula, threshold, direction, interval)
                    candidates.append(
                        SleeveCandidate(
                            id=f"{instrument_id}-{factor.id}",
                            instrument_id=instrument_id,
                            family="expression",
                            interval_minutes=interval,
                            parameters={
                                "formula": formula.display,
                                "tokens": list(formula.tokens),
                                "threshold": float(threshold),
                                "direction": direction,
                            },
                            bars=bars,
                            funding=grouped_funding,
                            targets=factor_targets(values, threshold, direction),
                        )
                    )
    return candidates


def _bar_factor_candidates(
    instrument_id: str,
    bars_by_interval: dict[int, list[ResearchBar]],
    funding_by_interval: dict[int, list[list[FundingRate]]],
) -> list[SleeveCandidate]:
    candidates = []
    for interval in (60, 240, 1440):
        bars = bars_by_interval[interval]
        funding = funding_by_interval[interval]
        for direction in ("long_only", "long_short"):
            for fast, slow in _ema_pairs(interval):
                candidates.append(
                    _candidate(
                        instrument_id,
                        "ema",
                        interval,
                        direction,
                        {"fast": fast, "slow": slow},
                        bars,
                        funding,
                        ema_targets(bars, fast, slow, direction),
                    )
                )
            for entry, exit_window in _donchian_pairs(interval):
                candidates.append(
                    _candidate(
                        instrument_id,
                        "donchian",
                        interval,
                        direction,
                        {"entry": entry, "exit": exit_window},
                        bars,
                        funding,
                        donchian_targets(bars, entry, exit_window, direction),
                    )
                )
            for lookback, threshold in _momentum_pairs(interval):
                candidates.append(
                    _candidate(
                        instrument_id,
                        "momentum",
                        interval,
                        direction,
                        {"lookback": lookback, "threshold": threshold},
                        bars,
                        funding,
                        momentum_targets(bars, lookback, threshold, direction),
                    )
                )
            for period, lower, upper in ((14, 30, 70), (28, 25, 75)):
                candidates.append(
                    _candidate(
                        instrument_id,
                        "rsi_reversion",
                        interval,
                        direction,
                        {"period": period, "lower": lower, "upper": upper},
                        bars,
                        funding,
                        rsi_reversion_targets(bars, period, lower, upper, direction),
                    )
                )
            for fast, slow, signal in _macd_sets(interval):
                candidates.append(
                    _candidate(
                        instrument_id,
                        "macd",
                        interval,
                        direction,
                        {"fast": fast, "slow": slow, "signal": signal},
                        bars,
                        funding,
                        macd_targets(bars, fast, slow, signal, direction),
                    )
                )
            for period, deviations in ((20, 2.0), (40, 2.5)):
                candidates.append(
                    _candidate(
                        instrument_id,
                        "bollinger_reversion",
                        interval,
                        direction,
                        {"period": period, "deviations": deviations},
                        bars,
                        funding,
                        bollinger_reversion_targets(bars, period, deviations, direction),
                    )
                )
    return candidates


def _candidate(
    instrument_id: str,
    family: str,
    interval: int,
    direction: str,
    parameters: dict[str, Any],
    bars: list[ResearchBar],
    funding: list[list[FundingRate]],
    targets: tuple[int | None, ...],
) -> SleeveCandidate:
    parameter_id = "-".join(str(value).replace(".", "p") for value in parameters.values())
    return SleeveCandidate(
        id=f"{instrument_id}-{family}-{interval}m-{parameter_id}-{direction}",
        instrument_id=instrument_id,
        family=family,
        interval_minutes=interval,
        parameters={**parameters, "direction": direction},
        bars=bars,
        funding=funding,
        targets=targets,
    )


def _portfolio_search(
    candidates: list[CandidateReplay],
    lead_results: dict[str, ResearchResult],
    lead_curves: dict[str, DailyReturns],
) -> list[dict[str, Any]]:
    rows = []
    for replay in candidates:
        secondary_curves = {
            "discovery": decimal_returns(replay.discovery.daily_returns),
            "validation": decimal_returns(replay.validation.daily_returns),
        }
        for lead_allocation in LEAD_ALLOCATIONS:
            for leverage in PORTFOLIO_LEVERAGES:
                portfolios = {
                    split: evaluate_static_portfolio(
                        {"lead_lag": lead_curves[split], "secondary": secondary_curves[split]},
                        {
                            "lead_lag": lead_allocation,
                            "secondary": Decimal("1") - lead_allocation,
                        },
                        leverage=leverage,
                    )
                    for split in ("discovery", "validation")
                }
                if not _portfolio_eligible(
                    portfolios, lead_results, replay, lead_allocation, leverage
                ):
                    continue
                rows.append(
                    {
                        "replay": replay,
                        "lead_allocation": lead_allocation,
                        "leverage": leverage,
                        "discovery": portfolios["discovery"],
                        "validation": portfolios["validation"],
                        "score": _portfolio_score(portfolios),
                    }
                )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


def _portfolio_eligible(
    portfolios: dict[str, PortfolioResult],
    lead_results: dict[str, ResearchResult],
    secondary: CandidateReplay,
    lead_allocation: Decimal,
    leverage: Decimal,
) -> bool:
    if any(
        result.bankrupt or result.net_return <= 0 or result.max_drawdown < Decimal("-0.35")
        for result in portfolios.values()
    ):
        return False
    secondary_results = {
        "discovery": secondary.discovery,
        "validation": secondary.validation,
    }
    for split in ("discovery", "validation"):
        lead_risk = lead_allocation * Decimal(str(abs(lead_results[split].max_drawdown)))
        secondary_risk = (Decimal("1") - lead_allocation) * Decimal(
            str(abs(secondary_results[split].max_drawdown))
        )
        if leverage * max(lead_risk, secondary_risk) > Decimal("0.35"):
            return False
    return True


def _portfolio_score(portfolios: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
    discovery = portfolios["discovery"]
    validation = portfolios["validation"]
    return (
        min(discovery.target_month_rate, validation.target_month_rate),
        discovery.target_month_rate + validation.target_month_rate,
        min(discovery.positive_month_rate, validation.positive_month_rate),
        min(discovery.worst_month, validation.worst_month),
        min(discovery.net_return, validation.net_return),
        min(discovery.max_drawdown, validation.max_drawdown),
    )


def _standalone_eligible(discovery: ResearchResult, validation: ResearchResult) -> bool:
    return bool(
        discovery.net_return > 0
        and validation.net_return > 0
        and discovery.max_drawdown >= -0.50
        and validation.max_drawdown >= -0.50
        and discovery.completed_trades >= 10
        and validation.completed_trades >= 8
        and not discovery.bankrupt
        and not validation.bankrupt
    )


def _combine_results(
    lead: ResearchResult,
    secondary: ResearchResult,
    lead_allocation: Decimal,
    leverage: Decimal,
) -> PortfolioResult:
    return evaluate_static_portfolio(
        {
            "lead_lag": decimal_returns(lead.daily_returns),
            "secondary": decimal_returns(secondary.daily_returns),
        },
        {"lead_lag": lead_allocation, "secondary": Decimal("1") - lead_allocation},
        leverage=leverage,
    )


def _evaluate_lead(
    bars: list[ResearchBar],
    funding: list[list[FundingRate]],
    targets: tuple[Decimal | None, ...],
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
        monthly_loss_limit=LEAD_MONTHLY_LOSS_LIMIT,
    )


def _evaluate_candidate(
    candidate: SleeveCandidate,
    period: tuple[int, int],
    *,
    fee_bps: Decimal = BASE_FEE_BPS,
    slippage_bps: Decimal = BASE_SLIPPAGE_BPS,
) -> ResearchResult:
    return evaluate_targets(
        candidate.bars,
        candidate.targets,
        start_ms=period[0],
        end_ms=period[1],
        funding=candidate.funding,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )


def _report(
    btc_source: list[ResearchBar],
    eth_source: list[ResearchBar],
    candidates: list[SleeveCandidate],
    evaluated: int,
    eligible: list[CandidateReplay],
    lead_results: dict[str, ResearchResult],
    selected: dict[str, Any],
    top_rows: list[dict[str, Any]],
    confirmation_lead: ResearchResult,
    confirmation_secondary: ResearchResult,
    confirmation: PortfolioResult,
    stress: PortfolioResult,
) -> dict[str, Any]:
    replay: CandidateReplay = selected["replay"]
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
        "strategy": "static two-sleeve causal factor portfolio",
        "data": {
            "first_bar": _timestamp(max(btc_source[0].start_ms, eth_source[0].start_ms)),
            "last_bar": _timestamp(min(btc_source[-1].end_ms, eth_source[-1].end_ms)),
            "btc_bars_15m": len(btc_source),
            "eth_bars_15m": len(eth_source),
        },
        "periods": {
            "discovery": _period(DISCOVERY),
            "validation": _period(VALIDATION),
            "confirmation": _period(CONFIRMATION),
        },
        "execution": {
            "signal_timing": "closed bar",
            "fill_timing": "next bar open",
            "base_fee_bps_per_fill": float(BASE_FEE_BPS),
            "base_slippage_bps_per_fill": float(BASE_SLIPPAGE_BPS),
            "stress_fee_bps_per_fill": float(STRESS_FEE_BPS),
            "stress_slippage_bps_per_fill": float(STRESS_SLIPPAGE_BPS),
            "funding": "historical instrument funding while positioned",
            "portfolio_model": "fixed initial sleeve capital; no daily rebalancing",
            "liquidation_modeled": False,
        },
        "lead_sleeve": {
            "candidate": LEAD_CANDIDATE.as_dict(),
            "sizing": LEAD_SIZING.as_dict(),
            "monthly_loss_limit": float(LEAD_MONTHLY_LOSS_LIMIT),
            **{name: _research_summary(result) for name, result in lead_results.items()},
        },
        "selection": {
            "secondary_candidate_count": len(candidates),
            "secondary_evaluated_count": evaluated,
            "low_correlation_eligible_count": len(eligible),
            "confirmation_used_for_selection": False,
            "rule": (
                "require positive discovery and validation returns, component drawdown no worse "
                "than 50%, minimum trade counts, and absolute monthly correlation to lead-lag no "
                "greater than 0.75; rank portfolios by 25% month "
                "coverage "
                "with a 35% daily-close portfolio drawdown gate"
            ),
        },
        "selected": {
            "secondary": _candidate_summary(replay),
            "lead_allocation": float(selected["lead_allocation"]),
            "secondary_allocation": float(Decimal("1") - selected["lead_allocation"]),
            "portfolio_leverage": float(selected["leverage"]),
            "discovery": selected["discovery"].as_dict(),
            "validation": selected["validation"].as_dict(),
        },
        "top_development_portfolios": [_portfolio_row(row) for row in top_rows],
        "confirmation_components": {
            "lead_lag": _research_summary(confirmation_lead),
            "secondary": _research_summary(confirmation_secondary),
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
                "The portfolio reached the research return gate, but 2026 is a reused holdout and "
                "bar-level joint liquidation risk is not modeled."
                if achieved
                else "No development-selected two-sleeve portfolio passed confirmation monthly "
                "return, drawdown, and cost-stress gates."
            ),
        },
        "limitations": [
            "2026 has been viewed in prior studies and is not a fresh independent holdout.",
            "Portfolio drawdown is measured at daily closes; component replays retain "
            "bar-level DD.",
            "Fixed sleeve allocations do not include borrowing cost or cross-margin liquidation.",
            "Market impact, exchange failure, and synchronized intrabar joint equity are not "
            "modeled.",
        ],
    }


def _portfolio_row(row: dict[str, Any]) -> dict[str, Any]:
    replay: CandidateReplay = row["replay"]
    return {
        "secondary_id": replay.candidate.id,
        "lead_allocation": float(row["lead_allocation"]),
        "portfolio_leverage": float(row["leverage"]),
        "score": [float(value) for value in row["score"]],
        "discovery": row["discovery"].as_dict(),
        "validation": row["validation"].as_dict(),
    }


def _candidate_summary(replay: CandidateReplay) -> dict[str, Any]:
    candidate = replay.candidate
    return {
        "id": candidate.id,
        "instrument_id": candidate.instrument_id,
        "family": candidate.family,
        "interval_minutes": candidate.interval_minutes,
        "parameters": candidate.parameters,
        "monthly_correlation": {
            "discovery": float(replay.discovery_correlation),
            "validation": float(replay.validation_correlation),
        },
        "discovery": _research_summary(replay.discovery),
        "validation": _research_summary(replay.validation),
    }


def _research_summary(result: ResearchResult) -> dict[str, Any]:
    monthly = [{"label": label, "return": value} for label, value in result.monthly_returns]
    return {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
        "bankrupt": result.bankrupt,
        "positive_month_rate": _positive_month_rate(result),
        "target_25pct_month_rate": (
            sum(value >= 0.25 for _label, value in result.monthly_returns)
            / len(result.monthly_returns)
        ),
        "monthly_returns": monthly,
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only development-selected two-sleeve factor portfolio.",
        "",
        f"Decision: `{payload['decision']['status']}`.",
        f"Independent sleeves evaluated: `{payload['selection']['secondary_evaluated_count']:,}`; "
        f"low-correlation eligible: `{payload['selection']['low_correlation_eligible_count']:,}`.",
        "",
        f"Secondary: `{selected['secondary']['id']}`.",
        f"Allocation: lead-lag `{selected['lead_allocation']:.0%}`, secondary "
        f"`{selected['secondary_allocation']:.0%}`; portfolio leverage "
        f"`{selected['portfolio_leverage']:.2f}x`.",
        "",
        "| Split | Return | Daily-close max DD | Positive months | 25% months |",
        "|---|---:|---:|---:|---:|",
    ]
    for label in ("discovery", "validation"):
        row = selected[label]
        lines.append(
            f"| {label} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | "
            f"{row['positive_month_rate']:.2%} | {row['target_25pct_month_rate']:.2%} |"
        )
    lines.append(
        f"| confirmation | {confirmation['net_return']:.2%} | "
        f"{confirmation['max_drawdown']:.2%} | {confirmation['positive_month_rate']:.2%} | "
        f"{confirmation['target_25pct_month_rate']:.2%} |"
    )
    lines.extend(
        [
            "",
            f"Stress confirmation (10+5 bps): `{stress['net_return']:.2%}`; "
            f"daily-close max DD `{stress['max_drawdown']:.2%}`.",
            "",
            "## Confirmation monthly returns",
            "",
            "| Month | Return |",
            "|---|---:|",
        ]
    )
    lines.extend(
        f"| {row['label']} | {row['return']:.2%} |" for row in confirmation["monthly_returns"]
    )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _positive_month_rate(result: ResearchResult) -> float:
    return sum(value > 0 for _label, value in result.monthly_returns) / len(result.monthly_returns)


def _labels(rows: DailyReturns) -> tuple[str, ...]:
    return tuple(label for label, _value in rows)


def _require_aligned_bars(left: list[ResearchBar], right: list[ResearchBar]) -> None:
    if len(left) != len(right) or any(
        first.start_ms != second.start_ms for first, second in zip(left, right, strict=True)
    ):
        raise ValueError("BTC and ETH lead-lag bars are not aligned")


def _ema_pairs(interval: int) -> tuple[tuple[int, int], ...]:
    return {
        60: ((12, 48), (24, 96), (48, 192)),
        240: ((6, 24), (12, 48), (24, 96)),
        1440: ((10, 50), (20, 100), (50, 200)),
    }[interval]


def _donchian_pairs(interval: int) -> tuple[tuple[int, int], ...]:
    return {
        60: ((24, 12), (72, 24), (168, 48)),
        240: ((6, 3), (18, 6), (42, 12)),
        1440: ((20, 10), (55, 20), (100, 50)),
    }[interval]


def _momentum_pairs(interval: int) -> tuple[tuple[int, float], ...]:
    return {
        60: ((24, 0.01), (72, 0.02), (168, 0.04)),
        240: ((6, 0.01), (18, 0.02), (42, 0.04)),
        1440: ((20, 0.05), (60, 0.10), (120, 0.15)),
    }[interval]


def _macd_sets(interval: int) -> tuple[tuple[int, int, int], ...]:
    return {
        60: ((12, 26, 9), (24, 52, 18)),
        240: ((6, 24, 6), (12, 48, 9)),
        1440: ((10, 30, 9), (20, 60, 12)),
    }[interval]


def _period(value: tuple[int, int]) -> dict[str, str]:
    return {"start": _timestamp(value[0]), "end": _timestamp(value[1])}


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
