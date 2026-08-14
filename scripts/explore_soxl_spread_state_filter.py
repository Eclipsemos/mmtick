#!/usr/bin/env python3
"""Explore a no-lookahead 15m state filter on higher-horizon spread sleeves."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import explore_soxl_multihorizon_spread as multi
import explore_soxl_volatility_spread_v2 as phase_two

from mastermind_tick.multi_horizon_spread import build_15m_state_filter
from mastermind_tick.volatility_spread import (
    SpreadFeatures,
    SpreadParameters,
    SpreadResult,
    build_spread_features,
    evaluate_spread,
)
from mastermind_tick.volatility_spread_forward import load_forward_market, load_frozen_candidate

FILTERS = (
    ("none", "none", 1.0),
    ("ratio_1.0", "ratio", 1.0),
    ("ratio_1.2", "ratio", 1.2),
    ("direction_consensus", "direction_consensus", 1.0),
    ("consensus_ratio_1.0", "consensus_ratio", 1.0),
    ("consensus_ratio_1.2", "consensus_ratio", 1.2),
)


def main() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("strategies/candidates/soxl_volatility_spread_true_range_v1.json"),
    )
    parser.add_argument(
        "--base-report",
        type=Path,
        default=Path(
            "reports/experiments/soxl_volatility_spread/2026-08-14-multihorizon/results.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14-state-filter"),
    )
    args = parser.parse_args()

    frozen = load_frozen_candidate(args.candidate)
    base_report = json.loads(args.base_report.read_text(encoding="utf-8"))
    base_bars, base_funding, base_executions = load_forward_market(args.database, frozen)
    base_features = build_spread_features(
        base_bars,
        fast_window=frozen.parameters.fast_window,
        slow_window=frozen.parameters.slow_window,
        breakout_window=frozen.parameters.breakout_window,
        compression_ratio=frozen.parameters.compression_ratio,
        compression_lookback=frozen.parameters.compression_lookback,
        spread_measure=frozen.parameters.spread_measure,
    )
    markets = {
        15: multi.AggregatedSpreadMarket(15, base_bars, base_funding, base_executions),
        30: multi.aggregate_spread_market(
            base_bars, base_funding, base_executions, interval_minutes=30
        ),
        60: multi.aggregate_spread_market(
            base_bars, base_funding, base_executions, interval_minutes=60
        ),
    }
    periods = multi._periods()
    chosen: dict[str, dict[str, Any]] = {}
    filter_arrays: dict[tuple[int, str], tuple[int | None, ...]] = {}
    for timeframe in (30, 60):
        finalists = base_report["selection"]["searches"][str(timeframe)]["top_finalists"]
        evaluations = []
        for finalist in finalists:
            parameters = SpreadParameters(**finalist["parameters"])
            features = multi._features(markets[timeframe].bars, parameters)
            for filter_name, mode, minimum_ratio in FILTERS:
                key = (timeframe, filter_name)
                if key not in filter_arrays:
                    filter_arrays[key] = build_15m_state_filter(
                        base_bars,
                        base_features,
                        markets[timeframe].bars,
                        mode=mode,
                        minimum_ratio=minimum_ratio,
                    )
                state_filter = filter_arrays[key]
                train = _evaluate_filtered(
                    markets[timeframe],
                    features,
                    parameters,
                    periods["train"],
                    state_filter,
                )
                validation = _evaluate_filtered(
                    markets[timeframe],
                    features,
                    parameters,
                    periods["validation"],
                    state_filter,
                )
                train_summary = multi._summary(train)
                validation_summary = multi._summary(validation)
                if not multi._eligible(train_summary, validation_summary):
                    continue
                evaluations.append(
                    {
                        "parameters": asdict(parameters),
                        "filter": filter_name,
                        "selection_score": multi._selection_score(train, validation),
                        "train": train_summary,
                        "validation": validation_summary,
                    }
                )
        ranked = sorted(evaluations, key=lambda item: item["selection_score"], reverse=True)
        finalists_with_confirmation = []
        for item in ranked[:20]:
            parameters = SpreadParameters(**item["parameters"])
            features = multi._features(markets[timeframe].bars, parameters)
            state_filter = filter_arrays[(timeframe, item["filter"])]
            confirmation = _evaluate_filtered(
                markets[timeframe],
                features,
                parameters,
                periods["confirmation"],
                state_filter,
            )
            fresh = _evaluate_filtered(
                markets[timeframe],
                features,
                parameters,
                periods["fresh_holdout"],
                state_filter,
            )
            finalists_with_confirmation.append(
                {
                    **item,
                    "confirmation": multi._summary(confirmation),
                    "fresh_holdout_diagnostic": multi._summary(fresh),
                }
            )
        passed = [
            item
            for item in finalists_with_confirmation
            if item["confirmation"]["net_return"] > 0
            and item["confirmation"]["completed_trades"] >= 2
        ]
        if not finalists_with_confirmation:
            raise RuntimeError(f"no eligible state-filter candidates for {timeframe}m")
        selected = (passed or finalists_with_confirmation)[0]
        chosen[str(timeframe)] = {
            "candidate_count_from_base_finalists": len(finalists),
            "filter_candidate_count": len(evaluations),
            "finalists_checked": len(finalists_with_confirmation),
            "confirmation_positive_finalists": len(passed),
            "selected": selected,
            "top_finalists": finalists_with_confirmation,
        }
        print(f"selected {timeframe}m state filter {selected['filter']}", flush=True)

    sleeves: dict[str, dict[str, Any]] = {}
    for timeframe in (15, 30, 60):
        if timeframe == 15:
            parameters = frozen.parameters
            state_filter = None
        else:
            selected = chosen[str(timeframe)]["selected"]
            parameters = SpreadParameters(**selected["parameters"])
            state_filter = filter_arrays[(timeframe, selected["filter"])]
        market = markets[timeframe]
        features = multi._features(market.bars, parameters)
        results = {
            name: _evaluate_filtered(market, features, parameters, period, state_filter)
            for name, period in periods.items()
        }
        all_funding = [event for events in market.funding_by_bar for event in events]
        tick_risk = phase_two._tick_path_risk(
            args.database,
            results["development"].trades,
            all_funding,
            expected_final_equity=results["development"].final_equity,
        )
        sleeves[str(timeframe)] = {
            "timeframe_minutes": timeframe,
            "parameters": asdict(parameters),
            "state_filter": "none"
            if state_filter is None
            else chosen[str(timeframe)]["selected"]["filter"],
            "results": {name: multi._summary(result) for name, result in results.items()},
            "daily": {name: list(result.daily_returns) for name, result in results.items()},
            "development_tick_path": tick_risk,
        }

    portfolio = multi._portfolio_report(sleeves, periods)
    payload = {
        "schema_version": 1,
        "strategy": "SOXLUSDT 15m-state-filtered multi-horizon volatility spread",
        "status": "exploratory_post_reveal_no_clean_holdout",
        "parameter_search_performed": True,
        "fresh_holdout_used_for_selection": False,
        "source_candidate": frozen.id,
        "base_multihorizon_report": str(args.base_report),
        "splits": {
            name: {"start": multi._timestamp(period[0]), "end": multi._timestamp(period[1])}
            for name, period in periods.items()
        },
        "filter_definition": (
            "At a higher-horizon signal close, inspect only the last closed 15m bar. A consensus "
            "filter allows an entry only when that bar breaks its prior channel in the same "
            "direction."
        ),
        "selection": chosen,
        "portfolio": portfolio,
        "target": {
            "geometric_daily_return": 0.05,
            "achieved_by_diagnostic_cap": portfolio["decision"]["achieved_by_diagnostic_cap"],
            "diagnostic_cap_total_exposure": 3.0,
        },
        "sleeves": sleeves,
        "limitations": [
            "The filter overlay was selected from prior 30m/60m finalists, not a fresh global "
            "search.",
            "August 11-13 was already revealed and is diagnostic only.",
            "Portfolio results are daily-rebalanced and have no shared intraday margin model.",
            "No production approval is granted; forward evidence starts August 14.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    markdown = multi._markdown(payload).replace(
        "# SOXLUSDT Multi-Horizon Volatility-Spread Exploration",
        "# SOXLUSDT State-Filtered Multi-Horizon Volatility-Spread Exploration",
        1,
    )
    (args.output_dir / "README.md").write_text(markdown, encoding="utf-8")
    print(args.output_dir / "README.md")
    return payload


def _evaluate_filtered(
    market: multi.AggregatedSpreadMarket,
    features: SpreadFeatures,
    parameters: SpreadParameters,
    period: tuple[int, int],
    state_filter: tuple[int | None, ...] | None,
) -> SpreadResult:
    return evaluate_spread(
        market.bars,
        features,
        parameters,
        start_ms=period[0],
        end_ms=period[1],
        funding_by_bar=market.funding_by_bar,
        execution_by_bar=market.executions,
        entry_direction_filter=state_filter,
    )


if __name__ == "__main__":
    main()
