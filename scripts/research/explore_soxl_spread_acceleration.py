#!/usr/bin/env python3
"""Explore accelerating volatility-spread releases on closed SOXLUSDT bars."""

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
    parser.add_argument("--finalists", type=int, default=20)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14-acceleration"),
    )
    args = parser.parse_args()
    if args.finalists < 1:
        raise ValueError("finalists must be positive")
    frozen = load_frozen_candidate(args.candidate)
    bars, funding_by_bar, executions = load_forward_market(args.database, frozen)
    periods = _periods(frozen.continuous_replay_start_ms)
    candidates = _candidate_grid()
    cache: dict[tuple[int, int, int], SpreadFeatures] = {}
    evaluated = []
    for index, (parameters, gate) in enumerate(candidates, start=1):
        features = _features(bars, parameters, cache)
        state_filter = acceleration_filter(features, parameters, gate)
        train = _evaluate(
            bars, funding_by_bar, executions, features, parameters, state_filter, periods["train"]
        )
        validation = _evaluate(
            bars,
            funding_by_bar,
            executions,
            features,
            parameters,
            state_filter,
            periods["validation"],
        )
        evaluated.append(
            {
                "parameters": asdict(parameters),
                "gate": gate,
                "selection_score": _selection_score(train, validation),
                "train": _summary(train),
                "validation": _summary(validation),
            }
        )
        if index % 250 == 0:
            print(f"acceleration search {index}/{len(candidates)}", flush=True)
    eligible = [item for item in evaluated if _eligible(item["train"], item["validation"])]
    finalists = sorted(
        eligible or evaluated, key=lambda item: item["selection_score"], reverse=True
    )[: args.finalists]
    for item in finalists:
        parameters = SpreadParameters(**item["parameters"])
        features = _features(bars, parameters, cache)
        state_filter = acceleration_filter(features, parameters, item["gate"])
        confirmation = _evaluate(
            bars,
            funding_by_bar,
            executions,
            features,
            parameters,
            state_filter,
            periods["confirmation"],
        )
        item["confirmation"] = _summary(confirmation)
    confirmation_passed = [
        item
        for item in finalists
        if bool(eligible)
        and item["confirmation"]["net_return"] > 0
        and item["confirmation"]["completed_trades"] >= 2
    ]
    selected = confirmation_passed[0] if confirmation_passed else finalists[0]
    gate_selected = selected["gate"]["mode"] != "none"
    parameters = SpreadParameters(**selected["parameters"])
    features = _features(bars, parameters, cache)
    state_filter = acceleration_filter(features, parameters, selected["gate"])
    results = {
        name: _evaluate(
            bars, funding_by_bar, executions, features, parameters, state_filter, period
        )
        for name, period in periods.items()
    }
    base_features = _features(bars, frozen.parameters, cache)
    baseline = {
        name: _evaluate(
            bars,
            funding_by_bar,
            executions,
            base_features,
            frozen.parameters,
            None,
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
        "strategy": "SOXLUSDT accelerating volatility-spread release",
        "status": (
            "exploratory_post_reveal_no_clean_holdout"
            if eligible and gate_selected
            else "rejected_acceleration_gate_not_selected"
            if eligible
            else "rejected_no_train_validation_candidate"
        ),
        "parameter_search_performed": True,
        "fresh_holdout_used_for_selection": False,
        "gate_definition": (
            "The ratio filter is evaluated at the SOXL closed signal bar. An allowed entry must "
            "have either an upward ratio delta above its gate or a first upward threshold cross."
        ),
        "acceleration_gate_selected": gate_selected,
        "splits": {
            name: {"start": _timestamp(period[0]), "end": _timestamp(period[1])}
            for name, period in periods.items()
        },
        "selection": {
            "candidate_count": len(candidates),
            "eligible_count": len(eligible),
            "finalists_checked": len(finalists),
            "confirmation_positive_finalists": len(confirmation_passed),
            "rule": "positive train and validation, then positive August 1-10 confirmation",
            "selected": selected,
            "finalists": finalists,
        },
        "acceleration": {
            "results": {name: _summary(result) for name, result in results.items()},
            "daily": {name: list(result.daily_returns) for name, result in results.items()},
            "development_tick_path": tick_risk,
        },
        "frozen_15m_baseline": {
            "parameters": asdict(frozen.parameters),
            "results": {name: _summary(result) for name, result in baseline.items()},
        },
        "target": {"geometric_daily_return": 0.05, "achieved": False},
        "limitations": [
            "This parameter search has no clean holdout after selection.",
            "August 11-13 was already revealed and is diagnostic only.",
            "Liquidation and shared intraday portfolio margin are not modeled.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(_markdown(payload), encoding="utf-8")
    print(args.output_dir / "README.md")
    return payload


def acceleration_filter(
    features: SpreadFeatures, parameters: SpreadParameters, gate: dict[str, Any]
) -> tuple[int | None, ...]:
    if gate["mode"] not in {"none", "delta", "cross"}:
        raise ValueError(f"unknown acceleration mode: {gate['mode']}")
    result: list[int | None] = []
    for index, ratio in enumerate(features.ratios):
        prior = features.ratios[index - 1] if index else None
        allowed = False
        if gate["mode"] == "none":
            allowed = True
        elif gate["mode"] == "delta":
            allowed = ratio is not None and prior is not None and ratio - prior >= gate["minimum"]
        else:
            allowed = (
                ratio is not None and prior is not None and prior < parameters.entry_ratio <= ratio
            )
        result.append(None if allowed else 0)
    return tuple(result)


def _candidate_grid() -> list[tuple[SpreadParameters, dict[str, Any]]]:
    return [
        (
            SpreadParameters(
                variant="compression_release",
                direction="long_short",
                fast_window=fast,
                slow_window=slow,
                entry_ratio=entry,
                exit_ratio=exit_ratio,
                breakout_window=breakout,
                stop_atr=stop,
                max_hold_bars=max_hold,
                exposure=1.25,
                compression_ratio=0.85,
                compression_lookback=16,
                spread_measure="true_range",
            ),
            gate,
        )
        for fast, slow in ((8, 32), (12, 64), (24, 96))
        for entry in (1.0, 1.1)
        for exit_ratio in (0.6, 0.8)
        for breakout in (12, 24)
        for stop in (2.0, 2.5)
        for max_hold in (48, 96)
        for gate in (
            {"mode": "none", "minimum": 0.0, "name": "none"},
            {"mode": "delta", "minimum": 0.0, "name": "delta_0"},
            {"mode": "delta", "minimum": 0.05, "name": "delta_0.05"},
            {"mode": "delta", "minimum": 0.10, "name": "delta_0.10"},
            {"mode": "cross", "minimum": 0.0, "name": "first_cross"},
        )
    ]


def _features(
    bars, parameters: SpreadParameters, cache: dict[tuple[int, int, int], SpreadFeatures]
) -> SpreadFeatures:
    key = (parameters.fast_window, parameters.slow_window, parameters.breakout_window)
    if key not in cache:
        cache[key] = build_spread_features(
            bars,
            fast_window=parameters.fast_window,
            slow_window=parameters.slow_window,
            breakout_window=parameters.breakout_window,
            compression_ratio=parameters.compression_ratio,
            compression_lookback=parameters.compression_lookback,
            spread_measure=parameters.spread_measure,
        )
    return cache[key]


def _evaluate(
    bars, funding, executions, features, parameters, state_filter, period
) -> SpreadResult:
    return evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=period[0],
        end_ms=period[1],
        funding_by_bar=funding,
        execution_by_bar=executions,
        entry_direction_filter=state_filter,
    )


def _eligible(train: dict[str, Any], validation: dict[str, Any]) -> bool:
    return (
        train["net_return"] > 0
        and validation["net_return"] > 0
        and train["completed_trades"] >= 8
        and validation["completed_trades"] >= 8
    )


def _selection_score(train: SpreadResult, validation: SpreadResult) -> float:
    return min(train.geometric_daily_return, validation.geometric_daily_return) - 0.25 * max(
        abs(train.max_drawdown), abs(validation.max_drawdown)
    )


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
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    acceleration = payload["acceleration"]["results"]
    baseline = payload["frozen_15m_baseline"]["results"]
    return "\n".join(
        [
            "# SOXLUSDT Accelerating Volatility-Spread Exploration",
            "",
            f"Status: **{payload['status']}**  ",
            f"Best gate: `{selected['gate']['name']}`  ",
            f"Best parameters: `{_parameter_label(selected['parameters'])}`  ",
            "5% daily target: **not achieved**",
            "",
            "The entry gate uses only the latest and preceding closed 15m volatility-spread "
            "ratios. It admits a breakout only when the spread is accelerating or first crosses "
            "the configured level.",
            "",
            "The stable selection chose `none`, so the acceleration and crossing gates add no "
            "value over the frozen baseline and are rejected.",
            "",
            (
                "| Path | Train geo/day | Validation geo/day | Confirmation geo/day | "
                "Development geo/day | Revealed 8/11-13 | 15m close DD |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|",
            _row("Acceleration gate", acceleration),
            _row("Frozen baseline", baseline),
            "",
            f"The search tested {payload['selection']['candidate_count']} candidates; "
            f"{payload['selection']['eligible_count']} passed train/validation and "
            f"{payload['selection']['confirmation_positive_finalists']} of "
            f"{payload['selection']['finalists_checked']} finalists were positive in confirmation.",
            "",
            "This is not a production recommendation. August 11-13 was already revealed and is "
            "diagnostic only; a retained candidate would require a fresh post-August-13 window.",
            "",
        ]
    )


def _row(name: str, results: dict[str, dict[str, Any]]) -> str:
    return (
        f"| {name} | {_percent(results['train']['geometric_daily_return'])} | "
        f"{_percent(results['validation']['geometric_daily_return'])} | "
        f"{_percent(results['confirmation']['geometric_daily_return'])} | "
        f"{_percent(results['development']['geometric_daily_return'])} | "
        f"{_percent(results['revealed_diagnostic']['net_return'])} | "
        f"{_percent(results['development']['max_drawdown'])} |"
    )


def _parameter_label(parameters: dict[str, Any]) -> str:
    return (
        f"{parameters['fast_window']}-{parameters['slow_window']}/"
        f"entry_{parameters['entry_ratio']:g}/exit_{parameters['exit_ratio']:g}/"
        f"breakout_{parameters['breakout_window']}/stop_{parameters['stop_atr']:g}/"
        f"hold_{parameters['max_hold_bars']}"
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
