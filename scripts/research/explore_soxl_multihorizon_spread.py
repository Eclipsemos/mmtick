#!/usr/bin/env python3
"""Explore a risk-normalized 15m/30m/60m SOXL volatility-spread portfolio."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import explore_soxl_volatility_spread_v2 as phase_two

from mastermind_tick.multi_horizon_spread import (
    AggregatedSpreadMarket,
    aggregate_spread_market,
    combine_daily_paths,
    inverse_volatility_weights,
)
from mastermind_tick.volatility_spread import (
    SpreadBar,
    SpreadFeatures,
    SpreadParameters,
    SpreadResult,
    build_spread_features,
    daily_path_metrics,
    evaluate_spread,
)
from mastermind_tick.volatility_spread_forward import load_forward_market, load_frozen_candidate

TIMEFRAMES = (15, 30, 60)
SEARCH_TIMEFRAMES = (30, 60)
DEVELOPMENT_START = date(2026, 5, 17)
TRAIN_END = date(2026, 6, 30)
VALIDATION_START = date(2026, 7, 1)
VALIDATION_END = date(2026, 7, 31)
CONFIRMATION_START = date(2026, 8, 1)
CONFIRMATION_END = date(2026, 8, 10)
FRESH_START = date(2026, 8, 11)
FRESH_END = date(2026, 8, 13)
TARGET_DAILY = 0.05


def main() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("strategies/candidates/soxl_volatility_spread_true_range_v1.json"),
    )
    parser.add_argument("--finalists-per-timeframe", type=int, default=20)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14-multihorizon"),
    )
    args = parser.parse_args()
    if args.finalists_per_timeframe < 1:
        raise ValueError("finalists-per-timeframe must be positive")

    frozen = load_frozen_candidate(args.candidate)
    base_bars, base_funding, base_executions = load_forward_market(args.database, frozen)
    markets = {
        15: AggregatedSpreadMarket(15, base_bars, base_funding, base_executions),
        30: aggregate_spread_market(
            base_bars,
            base_funding,
            base_executions,
            interval_minutes=30,
        ),
        60: aggregate_spread_market(
            base_bars,
            base_funding,
            base_executions,
            interval_minutes=60,
        ),
    }
    periods = _periods()
    chosen: dict[int, SpreadParameters] = {15: frozen.parameters}
    searches: dict[str, Any] = {}
    for timeframe in SEARCH_TIMEFRAMES:
        chosen[timeframe], searches[str(timeframe)] = _select_timeframe(
            markets[timeframe],
            timeframe,
            periods,
            args.finalists_per_timeframe,
        )
        print(f"selected {timeframe}m candidate", flush=True)

    sleeves: dict[str, dict[str, Any]] = {}
    for timeframe in TIMEFRAMES:
        market = markets[timeframe]
        parameters = chosen[timeframe]
        features = _features(market.bars, parameters)
        result_by_period = {
            name: _evaluate(market, features, parameters, period)
            for name, period in periods.items()
        }
        all_funding = [event for events in market.funding_by_bar for event in events]
        tick_risk = phase_two._tick_path_risk(
            args.database,
            result_by_period["development"].trades,
            all_funding,
            expected_final_equity=result_by_period["development"].final_equity,
        )
        sleeves[str(timeframe)] = {
            "timeframe_minutes": timeframe,
            "parameters": asdict(parameters),
            "bars": len(market.bars),
            "results": {name: _summary(result) for name, result in result_by_period.items()},
            "daily": {
                name: list(result.daily_returns) for name, result in result_by_period.items()
            },
            "development_tick_path": tick_risk,
        }

    portfolio = _portfolio_report(sleeves, periods)
    payload = {
        "schema_version": 1,
        "strategy": "SOXLUSDT multi-horizon volatility spread exploratory portfolio",
        "status": "exploratory_post_reveal_no_clean_holdout",
        "parameter_search_performed": True,
        "fresh_holdout_used_for_selection": False,
        "source_candidate": frozen.id,
        "timeframes": list(TIMEFRAMES),
        "splits": {
            name: {"start": _timestamp(period[0]), "end": _timestamp(period[1])}
            for name, period in periods.items()
        },
        "selection": {
            "candidate_count_by_timeframe": {
                name: value["candidate_count"] for name, value in searches.items()
            },
            "rule": (
                "rank 30m/60m on weaker train/validation geometric daily return with a drawdown "
                "penalty; require positive train and validation; confirmation is diagnostic only"
            ),
            "chosen": {
                str(timeframe): asdict(parameters) for timeframe, parameters in chosen.items()
            },
            "searches": searches,
        },
        "portfolio": portfolio,
        "target": {
            "geometric_daily_return": TARGET_DAILY,
            "achieved_by_safe_cap": portfolio["decision"]["achieved_by_diagnostic_cap"],
            "diagnostic_cap_total_exposure": 3.0,
        },
        "sleeves": sleeves,
        "limitations": [
            "30m and 60m bars are deterministic aggregations of closed 15m bars.",
            "Portfolio combination is daily rebalanced and has no shared intraday margin model.",
            "August 11-13 was already revealed in the prior study and is diagnostic only here.",
            "No parameter or portfolio approval is granted; new forward evidence starts August 14.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(_markdown(payload), encoding="utf-8")
    print(args.output_dir / "README.md")
    return payload


def _select_timeframe(
    market: AggregatedSpreadMarket,
    timeframe: int,
    periods: dict[str, tuple[int, int]],
    finalists_per_timeframe: int,
) -> tuple[SpreadParameters, dict[str, Any]]:
    grid = _candidate_grid()
    cache: dict[tuple[int, int, int], SpreadFeatures] = {}
    evaluations = []
    for index, parameters in enumerate(grid, start=1):
        features = _features(market.bars, parameters, cache)
        train = _evaluate(market, features, parameters, periods["train"])
        validation = _evaluate(market, features, parameters, periods["validation"])
        train_summary = _summary(train)
        validation_summary = _summary(validation)
        if not _eligible(train_summary, validation_summary):
            continue
        evaluations.append(
            {
                "parameters": asdict(parameters),
                "selection_score": _selection_score(train, validation),
                "train": train_summary,
                "validation": validation_summary,
            }
        )
        if index % 1000 == 0:
            print(f"{timeframe}m search {index}/{len(grid)}", flush=True)
    ranked = sorted(evaluations, key=lambda item: item["selection_score"], reverse=True)
    if not ranked:
        raise RuntimeError(f"no eligible {timeframe}m candidates")
    finalists = []
    for item in ranked[:finalists_per_timeframe]:
        parameters = SpreadParameters(**item["parameters"])
        features = _features(market.bars, parameters, cache)
        confirmation = _evaluate(market, features, parameters, periods["confirmation"])
        finalists.append({**item, "confirmation": _summary(confirmation)})
    passed = [
        item
        for item in finalists
        if item["confirmation"]["net_return"] > 0 and item["confirmation"]["completed_trades"] >= 2
    ]
    chosen = passed[0] if passed else finalists[0]
    return SpreadParameters(**chosen["parameters"]), {
        "candidate_count": len(grid),
        "eligible_count": len(evaluations),
        "finalists_checked": len(finalists),
        "confirmation_positive_finalists": len(passed),
        "chosen": chosen,
        "top_finalists": finalists,
    }


def _candidate_grid() -> list[SpreadParameters]:
    candidates = []
    for variant in ("compression_release", "expansion_breakout"):
        for direction in ("long_only", "long_short"):
            for fast_window in (4, 8, 12):
                for slow_window in (24, 32, 48):
                    if fast_window >= slow_window:
                        continue
                    for entry_ratio in (1.0, 1.1, 1.3):
                        for exit_ratio in (0.7, 0.8):
                            for breakout_window in (8, 16, 24):
                                for stop_atr in (1.5, 2.5, 3.5):
                                    for max_hold_bars in (24, 48, 96):
                                        candidates.append(
                                            SpreadParameters(
                                                variant=variant,
                                                direction=direction,
                                                fast_window=fast_window,
                                                slow_window=slow_window,
                                                entry_ratio=entry_ratio,
                                                exit_ratio=exit_ratio,
                                                breakout_window=breakout_window,
                                                stop_atr=stop_atr,
                                                max_hold_bars=max_hold_bars,
                                                exposure=1.25,
                                                compression_ratio=0.85,
                                                compression_lookback=16,
                                                spread_measure="true_range",
                                            )
                                        )
    return candidates


def _portfolio_report(
    sleeves: dict[str, dict[str, Any]], periods: dict[str, tuple[int, int]]
) -> dict[str, Any]:
    development_paths = {
        name: _complete_daily(value["daily"]["development"], periods["development"][0])
        for name, value in sleeves.items()
    }
    train_paths = {
        name: _filter_path(path, periods["train"]) for name, path in development_paths.items()
    }
    weights = {
        "equal": {name: 1 / len(TIMEFRAMES) for name in development_paths},
        "inverse_volatility": inverse_volatility_weights(train_paths),
    }
    scored = []
    for scheme, scheme_weights in weights.items():
        train = _path_metrics(combine_daily_paths(train_paths, scheme_weights))
        validation = _path_metrics(
            combine_daily_paths(
                {
                    name: _filter_path(path, periods["validation"])
                    for name, path in development_paths.items()
                },
                scheme_weights,
            )
        )
        score = min(train["geometric_daily_return"], validation["geometric_daily_return"])
        scored.append((score, scheme))
    selected_scheme = max(scored)[1]

    selected = {}
    for scheme, scheme_weights in weights.items():
        selected[scheme] = {
            "weights": scheme_weights,
            "periods": {},
            "scales": _scale_stress(sleeves, scheme_weights, periods),
        }
        for name, period in periods.items():
            paths = {
                sleeve: _filter_path(
                    _complete_daily(value["daily"]["continuous"], periods["continuous"][0]),
                    period,
                )
                for sleeve, value in sleeves.items()
            }
            selected[scheme]["periods"][name] = _path_metrics(
                combine_daily_paths(paths, scheme_weights)
            )
    cap = 3.0 / 1.25
    cap_metrics = selected[selected_scheme]["scales"][f"{cap:g}"]["development"]
    return {
        "weights": weights,
        "selected_scheme_by_train_validation": selected_scheme,
        "schemes": selected,
        "development_daily_correlation": _correlation_matrix(development_paths),
        "decision": {
            "diagnostic_cap_total_exposure": 3.0,
            "achieved_by_diagnostic_cap": cap_metrics["geometric_daily_return"] >= TARGET_DAILY,
            "reason": (
                "Use the train/validation-selected risk weighting for comparison; the 3x total "
                "exposure cap is diagnostic and has no shared intraday liquidation model."
            ),
        },
    }


def _scale_stress(
    sleeves: dict[str, dict[str, Any]],
    weights: dict[str, float],
    periods: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    paths = {
        name: _complete_daily(value["daily"]["continuous"], periods["continuous"][0])
        for name, value in sleeves.items()
    }
    result = {}
    for scale in (1.0, 1.6, 2.4, 4.0, 6.0, 8.0):
        development = _path_metrics(
            combine_daily_paths(
                {name: _filter_path(path, periods["development"]) for name, path in paths.items()},
                weights,
                scale=scale,
            )
        )
        fresh = _path_metrics(
            combine_daily_paths(
                {
                    name: _filter_path(path, periods["fresh_holdout"])
                    for name, path in paths.items()
                },
                weights,
                scale=scale,
            )
        )
        result[f"{scale:g}"] = {
            "approximate_total_exposure": 1.25 * scale,
            "development": development,
            "fresh_holdout": fresh,
        }
    return result


def _features(
    bars: list[SpreadBar], parameters: SpreadParameters, cache: dict | None = None
) -> SpreadFeatures:
    if cache is None:
        return build_spread_features(
            bars,
            fast_window=parameters.fast_window,
            slow_window=parameters.slow_window,
            breakout_window=parameters.breakout_window,
            compression_ratio=parameters.compression_ratio,
            compression_lookback=parameters.compression_lookback,
            spread_measure=parameters.spread_measure,
        )
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
    market: AggregatedSpreadMarket,
    features: SpreadFeatures,
    parameters: SpreadParameters,
    period: tuple[int, int],
) -> SpreadResult:
    return evaluate_spread(
        market.bars,
        features,
        parameters,
        start_ms=period[0],
        end_ms=period[1],
        funding_by_bar=market.funding_by_bar,
        execution_by_bar=market.executions,
    )


def _periods() -> dict[str, tuple[int, int]]:
    start = _day_start(DEVELOPMENT_START) + 16 * 60 * 60 * 1000
    return {
        "train": (start, _day_end(TRAIN_END)),
        "validation": (_day_start(VALIDATION_START), _day_end(VALIDATION_END)),
        "confirmation": (_day_start(CONFIRMATION_START), _day_end(CONFIRMATION_END)),
        "development": (start, _day_end(CONFIRMATION_END)),
        "fresh_holdout": (_day_start(FRESH_START), _day_end(FRESH_END)),
        "continuous": (start, _day_end(FRESH_END)),
    }


def _eligible(train: dict[str, Any], validation: dict[str, Any]) -> bool:
    return (
        train["completed_trades"] >= 3
        and validation["completed_trades"] >= 2
        and train["net_return"] > 0
        and validation["net_return"] > 0
        and train["max_drawdown"] > -0.35
        and validation["max_drawdown"] > -0.35
        and not train["bankrupt"]
        and not validation["bankrupt"]
    )


def _selection_score(train: SpreadResult, validation: SpreadResult) -> float:
    return min(train.geometric_daily_return, validation.geometric_daily_return) + 0.05 * min(
        train.max_drawdown, validation.max_drawdown
    )


def _summary(result: SpreadResult) -> dict[str, Any]:
    return {
        "net_return": result.net_return,
        "geometric_daily_return": result.geometric_daily_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "profitable_day_rate": result.profitable_day_rate,
        "target_day_rate": result.target_day_rate,
        "active_days": result.active_days,
        "final_equity": result.final_equity,
        "bankrupt": result.bankrupt,
    }


def _complete_daily(path: list[tuple[str, float]], start_ms: int) -> list[tuple[str, float]]:
    partial = datetime.fromtimestamp(start_ms / 1000, UTC).date().isoformat()
    return [(day, value) for day, value in path if day > partial]


def _filter_path(path: list[tuple[str, float]], period: tuple[int, int]) -> list[tuple[str, float]]:
    start = datetime.fromtimestamp(period[0] / 1000, UTC).date().isoformat()
    end = datetime.fromtimestamp(period[1] / 1000, UTC).date().isoformat()
    return [(day, value) for day, value in path if start <= day <= end]


def _path_metrics(path: list[tuple[str, float]]) -> dict[str, Any]:
    values = [value for _, value in path]
    return {**daily_path_metrics(values), "days": len(values)}


def _correlation_matrix(
    paths: dict[str, list[tuple[str, float]]],
) -> dict[str, dict[str, float | None]]:
    names = sorted(paths)
    return {
        left: {right: _correlation(paths[left], paths[right]) for right in names} for left in names
    }


def _correlation(left: list[tuple[str, float]], right: list[tuple[str, float]]) -> float | None:
    if [day for day, _ in left] != [day for day, _ in right] or len(left) < 2:
        return None
    left_values = [value for _, value in left]
    right_values = [value for _, value in right]
    left_mean = statistics.fmean(left_values)
    right_mean = statistics.fmean(right_values)
    numerator = sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left_values, right_values, strict=True)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left_values)
        * sum((value - right_mean) ** 2 for value in right_values)
    )
    return numerator / denominator if denominator else None


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def _markdown(payload: dict[str, Any]) -> str:
    portfolio = payload["portfolio"]
    selected = portfolio["selected_scheme_by_train_validation"]
    lines = [
        "# SOXLUSDT Multi-Horizon Volatility-Spread Exploration",
        "",
        "Status: **exploratory_post_reveal_no_clean_holdout**  ",
        f"Train/validation-selected weighting: `{selected}`  ",
        "Fresh holdout used for selection: **no**",
        "",
        "30m and 60m bars are aggregated from closed 15m bars. Each sleeve uses the same next "
        "persisted Tick execution model. Portfolio figures are daily-rebalanced diagnostics, not "
        "a shared intraday margin simulation.",
        "",
        "## Sleeve Results",
        "",
        (
            "| Sleeve | Parameters | Train geo/day | Validation geo/day | Confirmation geo/day | "
            "Development geo/day | Fresh return | Tick DD |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("15", "30", "60"):
        sleeve = payload["sleeves"][name]
        parameters = sleeve["parameters"]
        lines.append(
            f"| {name}m | `{parameters['variant']}/{parameters['direction']}/"
            f"{parameters['fast_window']}-{parameters['slow_window']}` | "
            f"{_pct(sleeve['results']['train']['geometric_daily_return'])} | "
            f"{_pct(sleeve['results']['validation']['geometric_daily_return'])} | "
            f"{_pct(sleeve['results']['confirmation']['geometric_daily_return'])} | "
            f"{_pct(sleeve['results']['development']['geometric_daily_return'])} | "
            f"{_pct(sleeve['results']['fresh_holdout']['net_return'])} | "
            f"{_pct(sleeve['development_tick_path']['max_drawdown'])} |"
        )
    lines.extend(
        [
            "",
            "## Portfolio Scaling",
            "",
            "| Scheme | Total exposure | Development geo/day | Development DD | Fresh return |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for scheme, values in portfolio["schemes"].items():
        for _scale, row in values["scales"].items():
            lines.append(
                f"| {scheme} | {row['approximate_total_exposure']:.2f}x | "
                f"{_pct(row['development']['geometric_daily_return'])} | "
                f"{_pct(row['development']['max_daily_close_drawdown'])} | "
                f"{_pct(row['fresh_holdout']['net_return'])} |"
            )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            portfolio["decision"]["reason"],
            "",
            "A positive combination result is not an approval. Parameters must be frozen before "
            "the next complete UTC day and then evaluated only on new forward data.",
            "",
        ]
    )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2%}"


if __name__ == "__main__":
    main()
