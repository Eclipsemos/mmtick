#!/usr/bin/env python3
"""Measure whether the frozen SOXL spread return distribution can sustain 5% per day."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mastermind_tick.return_bootstrap import circular_block_bootstrap
from mastermind_tick.volatility_spread import (
    build_spread_features,
    daily_path_metrics,
    evaluate_spread,
)
from mastermind_tick.volatility_spread_forward import (
    load_forward_market,
    load_frozen_candidate,
)

EXPOSURES = (1.25, 2.0, 3.0, 5.0, 7.5, 10.0)
HORIZONS = (30, 90)
BLOCK_SIZES = (1, 3, 7)
TARGET_DAILY = 0.05
TICK_DRAWDOWN_GUARD = -0.30


def main() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("strategies/candidates/soxl_volatility_spread_true_range_v1.json"),
    )
    parser.add_argument(
        "--source-report",
        type=Path,
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14-v2/results.json"),
    )
    parser.add_argument("--simulations", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20_260_814)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14-v2"),
    )
    args = parser.parse_args()
    if args.simulations < 1:
        raise ValueError("simulations must be positive")

    candidate = load_frozen_candidate(args.candidate)
    source = json.loads(args.source_report.read_text(encoding="utf-8"))
    source_parameters = source["locked_candidates"]["true_range"]["parameters"]
    candidate_parameters = json.loads(args.candidate.read_text(encoding="utf-8"))["parameters"]
    if candidate_parameters != source_parameters:
        raise ValueError("frozen candidate parameters differ from the source report")

    development_start = _timestamp_ms(source["splits"]["development"]["start"])
    development_end = _timestamp_ms(source["splits"]["development"]["end"])
    continuous_end = _timestamp_ms(source["splits"]["continuous"]["end"])
    if development_start != candidate.continuous_replay_start_ms:
        raise ValueError("frozen replay start differs from the source report")

    bars, funding_by_bar, executions = load_forward_market(args.database, candidate)
    features = build_spread_features(
        bars,
        fast_window=candidate.parameters.fast_window,
        slow_window=candidate.parameters.slow_window,
        breakout_window=candidate.parameters.breakout_window,
        compression_ratio=candidate.parameters.compression_ratio,
        compression_lookback=candidate.parameters.compression_lookback,
        spread_measure=candidate.parameters.spread_measure,
    )
    source_exposures = {
        float(row["exposure"]): row for row in source["high_exposure_stress"]["rows"]
    }
    rows = []
    for exposure in EXPOSURES:
        parameters = replace(candidate.parameters, exposure=exposure)
        development = evaluate_spread(
            bars,
            features,
            parameters,
            start_ms=development_start,
            end_ms=development_end,
            funding_by_bar=funding_by_bar,
            execution_by_bar=executions,
            initial_equity=candidate.initial_equity,
            fee_bps=candidate.fee_bps_per_fill,
            slippage_bps=candidate.slippage_bps_per_fill,
            quantity_step=candidate.quantity_step,
        )
        source_row = source_exposures[exposure]
        expected_return = float(source_row["development"]["net_return"])
        if abs(development.net_return - expected_return) > 1e-12:
            raise ValueError(f"{exposure:g}x development replay differs from source report")

        first_partial_date = (
            datetime.fromtimestamp(development_start / 1000, UTC).date().isoformat()
        )
        complete_daily = [
            (day, value) for day, value in development.daily_returns if day > first_partial_date
        ]
        complete_values = [value for _, value in complete_daily]
        empirical = {
            **daily_path_metrics(complete_values),
            "complete_days": len(complete_values),
            "profitable_day_rate": sum(value > 0 for value in complete_values)
            / len(complete_values),
            "target_day_rate": sum(value >= TARGET_DAILY for value in complete_values)
            / len(complete_values),
            "days_at_or_above_target": sum(value >= TARGET_DAILY for value in complete_values),
        }
        bootstrap = []
        for horizon in HORIZONS:
            for block_size in BLOCK_SIZES:
                bootstrap.append(
                    circular_block_bootstrap(
                        complete_values,
                        horizon_days=horizon,
                        block_size=block_size,
                        simulations=args.simulations,
                        seed=args.seed + horizon * 100 + block_size,
                        target_geometric_daily_return=TARGET_DAILY,
                    )
                )

        continuous = evaluate_spread(
            bars,
            features,
            parameters,
            start_ms=development_start,
            end_ms=continuous_end,
            funding_by_bar=funding_by_bar,
            execution_by_bar=executions,
            initial_equity=candidate.initial_equity,
            fee_bps=candidate.fee_bps_per_fill,
            slippage_bps=candidate.slippage_bps_per_fill,
            quantity_step=candidate.quantity_step,
        )
        fresh_daily = [
            (day, value)
            for day, value in continuous.daily_returns
            if "2026-08-11" <= day <= "2026-08-13"
        ]
        rows.append(
            {
                "exposure": exposure,
                "development_empirical": empirical,
                "development_full_replay": {
                    "net_return": development.net_return,
                    "geometric_daily_return": development.geometric_daily_return,
                    "max_15m_close_drawdown": development.max_drawdown,
                    "completed_trades": development.completed_trades,
                },
                "development_tick_path": source_row["development_tick_path"],
                "fresh_holdout": {
                    **daily_path_metrics([value for _, value in fresh_daily]),
                    "days": len(fresh_daily),
                    "daily_returns": [{"date": day, "return": value} for day, value in fresh_daily],
                },
                "bootstrap": bootstrap,
            }
        )
        print(f"completed exposure {exposure:g}x", flush=True)

    guarded_rows = [
        row for row in rows if row["development_tick_path"]["max_drawdown"] >= TICK_DRAWDOWN_GUARD
    ]
    target_and_guard = [
        row
        for row in guarded_rows
        if row["development_full_replay"]["geometric_daily_return"] >= TARGET_DAILY
    ]
    payload = {
        "schema_version": 1,
        "strategy": candidate.id,
        "parameter_hash_sha256": candidate.parameter_hash,
        "parameter_search_performed": False,
        "source_period": {
            "development_start": source["splits"]["development"]["start"],
            "development_end": source["splits"]["development"]["end"],
            "first_partial_utc_day_excluded_from_bootstrap": first_partial_date,
            "fresh_holdout_start": "2026-08-11T00:00:00+00:00",
            "fresh_holdout_end": source["splits"]["fresh_holdout"]["end"],
        },
        "method": {
            "name": "circular moving-block bootstrap of complete UTC daily returns",
            "simulations_per_row": args.simulations,
            "seed": args.seed,
            "horizons_days": list(HORIZONS),
            "block_sizes_days": list(BLOCK_SIZES),
            "fresh_holdout_resampled": False,
            "liquidation_modeled": False,
            "warning": (
                "Bootstrap assumes the short development distribution can recur; it does not "
                "model regime shifts, liquidation, market impact, or exchange failure."
            ),
        },
        "target": {
            "geometric_daily_return": TARGET_DAILY,
            "required_30_day_equity_multiple": (1 + TARGET_DAILY) ** 30,
            "required_90_day_equity_multiple": (1 + TARGET_DAILY) ** 90,
            "diagnostic_tick_drawdown_guard": TICK_DRAWDOWN_GUARD,
        },
        "rows": rows,
        "decision": {
            "status": "target_not_supported",
            "tested_exposure_meeting_target_and_tick_drawdown_guard": bool(target_and_guard),
            "reason": (
                "No tested exposure reaches 5% development geometric daily return while "
                "remaining inside the diagnostic 30% Tick-path drawdown guard."
            ),
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "target_feasibility.json"
    markdown_path = args.output_dir / "target_feasibility.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(markdown_path)
    return payload


def _markdown(payload: dict[str, Any]) -> str:
    target = payload["target"]
    lines = [
        "# SOXLUSDT Volatility-Spread 5% Daily Feasibility",
        "",
        f"Parameter hash: `{payload['parameter_hash_sha256']}`  ",
        "Parameter search: **no**  ",
        f"Decision: **{payload['decision']['status']}**",
        "",
        "A 5% geometric daily return requires equity to grow by "
        f"`{target['required_30_day_equity_multiple']:.2f}x` in 30 days and "
        f"`{target['required_90_day_equity_multiple']:.2f}x` in 90 days.",
        "",
        "The bootstrap resamples only complete development UTC days through August 10. "
        "August 11-13 remains a disclosed diagnostic and is never resampled.",
        "",
        "## Exposure Results",
        "",
        (
            "| Exposure | Dev geo/day | Tick DD | Dev >=5% days | 90d P(geo>=5%) | "
            "90d median geo/day | 90d P(DD<=-50%) | 90d P(DD<=-80%) | Fresh return |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        ninety = [item for item in row["bootstrap"] if item["horizon_days"] == 90]
        target_low = min(item["probability_target_reached"] for item in ninety)
        target_high = max(item["probability_target_reached"] for item in ninety)
        median_geo = next(
            item["geometric_daily_return"]["median"] for item in ninety if item["block_size"] == 7
        )
        drawdown_50 = next(
            item["probability_daily_close_drawdown_50"]
            for item in ninety
            if item["block_size"] == 7
        )
        drawdown_80 = next(
            item["probability_daily_close_drawdown_80"]
            for item in ninety
            if item["block_size"] == 7
        )
        lines.append(
            f"| {row['exposure']:.2f}x | "
            f"{_pct(row['development_full_replay']['geometric_daily_return'])} | "
            f"{_pct(row['development_tick_path']['max_drawdown'])} | "
            f"{row['development_empirical']['days_at_or_above_target']} / "
            f"{row['development_empirical']['complete_days']} | "
            f"{_pct(target_low)}-{_pct(target_high)} | {_pct(median_geo)} | "
            f"{_pct(drawdown_50)} | {_pct(drawdown_80)} | "
            f"{_pct(row['fresh_holdout']['net_return'])} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            payload["decision"]["reason"],
            "",
            "The 30% Tick drawdown limit is a diagnostic guard, not a promise of safety. "
            "Liquidation is not modeled; daily-close bootstrap drawdowns understate "
            "intraday risk.",
            "",
            "These probabilities describe resampled versions of a short historical path. They are "
            "not probabilities of future profit under a changing market regime.",
            "",
        ]
    )
    return "\n".join(lines)


def _timestamp_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _pct(value: float) -> str:
    return f"{value:+.2%}"


if __name__ == "__main__":
    main()
