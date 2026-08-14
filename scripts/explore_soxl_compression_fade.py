#!/usr/bin/env python3
"""Explore a compressed-volatility channel mean-reversion spread strategy."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import explore_soxl_volatility_spread_v2 as phase_two

from mastermind_tick.multi_horizon_spread import combine_daily_paths, inverse_volatility_weights
from mastermind_tick.volatility_spread import (
    SpreadFeatures,
    SpreadParameters,
    SpreadResult,
    build_spread_features,
    daily_path_metrics,
    evaluate_spread,
)
from mastermind_tick.volatility_spread_forward import load_forward_market, load_frozen_candidate

TARGET_DAILY = 0.05
DEVELOPMENT_START = date(2026, 5, 17)
TRAIN_END = date(2026, 6, 30)
VALIDATION_START = date(2026, 7, 1)
VALIDATION_END = date(2026, 7, 31)
CONFIRMATION_START = date(2026, 8, 1)
CONFIRMATION_END = date(2026, 8, 10)
FRESH_START = date(2026, 8, 11)
FRESH_END = date(2026, 8, 13)


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
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14-compression-fade"),
    )
    args = parser.parse_args()
    frozen = load_frozen_candidate(args.candidate)
    bars, funding_by_bar, executions = load_forward_market(args.database, frozen)
    periods = _periods()
    cache: dict[tuple[int, int, int], SpreadFeatures] = {}
    evaluations = []
    grid = _candidate_grid()
    for index, parameters in enumerate(grid, start=1):
        features = _features(bars, parameters, cache)
        train = _evaluate(bars, funding_by_bar, executions, features, parameters, periods["train"])
        validation = _evaluate(
            bars, funding_by_bar, executions, features, parameters, periods["validation"]
        )
        train_summary = _summary(train)
        validation_summary = _summary(validation)
        if not _eligible(train_summary, validation_summary):
            continue
        evaluations.append(
            {
                "parameters": asdict(parameters),
                "selection_score": _score(train, validation),
                "train": train_summary,
                "validation": validation_summary,
            }
        )
        if index % 500 == 0:
            print(f"compression-fade search {index}/{len(grid)}", flush=True)
    ranked = sorted(evaluations, key=lambda item: item["selection_score"], reverse=True)
    if not ranked:
        raise RuntimeError("no eligible compression-fade candidates")
    finalists = []
    for item in ranked[: args.finalists]:
        parameters = SpreadParameters(**item["parameters"])
        features = _features(bars, parameters, cache)
        confirmation = _evaluate(
            bars, funding_by_bar, executions, features, parameters, periods["confirmation"]
        )
        finalists.append({**item, "confirmation": _summary(confirmation)})
    passed = [
        item
        for item in finalists
        if item["confirmation"]["net_return"] > 0 and item["confirmation"]["completed_trades"] >= 2
    ]
    selected = (passed or finalists)[0]
    fade_parameters = SpreadParameters(**selected["parameters"])
    fade_features = _features(bars, fade_parameters, cache)
    fade_results = {
        name: _evaluate(
            bars,
            funding_by_bar,
            executions,
            fade_features,
            fade_parameters,
            period,
        )
        for name, period in periods.items()
    }
    base_features = _features(bars, frozen.parameters, cache)
    base_results = {
        name: _evaluate(
            bars,
            funding_by_bar,
            executions,
            base_features,
            frozen.parameters,
            period,
        )
        for name, period in periods.items()
    }
    combo = _combo_report(fade_results, base_results, periods)
    all_funding = [event for events in funding_by_bar for event in events]
    tick_risk = {
        "compression_fade": phase_two._tick_path_risk(
            args.database,
            fade_results["development"].trades,
            all_funding,
            expected_final_equity=fade_results["development"].final_equity,
        ),
        "frozen_breakout": phase_two._tick_path_risk(
            args.database,
            base_results["development"].trades,
            all_funding,
            expected_final_equity=base_results["development"].final_equity,
        ),
    }
    payload = {
        "schema_version": 1,
        "strategy": "SOXLUSDT compression-fade volatility spread",
        "status": "exploratory_post_reveal_no_clean_holdout",
        "parameter_search_performed": True,
        "fresh_holdout_used_for_selection": False,
        "splits": {
            name: {"start": _timestamp(period[0]), "end": _timestamp(period[1])}
            for name, period in periods.items()
        },
        "selection": {
            "candidate_count": len(grid),
            "eligible_count": len(evaluations),
            "finalists_checked": len(finalists),
            "confirmation_positive_finalists": len(passed),
            "rule": (
                "positive train and validation, then positive August 1-10 confirmation "
                "with at least two trades"
            ),
            "selected": selected,
            "top_finalists": finalists,
        },
        "compression_fade": {
            "parameters": asdict(fade_parameters),
            "results": {name: _summary(result) for name, result in fade_results.items()},
            "daily": {name: list(result.daily_returns) for name, result in fade_results.items()},
        },
        "frozen_breakout": {
            "parameters": asdict(frozen.parameters),
            "results": {name: _summary(result) for name, result in base_results.items()},
            "daily": {name: list(result.daily_returns) for name, result in base_results.items()},
        },
        "tick_path_risk": tick_risk,
        "combo": combo,
        "target": {
            "geometric_daily_return": TARGET_DAILY,
            "achieved": False,
            "achieved_by_3x_total_exposure": False,
            "linear_daily_path_reaches_5_at_10x": combo["decision"][
                "linear_daily_path_reaches_5_at_10x"
            ],
        },
        "limitations": [
            "Compression-fade is a post-hoc exploratory variant and is not a production strategy.",
            "August 11-13 was already revealed and is diagnostic only.",
            "Combo paths are daily rebalanced; shared intraday margin and liquidation are "
            "not modeled.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(_markdown(payload), encoding="utf-8")
    print(args.output_dir / "README.md")
    return payload


def _candidate_grid() -> list[SpreadParameters]:
    return [
        SpreadParameters(
            variant="compression_fade",
            direction=direction,
            fast_window=fast,
            slow_window=slow,
            entry_ratio=entry,
            exit_ratio=exit_ratio,
            breakout_window=breakout,
            stop_atr=stop,
            max_hold_bars=hold,
            exposure=1.25,
            compression_ratio=0.85,
            compression_lookback=16,
            spread_measure="true_range",
        )
        for direction in ("long_only", "long_short")
        for fast in (8, 12, 24)
        for slow in (32, 64)
        for entry in (0.75, 0.9, 1.0)
        for exit_ratio in (1.0, 1.2)
        for breakout in (8, 16)
        for stop in (1.0, 1.5, 2.5)
        for hold in (12, 24, 48)
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


def _evaluate(bars, funding, executions, features, parameters, period) -> SpreadResult:
    return evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=period[0],
        end_ms=period[1],
        funding_by_bar=funding,
        execution_by_bar=executions,
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
        train["completed_trades"] >= 5
        and validation["completed_trades"] >= 3
        and train["net_return"] > 0
        and validation["net_return"] > 0
        and train["max_drawdown"] > -0.35
        and validation["max_drawdown"] > -0.35
        and not train["bankrupt"]
        and not validation["bankrupt"]
    )


def _score(train: SpreadResult, validation: SpreadResult) -> float:
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


def _combo_report(fade, base, periods) -> dict[str, Any]:
    paths = {
        "fade": _complete_path(fade["continuous"].daily_returns, periods["continuous"][0]),
        "breakout": _complete_path(base["continuous"].daily_returns, periods["continuous"][0]),
    }
    train_paths = {name: _filter(path, periods["train"]) for name, path in paths.items()}
    weights = {
        f"fade_{fade_weight / 10:.1f}": {
            "fade": fade_weight / 10,
            "breakout": 1 - fade_weight / 10,
        }
        for fade_weight in range(11)
    }
    weights["inverse_volatility"] = inverse_volatility_weights(train_paths)
    scores = {}
    schemes = {}
    for scheme, scheme_weights in weights.items():
        train = _metrics(
            combine_daily_paths(
                {name: _filter(path, periods["train"]) for name, path in paths.items()},
                scheme_weights,
            )
        )
        validation = _metrics(
            combine_daily_paths(
                {name: _filter(path, periods["validation"]) for name, path in paths.items()},
                scheme_weights,
            )
        )
        scores[scheme] = min(
            train["geometric_daily_return"], validation["geometric_daily_return"]
        ) + 0.05 * min(train["max_daily_close_drawdown"], validation["max_daily_close_drawdown"])
        periods_metrics = {}
        for period_name, period in periods.items():
            periods_metrics[period_name] = _metrics(
                combine_daily_paths(
                    {name: _filter(path, period) for name, path in paths.items()}, scheme_weights
                )
            )
        scales = {}
        for scale in (1.0, 1.6, 2.4, 4.0, 6.0, 8.0):
            scales[f"{scale:g}"] = {
                "total_exposure": 1.25 * scale,
                "development": _metrics(
                    combine_daily_paths(
                        {
                            name: _filter(path, periods["development"])
                            for name, path in paths.items()
                        },
                        scheme_weights,
                        scale=scale,
                    )
                ),
                "fresh_holdout": _metrics(
                    combine_daily_paths(
                        {
                            name: _filter(path, periods["fresh_holdout"])
                            for name, path in paths.items()
                        },
                        scheme_weights,
                        scale=scale,
                    )
                ),
            }
        schemes[scheme] = {"weights": scheme_weights, "periods": periods_metrics, "scales": scales}
    selected = max(scores, key=scores.get)
    return {
        "selected_scheme": selected,
        "weights": weights,
        "schemes": schemes,
        "correlation": _correlation(paths["fade"], paths["breakout"]),
        "decision": {
            "achieved": False,
            "achieved_by_3x": False,
            "linear_daily_path_reaches_5_at_10x": schemes[selected]["scales"]["8"]["development"][
                "geometric_daily_return"
            ]
            >= TARGET_DAILY,
            "reason": (
                "Daily portfolio scaling is diagnostic only and cannot establish a 5% target "
                "without a shared intraday margin and liquidation replay."
            ),
        },
    }


def _complete_path(path, start_ms):
    partial = datetime.fromtimestamp(start_ms / 1000, UTC).date().isoformat()
    return [(day, value) for day, value in path if day > partial]


def _filter(path, period):
    start = datetime.fromtimestamp(period[0] / 1000, UTC).date().isoformat()
    end = datetime.fromtimestamp(period[1] / 1000, UTC).date().isoformat()
    return [(day, value) for day, value in path if start <= day <= end]


def _metrics(path):
    return {**daily_path_metrics([value for _, value in path]), "days": len(path)}


def _correlation(left, right):
    left_values = [value for _, value in left]
    right_values = [value for _, value in right]
    if len(left_values) < 2 or [day for day, _ in left] != [day for day, _ in right]:
        return None
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left_values, right_values, strict=True)
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
    selected = payload["selection"]["selected"]
    fade = payload["compression_fade"]
    base = payload["frozen_breakout"]
    combo = payload["combo"]
    lines = [
        "# SOXLUSDT Compression-Fade Volatility-Spread Exploration",
        "",
        "Status: **exploratory_post_reveal_no_clean_holdout**  ",
        f"Selected variant: `{selected['parameters']['direction']}/"
        f"{selected['parameters']['fast_window']}-{selected['parameters']['slow_window']}/"
        f"entry_{selected['parameters']['entry_ratio']}/"
        f"exit_{selected['parameters']['exit_ratio']}`  ",
        "Fresh holdout used for selection: **no**",
        "",
        "Compression-fade enters against a compressed channel boundary and exits at the prior "
        "mean, volatility expansion, a stop, or maximum hold. Signals use only prior bars.",
        "",
        (
            "| Path | Train geo/day | Validation geo/day | Confirmation geo/day | "
            "Development geo/day | Fresh return | Trades |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
        (
            f"| Compression fade | {_pct(fade['results']['train']['geometric_daily_return'])} | "
            f"{_pct(fade['results']['validation']['geometric_daily_return'])} | "
            f"{_pct(fade['results']['confirmation']['geometric_daily_return'])} | "
            f"{_pct(fade['results']['development']['geometric_daily_return'])} | "
            f"{_pct(fade['results']['fresh_holdout']['net_return'])} | "
            f"{fade['results']['development']['completed_trades']} |"
        ),
        (
            f"| Frozen breakout | {_pct(base['results']['train']['geometric_daily_return'])} | "
            f"{_pct(base['results']['validation']['geometric_daily_return'])} | "
            f"{_pct(base['results']['confirmation']['geometric_daily_return'])} | "
            f"{_pct(base['results']['development']['geometric_daily_return'])} | "
            f"{_pct(base['results']['fresh_holdout']['net_return'])} | "
            f"{base['results']['development']['completed_trades']} |"
        ),
        "",
        (
            f"Train-selected combination: `{combo['selected_scheme']}`, "
            f"correlation `{combo['correlation']:.3f}`."
        ),
        "",
        "| Total exposure | Development geo/day | Development DD | Fresh return |",
        "|---:|---:|---:|---:|",
    ]
    selected_scheme = combo["schemes"][combo["selected_scheme"]]
    for _scale, row in selected_scheme["scales"].items():
        lines.append(
            f"| {row['total_exposure']:.2f}x | "
            f"{_pct(row['development']['geometric_daily_return'])} | "
            f"{_pct(row['development']['max_daily_close_drawdown'])} | "
            f"{_pct(row['fresh_holdout']['net_return'])} |"
        )
    lines.extend(
        [
            "",
            "The compression-fade result is not a production recommendation. The 5% target is "
            "unmet. Daily linear scaling may cross 5% in development but is not an executable "
            "shared-margin replay and does not change that conclusion.",
            "",
        ]
    )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2%}"


if __name__ == "__main__":
    main()
