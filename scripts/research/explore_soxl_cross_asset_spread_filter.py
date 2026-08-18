#!/usr/bin/env python3
"""Explore BTC volatility/trend state filters for the frozen SOXL spread signal."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import explore_soxl_volatility_spread_v2 as phase_two

from mastermind_tick.volatility_spread import (
    SpreadBar,
    SpreadFeatures,
    SpreadResult,
    build_spread_features,
    evaluate_spread,
)
from mastermind_tick.volatility_spread_forward import load_forward_market, load_frozen_candidate

BTC_INSTRUMENT = "btc_perp"
TRAIN_END = date(2026, 6, 30)
VALIDATION_START = date(2026, 7, 1)
VALIDATION_END = date(2026, 7, 31)
CONFIRMATION_START = date(2026, 8, 1)
CONFIRMATION_END = date(2026, 8, 10)
DIAGNOSTIC_START = date(2026, 8, 11)
DIAGNOSTIC_END = date(2026, 8, 11)


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
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14-cross-asset"),
    )
    args = parser.parse_args()

    frozen = load_frozen_candidate(args.candidate)
    soxl_bars, soxl_funding, soxl_executions = load_forward_market(args.database, frozen)
    btc_bars = _load_bars(args.database, BTC_INSTRUMENT)
    if not btc_bars:
        raise RuntimeError("BTC 15m bars are required for the cross-asset experiment")
    btc_features = build_spread_features(
        btc_bars,
        fast_window=8,
        slow_window=32,
        breakout_window=24,
        compression_ratio=0.85,
        compression_lookback=16,
        spread_measure="true_range",
    )
    periods = _periods(frozen.continuous_replay_start_ms)
    candidates = _candidate_grid()
    evaluated = []
    for candidate in candidates:
        state_filter = _state_filter(soxl_bars, btc_bars, btc_features, candidate)
        train = _evaluate(
            soxl_bars,
            soxl_funding,
            soxl_executions,
            frozen.parameters,
            state_filter,
            periods["train"],
        )
        validation = _evaluate(
            soxl_bars,
            soxl_funding,
            soxl_executions,
            frozen.parameters,
            state_filter,
            periods["validation"],
        )
        evaluated.append(
            {
                "candidate": candidate,
                "selection_score": _selection_score(train, validation),
                "train": _summary(train),
                "validation": _summary(validation),
            }
        )
    eligible = [
        item
        for item in evaluated
        if item["train"]["net_return"] > 0
        and item["validation"]["net_return"] > 0
        and item["validation"]["completed_trades"] >= 4
    ]
    finalists = sorted(eligible, key=lambda item: item["selection_score"], reverse=True)[:12]
    if not finalists:
        raise RuntimeError("no BTC cross-asset filter passed the train/validation gates")
    for item in finalists:
        state_filter = _state_filter(soxl_bars, btc_bars, btc_features, item["candidate"])
        confirmation = _evaluate(
            soxl_bars,
            soxl_funding,
            soxl_executions,
            frozen.parameters,
            state_filter,
            periods["confirmation"],
        )
        item["confirmation"] = _summary(confirmation)
    confirmation_passed = [
        item
        for item in finalists
        if item["confirmation"]["net_return"] > 0 and item["confirmation"]["completed_trades"] >= 2
    ]
    selected = confirmation_passed[0] if confirmation_passed else finalists[0]
    selected_filter = _state_filter(soxl_bars, btc_bars, btc_features, selected["candidate"])
    selected_results = {
        name: _evaluate(
            soxl_bars,
            soxl_funding,
            soxl_executions,
            frozen.parameters,
            selected_filter,
            period,
        )
        for name, period in periods.items()
    }
    fixed_results = {
        name: _evaluate(
            soxl_bars,
            soxl_funding,
            soxl_executions,
            frozen.parameters,
            None,
            period,
        )
        for name, period in periods.items()
    }
    tick_risk = phase_two._tick_path_risk(
        args.database,
        selected_results["development"].trades,
        [event for events in soxl_funding for event in events],
        expected_final_equity=selected_results["development"].final_equity,
    )
    payload = {
        "schema_version": 1,
        "strategy": "SOXLUSDT frozen volatility spread with BTC state filter",
        "status": "exploratory_post_reveal_no_clean_holdout",
        "parameter_search_performed": True,
        "fresh_holdout_used_for_selection": False,
        "base_parameters_frozen": asdict(frozen.parameters),
        "data_coverage": {
            "soxl_through": _timestamp(soxl_bars[-1].end_ms),
            "btc_through": _timestamp(btc_bars[-1].end_ms),
            "selection_ends": _timestamp(_day_end(CONFIRMATION_END)),
        },
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
        "selected_filter": {
            "results": {name: _summary(result) for name, result in selected_results.items()},
            "daily": {
                name: list(result.daily_returns) for name, result in selected_results.items()
            },
            "tick_path_risk": tick_risk,
        },
        "fixed_baseline": {
            "results": {name: _summary(result) for name, result in fixed_results.items()},
        },
        "target": {"geometric_daily_return": 0.05, "achieved": False},
        "limitations": [
            "BTC is used only as a closed-15m state filter; it is not a second traded sleeve.",
            "BTC history currently ends on August 11, so the diagnostic overlap is August 11 only.",
            "The selected filter has no clean post-August-13 holdout and is not production "
            "approved.",
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


def _candidate_grid() -> list[dict[str, Any]]:
    return [
        {"mode": mode, "threshold": threshold, "name": f"{mode}_{threshold:g}"}
        for mode in ("low_vol", "high_vol", "btc_direction", "high_vol_direction")
        for threshold in (0.8, 1.0, 1.2, 1.5)
    ]


def _load_bars(database: Path, instrument_id: str) -> list[SpreadBar]:
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            """
            SELECT start_ms, end_ms, open, high, low, close, volume
            FROM ohlcv_bars
            WHERE instrument_id = ? AND interval_minutes = 15 AND is_closed = 1
            ORDER BY start_ms
            """,
            (instrument_id,),
        ).fetchall()
    return [
        SpreadBar(
            start_ms=int(row[0]),
            end_ms=int(row[1]),
            open=Decimal(row[2]),
            high=Decimal(row[3]),
            low=Decimal(row[4]),
            close=Decimal(row[5]),
            volume=Decimal(row[6]),
        )
        for row in rows
    ]


def _state_filter(
    soxl_bars: list[SpreadBar],
    btc_bars: list[SpreadBar],
    btc_features: SpreadFeatures,
    candidate: dict[str, Any],
) -> tuple[int | None, ...]:
    threshold = float(candidate["threshold"])
    mode = candidate["mode"]
    result: list[int | None] = []
    btc_index = 0
    for soxl_bar in soxl_bars:
        while btc_index + 1 < len(btc_bars) and btc_bars[btc_index + 1].end_ms <= soxl_bar.end_ms:
            btc_index += 1
        if btc_index >= len(btc_bars) or btc_bars[btc_index].end_ms != soxl_bar.end_ms:
            result.append(0)
            continue
        ratio = btc_features.ratios[btc_index]
        high = btc_features.prior_highs[btc_index]
        low = btc_features.prior_lows[btc_index]
        close = btc_bars[btc_index].close
        if ratio is None:
            result.append(0)
            continue
        direction = (
            1 if high is not None and close > high else -1 if low is not None and close < low else 0
        )
        if mode == "low_vol":
            result.append(None if ratio <= threshold else 0)
        elif mode == "high_vol":
            result.append(None if ratio >= threshold else 0)
        elif mode == "btc_direction":
            result.append(direction or 0)
        else:
            result.append(direction if ratio >= threshold else 0)
    return tuple(result)


def _evaluate(bars, funding, executions, parameters, state_filter, period) -> SpreadResult:
    return evaluate_spread(
        bars,
        build_spread_features(
            bars,
            fast_window=parameters.fast_window,
            slow_window=parameters.slow_window,
            breakout_window=parameters.breakout_window,
            compression_ratio=parameters.compression_ratio,
            compression_lookback=parameters.compression_lookback,
            spread_measure=parameters.spread_measure,
        ),
        parameters,
        start_ms=period[0],
        end_ms=period[1],
        funding_by_bar=funding,
        execution_by_bar=executions,
        entry_direction_filter=state_filter,
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
    }


def _periods(replay_start_ms: int) -> dict[str, tuple[int, int]]:
    return {
        "train": (replay_start_ms, _day_end(TRAIN_END)),
        "validation": (_day_start(VALIDATION_START), _day_end(VALIDATION_END)),
        "confirmation": (_day_start(CONFIRMATION_START), _day_end(CONFIRMATION_END)),
        "development": (replay_start_ms, _day_end(CONFIRMATION_END)),
        "diagnostic_overlap": (_day_start(DIAGNOSTIC_START), _day_end(DIAGNOSTIC_END)),
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    selected_results = payload["selected_filter"]["results"]
    baseline = payload["fixed_baseline"]["results"]
    return "\n".join(
        [
            "# SOXLUSDT Cross-Asset BTC State-Filter Exploration",
            "",
            "Status: **exploratory_post_reveal_no_clean_holdout**  ",
            f"Selected filter: `{selected['candidate']['name']}`  ",
            "5% daily target: **not achieved**",
            "",
            "The frozen SOXL volatility-spread signal is unchanged. The filter uses only the "
            "latest closed BTC 15m bar at the SOXL signal close; it controls whether the SOXL "
            "entry is allowed.",
            "",
            (
                "| Path | Train geo/day | Validation geo/day | Confirmation geo/day | "
                "Development geo/day | 8/11 overlap | 15m close DD |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|",
            _row("BTC filter", selected_results),
            _row("Fixed baseline", baseline),
            "",
            f"The search tested {payload['selection']['candidate_count']} BTC state filters; "
            f"{payload['selection']['eligible_count']} passed train/validation and "
            f"{payload['selection']['confirmation_positive_finalists']} of "
            f"{payload['selection']['finalists_checked']} finalists were positive in confirmation.",
            "",
            "BTC data ends on August 11, so the only post-selection overlap shown is August 11. "
            "This result is not a production recommendation and requires a fresh post-August-13 "
            "holdout.",
            "",
        ]
    )


def _row(name: str, results: dict[str, dict[str, Any]]) -> str:
    return (
        f"| {name} | {_percent(results['train']['geometric_daily_return'])} | "
        f"{_percent(results['validation']['geometric_daily_return'])} | "
        f"{_percent(results['confirmation']['geometric_daily_return'])} | "
        f"{_percent(results['development']['geometric_daily_return'])} | "
        f"{_percent(results['diagnostic_overlap']['net_return'])} | "
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
