#!/usr/bin/env python3
"""Test closed-bar volatility-spread strength as a bounded entry risk budget."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import explore_soxl_volatility_spread_v2 as phase_two

from mastermind_tick.volatility_spread import (
    SpreadFeatures,
    SpreadParameters,
    SpreadResult,
    build_spread_features,
    evaluate_spread,
)
from mastermind_tick.volatility_spread_forward import load_forward_market, load_frozen_candidate

TRAIN_END = date(2026, 6, 30)
VALIDATION_START = date(2026, 7, 1)
VALIDATION_END = date(2026, 7, 31)
CONFIRMATION_START = date(2026, 8, 1)
CONFIRMATION_END = date(2026, 8, 10)
REVEALED_START = date(2026, 8, 11)
REVEALED_END = date(2026, 8, 13)


def main() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("strategies/candidates/soxl_volatility_spread_true_range_v1.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14-risk-budget"),
    )
    args = parser.parse_args()

    frozen = load_frozen_candidate(args.candidate)
    bars, funding_by_bar, executions = load_forward_market(args.database, frozen)
    parameters = frozen.parameters
    features = build_spread_features(
        bars,
        fast_window=parameters.fast_window,
        slow_window=parameters.slow_window,
        breakout_window=parameters.breakout_window,
        compression_ratio=parameters.compression_ratio,
        compression_lookback=parameters.compression_lookback,
        spread_measure=parameters.spread_measure,
    )
    periods = _periods(frozen.continuous_replay_start_ms)
    profiles = _profiles()
    evaluation = []
    for profile in profiles:
        multipliers = _multipliers(features, parameters, profile)
        train = _evaluate(
            bars, funding_by_bar, executions, features, parameters, multipliers, periods["train"]
        )
        validation = _evaluate(
            bars,
            funding_by_bar,
            executions,
            features,
            parameters,
            multipliers,
            periods["validation"],
        )
        evaluation.append(
            {
                "profile": profile,
                "selection_score": _selection_score(train, validation),
                "train": _summary(train),
                "validation": _summary(validation),
            }
        )

    eligible = [
        item
        for item in evaluation
        if item["train"]["net_return"] > 0
        and item["validation"]["net_return"] > 0
        and item["validation"]["completed_trades"] >= 4
    ]
    finalists = sorted(eligible, key=lambda item: item["selection_score"], reverse=True)[:10]
    if not finalists:
        raise RuntimeError("no risk-budget profile passed train/validation gates")
    for item in finalists:
        multipliers = _multipliers(features, parameters, item["profile"])
        confirmation = _evaluate(
            bars,
            funding_by_bar,
            executions,
            features,
            parameters,
            multipliers,
            periods["confirmation"],
        )
        item["confirmation"] = _summary(confirmation)

    confirmation_passed = [
        item
        for item in finalists
        if item["confirmation"]["net_return"] > 0 and item["confirmation"]["completed_trades"] >= 2
    ]
    selected = confirmation_passed[0] if confirmation_passed else finalists[0]
    selected_multipliers = _multipliers(features, parameters, selected["profile"])
    results = {
        name: _evaluate(
            bars,
            funding_by_bar,
            executions,
            features,
            parameters,
            selected_multipliers,
            period,
        )
        for name, period in periods.items()
    }
    baseline = {
        name: _evaluate(
            bars,
            funding_by_bar,
            executions,
            features,
            parameters,
            _multipliers(features, parameters, _profiles()[0]),
            period,
        )
        for name, period in periods.items()
    }
    tick_risk = phase_two._tick_path_risk(
        args.database,
        results["development"].trades,
        [event for events in funding_by_bar for event in events],
        expected_final_equity=results["development"].final_equity,
    )
    payload = {
        "schema_version": 1,
        "strategy": "SOXLUSDT volatility-spread closed-bar strength risk budget",
        "status": "exploratory_post_reveal_no_clean_holdout",
        "parameter_search_performed": True,
        "base_parameters_frozen": asdict(parameters),
        "profile_rule": (
            "At a signal bar, ratio / entry_ratio is compared with a threshold; the assigned "
            "multiplier is fixed before the next persisted-tick fill. No future bar is used."
        ),
        "splits": {
            name: {"start": _timestamp(period[0]), "end": _timestamp(period[1])}
            for name, period in periods.items()
        },
        "selection": {
            "profile_count": len(profiles),
            "eligible_count": len(eligible),
            "finalists_checked": len(finalists),
            "confirmation_positive_finalists": len(confirmation_passed),
            "rule": "positive train and validation, then positive August 1-10 confirmation",
            "selected": selected,
            "finalists": finalists,
        },
        "selected_risk_budget": {
            "results": {name: _summary(result) for name, result in results.items()},
            "daily": {name: list(result.daily_returns) for name, result in results.items()},
            "tick_path_risk": tick_risk,
        },
        "fixed_budget_baseline": {
            "profile": _profiles()[0],
            "results": {name: _summary(result) for name, result in baseline.items()},
        },
        "target": {"geometric_daily_return": 0.05, "achieved": False},
        "limitations": [
            "The candidate is selected after a parameter search and has no clean holdout.",
            "August 11-13 is already revealed and is diagnostic only.",
            "Liquidation and shared portfolio margin are not modeled.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(_markdown(payload), encoding="utf-8")
    print(args.output_dir / "README.md")
    return payload


def _profiles() -> list[dict[str, float | str]]:
    return [
        {
            "name": "fixed_1.0",
            "threshold": 999.0,
            "weak_multiplier": 1.0,
            "strong_multiplier": 1.0,
        },
        *[
            {
                "name": f"threshold_{threshold:g}_{weak:g}_{strong:g}",
                "threshold": threshold,
                "weak_multiplier": weak,
                "strong_multiplier": strong,
            }
            for threshold in (1.10, 1.25, 1.50)
            for weak, strong in (
                (0.5, 1.25),
                (0.5, 1.5),
                (0.75, 1.25),
                (0.75, 1.5),
                (1.0, 1.25),
                (1.0, 1.5),
                (1.0, 2.0),
            )
        ],
    ]


def _multipliers(
    features: SpreadFeatures, parameters: SpreadParameters, profile: dict[str, float | str]
) -> tuple[float, ...]:
    threshold = float(profile["threshold"])
    weak = float(profile["weak_multiplier"])
    strong = float(profile["strong_multiplier"])
    return tuple(
        strong if ratio is not None and ratio / parameters.entry_ratio >= threshold else weak
        for ratio in features.ratios
    )


def _evaluate(bars, funding, executions, features, parameters, multipliers, period) -> SpreadResult:
    return evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=period[0],
        end_ms=period[1],
        funding_by_bar=funding,
        execution_by_bar=executions,
        entry_exposure_multipliers=multipliers,
    )


def _selection_score(train: SpreadResult, validation: SpreadResult) -> float:
    minimum_geo = min(train.geometric_daily_return, validation.geometric_daily_return)
    return minimum_geo - 0.25 * max(abs(train.max_drawdown), abs(validation.max_drawdown))


def _summary(result: SpreadResult) -> dict[str, Any]:
    return {
        "net_return": result.net_return,
        "geometric_daily_return": result.geometric_daily_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "target_day_rate": result.target_day_rate,
    }


def _periods(replay_start_ms: int) -> dict[str, tuple[int, int]]:
    return {
        "train": (replay_start_ms, _day_end(TRAIN_END)),
        "validation": (_day_start(VALIDATION_START), _day_end(VALIDATION_END)),
        "confirmation": (_day_start(CONFIRMATION_START), _day_end(CONFIRMATION_END)),
        "development": (replay_start_ms, _day_end(CONFIRMATION_END)),
        "revealed_diagnostic": (_day_start(REVEALED_START), _day_end(REVEALED_END)),
        "continuous": (replay_start_ms, _day_end(REVEALED_END)),
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    risk = payload["selected_risk_budget"]["results"]
    base = payload["fixed_budget_baseline"]["results"]
    lines = [
        "# SOXLUSDT Volatility-Spread Risk-Budget Exploration",
        "",
        "Status: **exploratory_post_reveal_no_clean_holdout**  ",
        f"Selected profile: `{selected['profile']['name']}`  ",
        "5% daily target: **not achieved**",
        "",
        "The base breakout parameters were fixed before this experiment. The only searched control "
        "is a bounded 0.5x-2.0x entry multiplier derived from the closed signal bar's volatility "
        "spread strength.",
        "",
        (
            "| Path | Train geo/day | Validation geo/day | Confirmation geo/day | "
            "Development geo/day | Revealed 8/11-13 | 15m close DD |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
        _row("Risk budget", risk),
        _row("Fixed 1.0x", base),
        "",
        f"Selection screened {payload['selection']['profile_count']} profiles, with "
        f"{payload['selection']['eligible_count']} passing train/validation and "
        f"{payload['selection']['confirmation_positive_finalists']} of "
        f"{payload['selection']['finalists_checked']} finalists positive in confirmation.",
        "",
        "This is not a production recommendation. August 11-13 was already revealed and is "
        "diagnostic only; any profile retained for monitoring needs a fresh post-August-13 "
        "forward window.",
        "",
    ]
    return "\n".join(lines)


def _row(name: str, results: dict[str, dict[str, Any]]) -> str:
    return (
        f"| {name} | {_percent(results['train']['geometric_daily_return'])} | "
        f"{_percent(results['validation']['geometric_daily_return'])} | "
        f"{_percent(results['confirmation']['geometric_daily_return'])} | "
        f"{_percent(results['development']['geometric_daily_return'])} | "
        f"{_percent(results['revealed_diagnostic']['net_return'])} | "
        f"{_percent(results['development']['max_drawdown'])} |"
    )


def _percent(value: float) -> str:
    return f"{value:+.2%}"


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
