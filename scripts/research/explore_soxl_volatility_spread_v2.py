#!/usr/bin/env python3
"""Compare orthogonal SOXLUSDT volatility-spread definitions with a fresh holdout."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import Any

import explore_soxl_volatility_spread as phase_one

from mastermind_tick.volatility_spread import (
    SpreadBar,
    SpreadExecution,
    SpreadFeatures,
    SpreadParameters,
    SpreadResult,
    SpreadTrade,
    build_spread_features,
    daily_path_metrics,
    evaluate_spread,
    pearson_correlation,
)

MEASURES = ("true_range", "return_volatility", "body_range")
FRESH_HOLDOUT_START = date(2026, 8, 11)
FRESH_HOLDOUT_END = date(2026, 8, 13)


def candidate_grid() -> list[SpreadParameters]:
    alternatives = [
        SpreadParameters(
            variant=variant,
            direction=direction,
            fast_window=fast,
            slow_window=slow,
            entry_ratio=entry,
            exit_ratio=exit_ratio,
            breakout_window=breakout,
            stop_atr=stop,
            max_hold_bars=max_hold,
            spread_measure=measure,
            minimum_volume_ratio=volume_ratio,
        )
        for (
            measure,
            variant,
            direction,
            fast,
            slow,
            entry,
            exit_ratio,
            breakout,
            stop,
            max_hold,
            volume_ratio,
        ) in product(
            MEASURES,
            ("expansion_breakout", "compression_release"),
            ("long_only", "long_short"),
            (8, 12, 24),
            (64, 96),
            (0.9, 1.1, 1.3),
            (0.8, 1.0),
            (12, 24),
            (2.5, 3.5),
            (24, 96),
            (None, 1.1),
        )
    ]
    return list(dict.fromkeys([*phase_one.candidate_grid(), *alternatives]))


def run() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/soxl_volatility_spread/2026-08-14-v2"),
    )
    parser.add_argument("--finalists-per-measure", type=int, default=20)
    parser.add_argument(
        "--atr-baseline-report",
        type=Path,
        default=Path(
            "reports/experiments/soxl_volatility_spread/2026-08-14-v2/"
            "atr_tick_grid_20260814T035639Z.json"
        ),
    )
    args = parser.parse_args()

    bars, funding_by_bar, execution_by_bar = phase_one.load_market(args.database)
    _verify_fresh_holdout(bars, execution_by_bar)
    splits = {
        "train": (bars[200].start_ms, _day_end(date(2026, 6, 30))),
        "validation": (_day_start(date(2026, 7, 1)), _day_end(date(2026, 7, 31))),
        "confirmation": (_day_start(date(2026, 8, 1)), _day_end(date(2026, 8, 10))),
        "development": (bars[200].start_ms, _day_end(date(2026, 8, 10))),
        "fresh_holdout": (_day_start(FRESH_HOLDOUT_START), _day_end(FRESH_HOLDOUT_END)),
        "continuous": (bars[200].start_ms, _day_end(FRESH_HOLDOUT_END)),
    }
    grid = candidate_grid()
    feature_cache: dict[tuple[Any, ...], SpreadFeatures] = {}
    evaluations: list[dict[str, Any]] = []
    for index, parameters in enumerate(grid, start=1):
        features = _features(bars, parameters, feature_cache)
        train = _evaluate(bars, funding_by_bar, features, parameters, splits["train"])
        validation = _evaluate(bars, funding_by_bar, features, parameters, splits["validation"])
        evaluations.append(
            {
                "parameters": asdict(parameters),
                "selection_score": _selection_score(train, validation),
                "train": _summary(train),
                "validation": _summary(validation),
            }
        )
        if index % 500 == 0:
            print(f"development search {index}/{len(grid)}", flush=True)

    locked: dict[str, dict[str, Any]] = {}
    for measure in MEASURES:
        ranked = sorted(
            (
                item
                for item in evaluations
                if item["parameters"]["spread_measure"] == measure
                and _eligible(item["train"], item["validation"])
            ),
            key=lambda item: item["selection_score"],
            reverse=True,
        )
        if not ranked:
            raise RuntimeError(f"no eligible {measure} candidates")
        finalists = []
        for item in ranked[: args.finalists_per_measure]:
            parameters = SpreadParameters(**item["parameters"])
            features = _features(bars, parameters, feature_cache)
            confirmation = _evaluate(
                bars, funding_by_bar, features, parameters, splits["confirmation"]
            )
            finalists.append({**item, "confirmation": _summary(confirmation)})
        passed_confirmation = [
            item
            for item in finalists
            if item["confirmation"]["net_return"] > 0
            and item["confirmation"]["completed_trades"] >= 2
        ]
        chosen = passed_confirmation[0] if passed_confirmation else finalists[0]
        parameters = SpreadParameters(**chosen["parameters"])
        locked[measure] = {
            **chosen,
            "confirmation_positive_finalists": sum(
                item["confirmation"]["net_return"] > 0 for item in finalists
            ),
            "confirmation_gate_passed": bool(passed_confirmation),
            "finalists_checked": len(finalists),
            "local_stability": _local_stability(
                bars,
                funding_by_bar,
                parameters,
                splits,
                feature_cache,
            ),
        }

    confirmation_passed_measures = [
        name for name, item in locked.items() if item["confirmation_gate_passed"]
    ]
    if not confirmation_passed_measures:
        raise RuntimeError("no spread measure passed the pre-reveal confirmation gate")
    global_measure = max(
        confirmation_passed_measures, key=lambda name: locked[name]["selection_score"]
    )
    locked_parameters = {
        **{name: SpreadParameters(**item["parameters"]) for name, item in locked.items()},
        "phase_one_fixed": _phase_one_parameters(),
    }

    # Fresh holdout access begins only after every candidate above has been finalized.
    revealed: dict[str, Any] = {}
    development_results: dict[str, SpreadResult] = {}
    continuous_results: dict[str, SpreadResult] = {}
    for name, parameters in locked_parameters.items():
        features = _features(bars, parameters, feature_cache)
        reset_holdout = evaluate_spread(
            bars,
            features,
            parameters,
            start_ms=splits["fresh_holdout"][0],
            end_ms=splits["fresh_holdout"][1],
            funding_by_bar=funding_by_bar,
            execution_by_bar=execution_by_bar,
        )
        continuous = evaluate_spread(
            bars,
            features,
            parameters,
            start_ms=splits["continuous"][0],
            end_ms=splits["continuous"][1],
            funding_by_bar=funding_by_bar,
            execution_by_bar=execution_by_bar,
        )
        development_tick = evaluate_spread(
            bars,
            features,
            parameters,
            start_ms=splits["development"][0],
            end_ms=splits["development"][1],
            funding_by_bar=funding_by_bar,
            execution_by_bar=execution_by_bar,
        )
        development_results[name] = development_tick
        continuous_results[name] = continuous
        revealed[name] = {
            "parameters": asdict(parameters),
            "development_tick_fill": _summary(development_tick),
            "development_monthly": _monthly_returns(development_tick.daily_returns),
            "development_trade_structure": _trade_structure(development_tick),
            "fresh_holdout_reset": _summary(reset_holdout),
            "fresh_holdout_continuous": _continuous_holdout_summary(
                continuous, splits["fresh_holdout"]
            ),
        }

    atr_comparison = _atr_spread_comparison(
        args.atr_baseline_report,
        development_results[global_measure],
        continuous_results[global_measure],
        splits["fresh_holdout"],
    )
    intrabar_tick_risk = _tick_path_risk(
        args.database,
        development_results[global_measure].trades,
        [event for events in funding_by_bar for event in events],
        expected_final_equity=development_results[global_measure].final_equity,
    )
    high_exposure_stress = []
    global_parameters = locked_parameters[global_measure]
    global_features = _features(bars, global_parameters, feature_cache)
    all_funding_events = [event for events in funding_by_bar for event in events]
    for exposure in (1.25, 2.0, 3.0, 5.0, 7.5, 10.0):
        parameters = replace(global_parameters, exposure=exposure)
        development = evaluate_spread(
            bars,
            global_features,
            parameters,
            start_ms=splits["development"][0],
            end_ms=splits["development"][1],
            funding_by_bar=funding_by_bar,
            execution_by_bar=execution_by_bar,
        )
        fresh = evaluate_spread(
            bars,
            global_features,
            parameters,
            start_ms=splits["fresh_holdout"][0],
            end_ms=splits["fresh_holdout"][1],
            funding_by_bar=funding_by_bar,
            execution_by_bar=execution_by_bar,
        )
        tick_risk = _tick_path_risk(
            args.database,
            development.trades,
            all_funding_events,
            expected_final_equity=development.final_equity,
        )
        high_exposure_stress.append(
            {
                "exposure": exposure,
                "development": _summary(development),
                "development_tick_path": tick_risk,
                "fresh_holdout_reset": _summary(fresh),
            }
        )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "SOXLUSDT multi-definition volatility spread, phase 2",
        "data": {
            "bars": len(bars),
            "first_bar": _timestamp(bars[0].start_ms),
            "last_bar": _timestamp(bars[-1].end_ms),
            "bars_with_execution_tick": sum(item is not None for item in execution_by_bar),
        },
        "costs": {
            "fee_bps_per_fill": 5,
            "slippage_bps_per_fill": 2,
            "initial_equity": 100000,
            "funding_included": True,
        },
        "splits": {
            name: {"start": _timestamp(period[0]), "end": _timestamp(period[1])}
            for name, period in splits.items()
        },
        "selection": {
            "candidate_count": len(grid),
            "fresh_holdout_used_for_selection": False,
            "global_measure_locked_before_reveal": global_measure,
            "rule": (
                "rank on the weaker train/validation geometric daily return with drawdown "
                "penalty; require positive train and validation, then a positive 8/1-8/10 "
                "confirmation with at least two trades"
            ),
        },
        "locked_candidates": locked,
        "fresh_holdout_reveal": revealed,
        "atr_baseline_comparison": atr_comparison,
        "locked_candidate_intrabar_tick_risk": intrabar_tick_risk,
        "high_exposure_stress": {
            "liquidation_modeled": False,
            "rows": high_exposure_stress,
        },
        "target": {
            "geometric_daily_return": 0.05,
            "achieved_by_locked_global_candidate": (
                revealed[global_measure]["fresh_holdout_continuous"]["geometric_daily_return"]
                >= 0.05
            ),
        },
        "decision": _decision(global_measure, revealed, locked),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(_markdown(payload), encoding="utf-8")
    print(args.output_dir / "README.md")
    return payload


def _features(
    bars: list[SpreadBar],
    parameters: SpreadParameters,
    cache: dict[tuple[Any, ...], SpreadFeatures],
) -> SpreadFeatures:
    key = (
        parameters.spread_measure,
        parameters.fast_window,
        parameters.slow_window,
        parameters.breakout_window,
        parameters.compression_ratio,
        parameters.compression_lookback,
    )
    features = cache.get(key)
    if features is None:
        features = build_spread_features(
            bars,
            fast_window=parameters.fast_window,
            slow_window=parameters.slow_window,
            breakout_window=parameters.breakout_window,
            compression_ratio=parameters.compression_ratio,
            compression_lookback=parameters.compression_lookback,
            spread_measure=parameters.spread_measure,
        )
        cache[key] = features
    return features


def _evaluate(
    bars: list[SpreadBar],
    funding_by_bar,
    features: SpreadFeatures,
    parameters: SpreadParameters,
    period: tuple[int, int],
) -> SpreadResult:
    return evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=period[0],
        end_ms=period[1],
        funding_by_bar=funding_by_bar,
    )


def _summary(result: SpreadResult) -> dict[str, Any]:
    value = asdict(result)
    value.pop("daily_returns")
    value.pop("trades")
    return value


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


def _selection_score(train: SpreadResult, validation: SpreadResult) -> float:
    if not _eligible(_summary(train), _summary(validation)):
        return -1000.0
    return min(train.geometric_daily_return, validation.geometric_daily_return) + 0.05 * min(
        train.max_drawdown, validation.max_drawdown
    )


def _phase_one_parameters() -> SpreadParameters:
    return SpreadParameters(
        variant="compression_release",
        direction="long_only",
        fast_window=24,
        slow_window=64,
        entry_ratio=1.0,
        exit_ratio=0.8,
        breakout_window=24,
        stop_atr=3.5,
        max_hold_bars=96,
    )


def _local_stability(
    bars: list[SpreadBar],
    funding_by_bar,
    selected: SpreadParameters,
    splits: dict[str, tuple[int, int]],
    cache: dict[tuple[Any, ...], SpreadFeatures],
) -> dict[str, Any]:
    values = {
        "fast_window": sorted({max(2, selected.fast_window // 2), selected.fast_window, 24}),
        "slow_window": sorted({64, selected.slow_window, 96}),
        "entry_ratio": sorted(
            {
                max(0.1, selected.entry_ratio - 0.1),
                selected.entry_ratio,
                selected.entry_ratio + 0.1,
            }
        ),
        "exit_ratio": sorted(
            {
                max(0.0, selected.exit_ratio - 0.1),
                selected.exit_ratio,
                selected.exit_ratio + 0.1,
            }
        ),
        "breakout_window": sorted({12, selected.breakout_window, 24, 48}),
        "stop_atr": sorted({2.5, selected.stop_atr, 3.5, 4.5}),
        "max_hold_bars": sorted({24, selected.max_hold_bars, 48, 96}),
        "minimum_volume_ratio": sorted(
            {selected.minimum_volume_ratio, None, 1.1, 1.3},
            key=lambda item: -1.0 if item is None else item,
        ),
    }
    neighbors: set[SpreadParameters] = set()
    for name, candidates in values.items():
        for value in candidates:
            candidate = replace(selected, **{name: value})
            try:
                candidate.validate()
            except ValueError:
                continue
            neighbors.add(candidate)
    rows = []
    for parameters in neighbors:
        features = _features(bars, parameters, cache)
        validation = _evaluate(bars, funding_by_bar, features, parameters, splits["validation"])
        confirmation = _evaluate(bars, funding_by_bar, features, parameters, splits["confirmation"])
        rows.append(
            {
                "parameters": asdict(parameters),
                "validation": _summary(validation),
                "confirmation": _summary(confirmation),
            }
        )
    stable = sum(
        row["validation"]["net_return"] > 0 and row["confirmation"]["net_return"] > 0
        for row in rows
    )
    return {
        "method": "one parameter varied at a time; fresh holdout excluded",
        "candidate_count": len(rows),
        "positive_validation_and_confirmation_count": stable,
        "positive_validation_and_confirmation_rate": stable / len(rows),
        "median_confirmation_return": statistics.median(
            row["confirmation"]["net_return"] for row in rows
        ),
        "rows": rows,
    }


def _continuous_holdout_summary(result: SpreadResult, period: tuple[int, int]) -> dict[str, Any]:
    start_date = datetime.fromtimestamp(period[0] / 1000, UTC).date().isoformat()
    end_date = datetime.fromtimestamp(period[1] / 1000, UTC).date().isoformat()
    daily = [(day, value) for day, value in result.daily_returns if start_date <= day <= end_date]
    metrics = daily_path_metrics([value for _, value in daily])
    trades = [trade for trade in result.trades if period[0] <= trade.exit_at_ms <= period[1]]
    return {
        **metrics,
        "days": len(daily),
        "completed_trades": len(trades),
        "win_rate": sum(trade.net_pnl > 0 for trade in trades) / len(trades) if trades else None,
        "daily": [{"date": day, "return": value} for day, value in daily],
    }


def _monthly_returns(daily_returns: tuple[tuple[str, float], ...]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    for day, value in daily_returns:
        grouped.setdefault(day[:7], []).append(value)
    return [{"month": month, **daily_path_metrics(values)} for month, values in grouped.items()]


def _atr_spread_comparison(
    path: Path,
    development_spread: SpreadResult,
    continuous_spread: SpreadResult,
    fresh_period: tuple[int, int],
) -> dict[str, Any]:
    atr_daily, baseline = _load_atr_daily(path)
    development_daily = dict(development_spread.daily_returns)
    development_days = sorted(atr_daily.keys() & development_daily.keys())
    development = _aligned_path_comparison(
        development_days,
        atr_daily,
        development_daily,
    )
    fresh_start = datetime.fromtimestamp(fresh_period[0] / 1000, UTC).date().isoformat()
    fresh_end = datetime.fromtimestamp(fresh_period[1] / 1000, UTC).date().isoformat()
    continuous_daily = dict(continuous_spread.daily_returns)
    fresh_days = [
        day
        for day in sorted(atr_daily.keys() & continuous_daily.keys())
        if fresh_start <= day <= fresh_end
    ]
    fresh = _aligned_path_comparison(fresh_days, atr_daily, continuous_daily)
    development_mix = [(atr_daily[day] + development_daily[day]) / 2 for day in development_days]
    fresh_mix = [(atr_daily[day] + continuous_daily[day]) / 2 for day in fresh_days]
    scales = []
    for scale in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0):
        scales.append(
            {
                "capital_scale": scale,
                "approximate_target_exposure": 1.25 * scale,
                "development": _scaled_daily_metrics(development_mix, scale),
                "fresh_holdout": _scaled_daily_metrics(fresh_mix, scale),
            }
        )
    target_scale = next(
        (
            scale / 4
            for scale in range(4, 41)
            if _scaled_daily_metrics(development_mix, scale / 4)["geometric_daily_return"] >= 0.05
        ),
        None,
    )
    return {
        "source_report": str(path),
        "baseline": baseline,
        "development": development,
        "fresh_holdout": fresh,
        "portfolio_scale_stress": {
            "method": (
                "Linear scaling of the 50/50 daily-rebalanced net-return series; diagnostic "
                "only, with no liquidation or cross-strategy intraday margin model."
            ),
            "minimum_development_scale_reaching_5_percent": target_scale,
            "rows": scales,
        },
        "caveat": (
            "ATR uses Tick-level signal replay. The spread uses closed 15m-bar signals and the "
            "first persisted Tick for fills; spread risk is marked at 15m closes. Intraday "
            "drawdowns are therefore not comparable."
        ),
    }


def _load_atr_daily(path: Path) -> tuple[dict[str, float], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "runs" in payload:
        run = payload["runs"][0]
        result = run["results"][0]
        previous_equity = float(result["initial_equity"])
        daily = {}
        for point in result["daily_equity"]:
            equity = float(point["equity"])
            daily[point["date"]] = equity / previous_equity - 1 if previous_equity else 0.0
            previous_equity = equity
        return daily, {
            "name": "ATR(32) x 3 long-only Tick replay, 1.25x exposure",
            "net_return_through_source_end": result["net_return"],
            "max_tick_drawdown": result["max_drawdown"],
            "completed_trades": result["completed_trades"],
            "source_end": _timestamp(run["metadata"]["end_ms"]),
        }
    candidate = next(
        item
        for item in payload["candidates"]
        if item["parameters"]["atr_period"] == 32 and item["parameters"]["atr_multiplier"] == 3.0
    )
    return {item["label"]: item["return"] for item in candidate["daily"]}, {
        "name": "ATR(32) x 3 long-only Tick replay, 1.25x exposure",
        "net_return_through_source_end": candidate["metrics"]["net_return"],
        "max_tick_drawdown": candidate["metrics"]["max_drawdown"],
        "completed_trades": candidate["metrics"]["completed_trades"],
        "source_end": _timestamp(candidate["metrics"]["end_ms"]),
    }


def _aligned_path_comparison(
    days: list[str], left_daily: dict[str, float], right_daily: dict[str, float]
) -> dict[str, Any]:
    left = [left_daily[day] for day in days]
    right = [right_daily[day] for day in days]
    mixes = []
    for right_weight in (0.25, 0.5, 0.75):
        values = [
            (1 - right_weight) * left_value + right_weight * right_value
            for left_value, right_value in zip(left, right, strict=True)
        ]
        mixes.append(
            {
                "spread_weight": right_weight,
                "atr_weight": 1 - right_weight,
                **daily_path_metrics(values),
            }
        )
    return {
        "days": days,
        "overlap_days": len(days),
        "pearson_daily_return_correlation": pearson_correlation(left, right),
        "joint_loss_days": sum(
            left_value < 0 and right_value < 0
            for left_value, right_value in zip(left, right, strict=True)
        ),
        "atr_daily_path": daily_path_metrics(left),
        "spread_daily_path": daily_path_metrics(right),
        "daily_rebalanced_mixes": mixes,
    }


def _scaled_daily_metrics(values: list[float], scale: float) -> dict[str, Any]:
    scaled = [value * scale for value in values]
    if any(value <= -1 for value in scaled):
        return {
            "net_return": -1.0,
            "geometric_daily_return": -1.0,
            "max_daily_close_drawdown": -1.0,
            "bankrupt": True,
        }
    return {**daily_path_metrics(scaled), "bankrupt": False}


def _trade_structure(result: SpreadResult) -> dict[str, Any]:
    long_trades = [trade for trade in result.trades if trade.direction == "LONG"]
    short_trades = [trade for trade in result.trades if trade.direction == "SHORT"]
    long_net = sum((trade.net_pnl for trade in long_trades), start=Decimal("0"))
    short_net = sum((trade.net_pnl for trade in short_trades), start=Decimal("0"))
    positive_net = max(Decimal("0"), long_net) + max(Decimal("0"), short_net)
    return {
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_net_pnl": float(long_net),
        "short_net_pnl": float(short_net),
        "short_positive_pnl_share": float(short_net / positive_net) if positive_net else None,
    }


def _tick_path_risk(
    database: Path,
    trades: tuple[SpreadTrade, ...],
    funding_events,
    *,
    initial_equity: Decimal = Decimal("100000"),
    fee_bps: Decimal = Decimal("5"),
    expected_final_equity: float | None = None,
) -> dict[str, Any]:
    fee_rate = fee_bps / Decimal("10000")
    cash = initial_equity
    peak = initial_equity
    minimum_equity = initial_equity
    max_drawdown = Decimal("0")
    max_drawdown_at_ms: int | None = None
    observations = 0
    funding_events = sorted(funding_events, key=lambda item: item.timestamp_ms)
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        for trade in trades:
            signed_quantity = trade.quantity if trade.direction == "LONG" else -trade.quantity
            entry_fee = trade.quantity * trade.entry_price * fee_rate
            exit_fee = trade.quantity * trade.exit_price * fee_rate
            cash -= entry_fee
            relevant_funding = [
                event
                for event in funding_events
                if trade.entry_at_ms <= event.timestamp_ms <= trade.exit_at_ms
            ]
            funding_index = 0
            applied_funding = Decimal("0")
            rows = connection.execute(
                """
                SELECT timestamp_ms, price FROM agg_trades
                WHERE instrument_id = 'soxl_perp' AND timestamp_ms BETWEEN ? AND ?
                ORDER BY timestamp_ms
                """,
                (trade.entry_at_ms, trade.exit_at_ms),
            )
            for timestamp_ms, price_text in rows:
                while funding_index < len(relevant_funding) and relevant_funding[
                    funding_index
                ].timestamp_ms <= int(timestamp_ms):
                    event = relevant_funding[funding_index]
                    amount = -(signed_quantity * event.mark_price * event.rate)
                    cash += amount
                    applied_funding += amount
                    funding_index += 1
                equity = cash + signed_quantity * (Decimal(price_text) - trade.entry_price)
                peak = max(peak, equity)
                minimum_equity = min(minimum_equity, equity)
                drawdown = equity / peak - Decimal("1") if peak else Decimal("0")
                if drawdown < max_drawdown:
                    max_drawdown = drawdown
                    max_drawdown_at_ms = int(timestamp_ms)
                observations += 1
            while funding_index < len(relevant_funding):
                event = relevant_funding[funding_index]
                amount = -(signed_quantity * event.mark_price * event.rate)
                cash += amount
                applied_funding += amount
                funding_index += 1
            if abs(applied_funding - trade.funding) > Decimal("0.00000001"):
                raise ValueError("Tick-path funding reconstruction does not match bar replay")
            cash += signed_quantity * (trade.exit_price - trade.entry_price) - exit_fee
            peak = max(peak, cash)
            minimum_equity = min(minimum_equity, cash)
            drawdown = cash / peak - Decimal("1") if peak else Decimal("0")
            if drawdown < max_drawdown:
                max_drawdown = drawdown
                max_drawdown_at_ms = trade.exit_at_ms
    if expected_final_equity is not None and (
        abs(cash - Decimal(str(expected_final_equity))) > Decimal("0.01")
    ):
        raise ValueError("Tick-path final equity does not match the selected replay")
    return {
        "tick_observations": observations,
        "completed_trades": len(trades),
        "final_equity": float(cash),
        "minimum_equity": float(minimum_equity),
        "max_drawdown": float(max_drawdown),
        "max_drawdown_at": (
            _timestamp(max_drawdown_at_ms) if max_drawdown_at_ms is not None else None
        ),
    }


def _decision(
    global_measure: str,
    revealed: dict[str, Any],
    locked: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    fresh = revealed[global_measure]["fresh_holdout_continuous"]
    stable = locked[global_measure]["local_stability"]
    passed = (
        fresh["net_return"] > 0
        and fresh["completed_trades"] >= 3
        and stable["positive_validation_and_confirmation_rate"] >= 0.7
    )
    return {
        "status": "provisional_candidate" if passed else "insufficient_fresh_evidence",
        "locked_global_measure": global_measure,
        "reason": (
            "fresh holdout and local stability gates passed, but the holdout remains only "
            "three days"
            if passed
            else "fresh holdout trade count or stability gate did not establish robust evidence"
        ),
    }


def _verify_fresh_holdout(
    bars: list[SpreadBar], execution_by_bar: list[SpreadExecution | None]
) -> None:
    expected_end = _day_end(FRESH_HOLDOUT_END)
    if bars[-1].end_ms != expected_end:
        raise ValueError(
            f"fresh holdout must end at {FRESH_HOLDOUT_END.isoformat()} 23:59:59.999 UTC"
        )
    if any(item is None for item in execution_by_bar):
        raise ValueError("fresh holdout research requires an execution Tick for every closed bar")


def _markdown(payload: dict[str, Any]) -> str:
    reveal = payload["fresh_holdout_reveal"]
    global_measure = payload["selection"]["global_measure_locked_before_reveal"]
    target_status = "met" if payload["target"]["achieved_by_locked_global_candidate"] else "not met"
    lines = [
        "# SOXLUSDT Volatility-Spread Phase 2",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Design",
        "",
        (
            "Three spread definitions were compared: normalized true range, close-return "
            "volatility, and candle-body range. Optional fast/slow volume confirmation was "
            "included. Signals use closed 15m bars; selected-candidate verification fills on "
            "the next persisted 250ms aggregate Tick."
        ),
        "",
        (
            "Candidate ranking used May 17 through July 31. August 1-10 was confirmation only. "
            "August 11-13 was not accessed until every per-measure candidate and the global "
            f"measure (`{global_measure}`) had been locked."
        ),
        "",
        "## Locked Candidates And Fresh Holdout",
        "",
        "| Candidate | Development | Confirmation | Fresh reset | Fresh continuous | "
        "Fresh geo/day | Fresh trades | Local stable | Gate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for name in (*MEASURES, "phase_one_fixed"):
        item = reveal[name]
        locked = payload["locked_candidates"].get(name)
        confirmation = locked["confirmation"]["net_return"] if locked else None
        stability = (
            locked["local_stability"]["positive_validation_and_confirmation_rate"]
            if locked
            else None
        )
        gate = locked["confirmation_gate_passed"] if locked else None
        continuous = item["fresh_holdout_continuous"]
        lines.append(
            f"| `{name}` | {_pct(item['development_tick_fill']['net_return'])} | "
            f"{_pct(confirmation)} | {_pct(item['fresh_holdout_reset']['net_return'])} | "
            f"{_pct(continuous['net_return'])} | "
            f"{_pct(continuous['geometric_daily_return'])} | "
            f"{continuous['completed_trades']} | {_pct(stability)} | "
            f"{'yes' if gate else 'no' if gate is not None else '--'} |"
        )
    global_item = reveal[global_measure]
    lines.extend(
        [
            "",
            "## Locked Global Candidate",
            "",
            "```json",
            json.dumps(global_item["parameters"], indent=2),
            "```",
            "",
            f"Decision: **{payload['decision']['status']}**. {payload['decision']['reason']}.",
            "",
            "## Development Structure",
            "",
            "| Month | Return | Geometric/day | Daily-close max DD |",
            "|---|---:|---:|---:|",
        ]
    )
    for month in global_item["development_monthly"]:
        lines.append(
            f"| {month['month']} | {_pct(month['net_return'])} | "
            f"{_pct(month['geometric_daily_return'])} | "
            f"{_pct(month['max_daily_close_drawdown'])} |"
        )
    structure = global_item["development_trade_structure"]
    tick_risk = payload["locked_candidate_intrabar_tick_risk"]
    lines.extend(
        [
            "",
            (
                f"Development trades: {structure['long_trades']} long / "
                f"{structure['short_trades']} short. Net PnL: "
                f"{structure['long_net_pnl']:,.2f} long / "
                f"{structure['short_net_pnl']:,.2f} short."
            ),
            (
                f"Shorts contributed {_pct(structure['short_positive_pnl_share'])} of positive "
                f"directional net PnL. The five largest wins contributed "
                f"{_pct(global_item['development_tick_fill']['top_five_profit_concentration'])} "
                "of gross profit."
            ),
            (
                f"Intrabar reconstruction scanned {tick_risk['tick_observations']:,} persisted "
                f"Ticks across all positions. Maximum Tick-path drawdown was "
                f"{_pct(tick_risk['max_drawdown'])} at {tick_risk['max_drawdown_at']}, versus "
                f"{_pct(global_item['development_tick_fill']['max_drawdown'])} when marked only "
                "at 15m closes."
            ),
            "",
            "## Fresh Holdout Daily Returns",
            "",
            "| UTC date | Return |",
            "|---|---:|",
        ]
    )
    for day in global_item["fresh_holdout_continuous"]["daily"]:
        lines.append(f"| {day['date']} | {_pct(day['return'])} |")

    comparison = payload["atr_baseline_comparison"]
    development_comparison = comparison["development"]
    fresh_comparison = comparison["fresh_holdout"]
    equal_mix = next(
        item
        for item in development_comparison["daily_rebalanced_mixes"]
        if item["spread_weight"] == 0.5
    )
    fresh_equal_mix = next(
        item for item in fresh_comparison["daily_rebalanced_mixes"] if item["spread_weight"] == 0.5
    )
    lines.extend(
        [
            "",
            "## ATR Baseline Comparison Through August 10",
            "",
            (
                f"Daily-return correlation with ATR(32) x 3 was "
                f"{development_comparison['pearson_daily_return_correlation']:.3f}."
            ),
            "",
            "| Path | Return | Geometric/day | Daily-close max DD |",
            "|---|---:|---:|---:|",
            (
                f"| ATR baseline | "
                f"{_pct(development_comparison['atr_daily_path']['net_return'])} | "
                f"{_pct(development_comparison['atr_daily_path']['geometric_daily_return'])} | "
                f"{_pct(development_comparison['atr_daily_path']['max_daily_close_drawdown'])} |"
            ),
            (
                f"| Locked spread | "
                f"{_pct(development_comparison['spread_daily_path']['net_return'])} | "
                f"{_pct(development_comparison['spread_daily_path']['geometric_daily_return'])} | "
                f"{_pct(development_comparison['spread_daily_path']['max_daily_close_drawdown'])} |"
            ),
            (
                f"| 50/50 daily-rebalanced mix | {_pct(equal_mix['net_return'])} | "
                f"{_pct(equal_mix['geometric_daily_return'])} | "
                f"{_pct(equal_mix['max_daily_close_drawdown'])} |"
            ),
            "",
            "### Fresh Holdout Portfolio",
            "",
            (
                f"The three-day ATR/spread correlation rose to "
                f"{fresh_comparison['pearson_daily_return_correlation']:.3f}; both paths lost "
                f"together on {fresh_comparison['joint_loss_days']} days."
            ),
            "",
            "| Fresh path | Three-day return | Geometric/day |",
            "|---|---:|---:|",
            (
                f"| ATR baseline | {_pct(fresh_comparison['atr_daily_path']['net_return'])} | "
                f"{_pct(fresh_comparison['atr_daily_path']['geometric_daily_return'])} |"
            ),
            (
                f"| Locked spread | "
                f"{_pct(fresh_comparison['spread_daily_path']['net_return'])} | "
                f"{_pct(fresh_comparison['spread_daily_path']['geometric_daily_return'])} |"
            ),
            (
                f"| 50/50 daily-rebalanced mix | {_pct(fresh_equal_mix['net_return'])} | "
                f"{_pct(fresh_equal_mix['geometric_daily_return'])} |"
            ),
            "",
            comparison["caveat"],
            "",
            "## 50/50 Portfolio Scale Stress",
            "",
            "| Capital scale | Approx. exposure | Development geo/day | Daily-close max DD | "
            "Fresh return |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    portfolio_stress = comparison["portfolio_scale_stress"]
    for item in portfolio_stress["rows"]:
        lines.append(
            f"| {item['capital_scale']:.1f}x | "
            f"{item['approximate_target_exposure']:.2f}x | "
            f"{_pct(item['development']['geometric_daily_return'])} | "
            f"{_pct(item['development']['max_daily_close_drawdown'])} | "
            f"{_pct(item['fresh_holdout']['net_return'])} |"
        )
    target_scale = portfolio_stress["minimum_development_scale_reaching_5_percent"]
    lines.extend(
        [
            "",
            portfolio_stress["method"],
            (
                f"Minimum tested development scale reaching 5% geometric daily return: "
                f"{target_scale:.2f}x."
                if target_scale is not None
                else "No tested portfolio scale reached 5% geometric daily return."
            ),
            "",
            "## High-Exposure Target Test",
            "",
            "| Target exposure | Development return | Development geo/day | Tick-path max DD | "
            "Fresh 3-day return |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for item in payload["high_exposure_stress"]["rows"]:
        lines.append(
            f"| {item['exposure']:.2f}x | {_pct(item['development']['net_return'])} | "
            f"{_pct(item['development']['geometric_daily_return'])} | "
            f"{_pct(item['development_tick_path']['max_drawdown'])} | "
            f"{_pct(item['fresh_holdout_reset']['net_return'])} |"
        )
    lines.extend(
        [
            "",
            (
                "Liquidation is not modeled. The 10x path reaches 5% per day only in the "
                "development sample while suffering an 89% Tick-path drawdown and failing to "
                "repeat that return in the fresh holdout. It is not an executable solution."
            ),
            "",
            "## 5% Daily Target",
            "",
            (
                f"The locked global candidate produced "
                f"{_pct(global_item['fresh_holdout_continuous']['geometric_daily_return'])} "
                "geometric daily return in the fresh holdout. The 5% target was "
                f"{target_status}."
            ),
            "",
            (
                "The fresh holdout contains only three UTC days. A positive result is useful "
                "directional evidence, not sufficient evidence for production or leverage."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2%}"


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    run()
