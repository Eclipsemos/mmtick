#!/home/spaceaic/env/.venv/bin/python
"""Train causal GPU XGBoost models for continuous BTC/ETH cross-asset factors."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mastermind_tick.bar_research import ResearchBar, ResearchResult, aggregate_bars, funding_by_bar
from mastermind_tick.continuous_factor import (
    FEATURE_NAMES,
    ContinuousSignalCandidate,
    _rolling_std,
    cross_asset_features,
    forward_open_returns,
    managed_targets,
)
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_portfolio import decimal_returns, evaluate_static_portfolio
from mastermind_tick.lead_lag_factor import evaluate_weighted_targets

ASSETS = ("btc_perp", "eth_perp")
HORIZONS = (1, 3, 6, 12)
BASE_FEE_BPS = Decimal("5")
BASE_SLIPPAGE_BPS = Decimal("2")
STRESS_FEE_BPS = Decimal("10")
STRESS_SLIPPAGE_BPS = Decimal("5")
QUANTILES = (0.50, 0.65, 0.80, 0.90)
EXPOSURES = tuple(Decimal(value) for value in ("1", "1.5", "2", "3", "4", "5", "6", "8"))
MONTHLY_LOSS_LIMITS = (None, Decimal("0.075"), Decimal("0.10"), Decimal("0.15"))
PORTFOLIO_LEVERAGES = tuple(Decimal(value) for value in ("0.5", "0.75", "1", "1.25", "1.5"))


def _day_start(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


FIT = (_day_start(date(2021, 1, 1)), _day_end(date(2022, 12, 31)))
CHECKPOINT = (_day_start(date(2023, 1, 1)), _day_end(date(2023, 12, 31)))
SELECTION_2024 = (_day_start(date(2024, 1, 1)), _day_end(date(2024, 12, 31)))
SELECTION_2025 = (_day_start(date(2025, 1, 1)), _day_end(date(2025, 12, 31)))
CONFIRMATION = (_day_start(date(2026, 1, 1)), _day_end(date(2026, 8, 10)))
SELECTION_PERIODS = {"selection_2024": SELECTION_2024, "selection_2025": SELECTION_2025}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/continuous_factor/2026-08-15"),
    )
    parser.add_argument(
        "--checkpoint-dir", type=Path, default=Path("data/continuous_factor_models")
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seeds", default="17,42,73")
    args = parser.parse_args()
    seeds = tuple(int(value) for value in args.seeds.split(",") if value.strip())
    if not seeds:
        raise ValueError("at least one continuous factor seed is required")
    xgb, np = _libraries()

    print("loading aligned BTC/ETH 4h bars and funding", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    bars = {asset: aggregate_bars(loaded[asset][0], 240) for asset in ASSETS}
    _require_aligned(bars[ASSETS[0]], bars[ASSETS[1]])
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in ASSETS}
    features = {
        "btc_perp": cross_asset_features(
            np,
            bars["btc_perp"],
            bars["eth_perp"],
            funding["btc_perp"],
            funding["eth_perp"],
        ),
        "eth_perp": cross_asset_features(
            np,
            bars["eth_perp"],
            bars["btc_perp"],
            funding["eth_perp"],
            funding["btc_perp"],
        ),
    }
    normalized = {
        asset: _normalize_features(np, features[asset], bars[asset], FIT) for asset in ASSETS
    }

    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    predictions: dict[str, dict[int, Any]] = {asset: {} for asset in ASSETS}
    model_metrics: dict[str, dict[str, Any]] = {asset: {} for asset in ASSETS}
    checkpoint_paths = []
    for asset in ASSETS:
        close_returns = np.full(len(bars[asset]), np.nan, dtype=np.float64)
        closes = np.asarray([float(bar.close) for bar in bars[asset]], dtype=np.float64)
        close_returns[1:] = closes[1:] / closes[:-1] - 1.0
        volatility = _rolling_std(np, close_returns, 42)
        for horizon in HORIZONS:
            raw_labels = forward_open_returns(np, bars[asset], horizon)
            labels = np.clip(
                raw_labels / np.maximum(volatility * np.sqrt(horizon), 1e-6), -6.0, 6.0
            )
            print(f"training {asset} horizon={horizon} with {len(seeds)} seeds", flush=True)
            output, paths, training = _train_ensemble(
                xgb,
                np,
                normalized[asset],
                labels,
                bars[asset],
                horizon,
                seeds,
                args.device,
                args.checkpoint_dir,
                stamp,
                asset,
            )
            predictions[asset][horizon] = output
            checkpoint_paths.extend(paths)
            model_metrics[asset][str(horizon)] = {
                "training": training,
                "fit": _prediction_metrics(np, output, labels, bars[asset], FIT),
                "checkpoint": _prediction_metrics(np, output, labels, bars[asset], CHECKPOINT),
                "selection_2024": _prediction_metrics(
                    np, output, labels, bars[asset], SELECTION_2024
                ),
                "selection_2025": _prediction_metrics(
                    np, output, labels, bars[asset], SELECTION_2025
                ),
                "confirmation": _prediction_metrics(np, output, labels, bars[asset], CONFIRMATION),
            }

    print("selecting causal signal and risk controls on 2024 and 2025", flush=True)
    asset_search = {}
    selected_assets: dict[str, dict[str, Any]] = {}
    for asset in ASSETS:
        search, selected = _select_asset(
            np,
            bars[asset],
            funding[asset],
            predictions[asset],
        )
        asset_search[asset] = search
        if selected is not None:
            selected_assets[asset] = selected
        print(
            f"{asset}: base eligible={search['base_eligible_count']} "
            f"risk eligible={search['risk_eligible_count']}",
            flush=True,
        )
    portfolio = _select_portfolio(selected_assets)
    confirmation = _confirm_portfolio(
        selected_assets,
        portfolio,
        bars,
        funding,
        stress=False,
    )
    stress = _confirm_portfolio(
        selected_assets,
        portfolio,
        bars,
        funding,
        stress=True,
    )
    payload = _report(
        loaded,
        bars,
        seeds,
        args.device,
        checkpoint_paths,
        model_metrics,
        asset_search,
        portfolio,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"continuous-factor-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _libraries() -> tuple[Any, Any]:
    try:
        import numpy as np
        import xgboost as xgb
    except ImportError as exc:
        raise RuntimeError(
            "continuous factor worker requires /home/spaceaic/env/.venv with xgboost and numpy"
        ) from exc
    return xgb, np


def _normalize_features(
    np: Any, values: Any, bars: list[ResearchBar], period: tuple[int, int]
) -> Any:
    mask = np.asarray([period[0] <= bar.start_ms <= period[1] for bar in bars], dtype=bool)
    mean = np.nanmean(np.where(mask[:, None], values, np.nan), axis=0)
    std = np.nanstd(np.where(mask[:, None], values, np.nan), axis=0)
    std = np.where(np.isfinite(std) & (std > 1e-8), std, 1.0)
    return np.nan_to_num((values - mean) / std, nan=0.0, posinf=8.0, neginf=-8.0).astype(np.float32)


def _train_ensemble(
    xgb: Any,
    np: Any,
    features: Any,
    labels: Any,
    bars: list[ResearchBar],
    horizon: int,
    seeds: tuple[int, ...],
    device: str,
    checkpoint_dir: Path,
    stamp: str,
    asset: str,
) -> tuple[Any, list[str], list[dict[str, Any]]]:
    fit = _sample_indices(np, bars, labels, FIT, horizon)
    stop = _sample_indices(np, bars, labels, CHECKPOINT, horizon)
    fit_data = xgb.DMatrix(features[fit], label=labels[fit], feature_names=list(FEATURE_NAMES))
    stop_data = xgb.DMatrix(features[stop], label=labels[stop], feature_names=list(FEATURE_NAMES))
    all_data = xgb.DMatrix(features, feature_names=list(FEATURE_NAMES))
    outputs = []
    paths = []
    histories = []
    for seed in seeds:
        model = xgb.train(
            {
                "objective": "reg:pseudohubererror",
                "eval_metric": "rmse",
                "tree_method": "hist",
                "device": device,
                "eta": 0.025,
                "max_depth": 4,
                "min_child_weight": 12,
                "subsample": 0.8,
                "colsample_bytree": 0.75,
                "lambda": 20,
                "alpha": 2,
                "seed": seed,
            },
            fit_data,
            num_boost_round=800,
            evals=[(stop_data, "checkpoint")],
            early_stopping_rounds=60,
            verbose_eval=False,
        )
        end = model.best_iteration + 1 if model.best_iteration is not None else 0
        outputs.append(model.predict(all_data, iteration_range=(0, end)))
        checkpoint = checkpoint_dir / f"continuous-{stamp}-{asset}-h{horizon}-seed-{seed}.json"
        model.save_model(checkpoint)
        paths.append(str(checkpoint))
        histories.append(
            {
                "seed": seed,
                "best_iteration": model.best_iteration,
                "best_score": float(model.best_score),
            }
        )
    return np.mean(np.stack(outputs), axis=0), paths, histories


def _sample_indices(
    np: Any,
    bars: list[ResearchBar],
    labels: Any,
    period: tuple[int, int],
    horizon: int,
) -> Any:
    return np.asarray(
        [
            index
            for index, bar in enumerate(bars)
            if period[0] <= bar.start_ms <= period[1]
            and index + horizon + 1 < len(bars)
            and bars[index + horizon + 1].start_ms <= period[1]
            and np.isfinite(labels[index])
        ],
        dtype=np.int64,
    )


def _prediction_metrics(
    np: Any,
    predictions: Any,
    labels: Any,
    bars: list[ResearchBar],
    period: tuple[int, int],
) -> dict[str, Any]:
    indexes = np.asarray(
        [
            index
            for index, bar in enumerate(bars)
            if period[0] <= bar.start_ms <= period[1] and np.isfinite(labels[index])
        ],
        dtype=np.int64,
    )
    predicted = predictions[indexes]
    actual = labels[indexes]
    correlation = float(np.corrcoef(predicted, actual)[0, 1]) if len(indexes) > 1 else 0.0
    return {
        "samples": len(indexes),
        "information_coefficient": correlation if np.isfinite(correlation) else 0.0,
        "direction_accuracy": float(np.mean((predicted >= 0) == (actual >= 0))),
        "prediction_mean": float(np.mean(predicted)),
        "prediction_std": float(np.std(predicted)),
    }


def _select_asset(
    np: Any,
    bars: list[ResearchBar],
    funding: list[list[Any]],
    predictions: dict[int, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    base_rows = []
    for horizon, scores in predictions.items():
        checkpoint_values = np.asarray(
            [
                scores[index]
                for index, bar in enumerate(bars)
                if CHECKPOINT[0] <= bar.start_ms <= CHECKPOINT[1]
            ]
        )
        for quantile in QUANTILES:
            threshold = float(np.quantile(np.abs(checkpoint_values), quantile))
            for direction in ("long_only", "long_short"):
                for smoothing in (1, 3, 6):
                    for hold in tuple(dict.fromkeys((1, horizon))):
                        for confirmation in (1, 2):
                            candidate = ContinuousSignalCandidate(
                                horizon,
                                direction,
                                threshold,
                                quantile,
                                smoothing,
                                hold,
                                confirmation,
                            )
                            targets = managed_targets([float(value) for value in scores], candidate)
                            results = _evaluate_periods(bars, funding, targets)
                            base_rows.append(
                                {
                                    "candidate": candidate,
                                    "scores": scores,
                                    "results": results,
                                    "score": _selection_score(results),
                                }
                            )
    base_eligible = [row for row in base_rows if _base_eligible(row["results"])]
    ranked_base = sorted(base_eligible or base_rows, key=lambda row: row["score"], reverse=True)
    risk_rows = []
    for row in ranked_base[:20]:
        for exposure in EXPOSURES:
            for loss in MONTHLY_LOSS_LIMITS:
                candidate = replace(row["candidate"], exposure=exposure, monthly_loss_limit=loss)
                targets = managed_targets([float(value) for value in row["scores"]], candidate)
                results = _evaluate_periods(
                    bars,
                    funding,
                    targets,
                    monthly_loss_limit=loss,
                )
                risk_rows.append(
                    {
                        "candidate": candidate,
                        "scores": row["scores"],
                        "targets": targets,
                        "results": results,
                        "score": _selection_score(results),
                    }
                )
    risk_eligible = [row for row in risk_rows if _risk_eligible(row["results"])]
    ranked = sorted(risk_eligible, key=lambda row: row["score"], reverse=True)
    search = {
        "base_candidate_count": len(base_rows),
        "base_eligible_count": len(base_eligible),
        "risk_candidate_count": len(risk_rows),
        "risk_eligible_count": len(risk_eligible),
        "used_fallback_diagnostic": not risk_eligible,
        "confirmation_used_for_selection": False,
        "selected": _asset_row(ranked[0]) if ranked else None,
        "top_development_candidates": [_asset_row(row) for row in ranked[:10]],
    }
    return search, ranked[0] if ranked else None


def _evaluate_periods(
    bars: list[ResearchBar],
    funding: list[list[Any]],
    targets: tuple[Decimal | None, ...],
    *,
    monthly_loss_limit: Decimal | None = None,
) -> dict[str, ResearchResult]:
    return {
        name: evaluate_weighted_targets(
            bars,
            targets,
            start_ms=period[0],
            end_ms=period[1],
            funding=funding,
            fee_bps=BASE_FEE_BPS,
            slippage_bps=BASE_SLIPPAGE_BPS,
            monthly_loss_limit=monthly_loss_limit,
        )
        for name, period in SELECTION_PERIODS.items()
    }


def _base_eligible(results: dict[str, ResearchResult]) -> bool:
    return all(
        result.net_return > 0
        and result.completed_trades >= 6
        and result.max_drawdown >= -0.50
        and not result.bankrupt
        for result in results.values()
    )


def _risk_eligible(results: dict[str, ResearchResult]) -> bool:
    return all(
        result.net_return > 0
        and result.completed_trades >= 6
        and result.max_drawdown >= -0.35
        and _positive_month_rate(result) >= 0.5
        and not result.bankrupt
        for result in results.values()
    )


def _selection_score(results: dict[str, ResearchResult]) -> tuple[float, ...]:
    first = results["selection_2024"]
    second = results["selection_2025"]
    return (
        min(_target_month_rate(first), _target_month_rate(second)),
        _target_month_rate(first) + _target_month_rate(second),
        min(_positive_month_rate(first), _positive_month_rate(second)),
        min(_worst_month(first), _worst_month(second)),
        min(first.net_return, second.net_return),
        min(first.max_drawdown, second.max_drawdown),
    )


def _select_portfolio(selected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not selected:
        return {
            "status": "no_valid_components",
            "eligible_components": [],
            "eligible_count": 0,
            "selected": None,
        }
    names = tuple(name for name in ASSETS if name in selected)
    weights = (
        (Decimal("1"),)
        if len(names) == 1
        else (Decimal("0"), Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1"))
    )
    rows = []
    for btc_weight in weights:
        allocations = {
            name: btc_weight if name == "btc_perp" else Decimal("1") - btc_weight for name in names
        }
        allocations = {name: value for name, value in allocations.items() if value > 0}
        for leverage in PORTFOLIO_LEVERAGES:
            results = {
                split: evaluate_static_portfolio(
                    {
                        name: decimal_returns(selected[name]["results"][split].daily_returns)
                        for name in allocations
                    },
                    allocations,
                    leverage=leverage,
                )
                for split in SELECTION_PERIODS
            }
            row = {
                "allocations": allocations,
                "leverage": leverage,
                "results": results,
                "score": _portfolio_score(results),
            }
            rows.append(row)
    eligible = [row for row in rows if _portfolio_eligible(row["results"])]
    ranked = sorted(eligible, key=lambda row: row["score"], reverse=True)
    return {
        "status": "selected" if ranked else "no_valid_portfolio",
        "eligible_components": list(names),
        "candidate_count": len(rows),
        "eligible_count": len(eligible),
        "selected": _portfolio_row(ranked[0]) if ranked else None,
        "_selected_row": ranked[0] if ranked else None,
    }


def _portfolio_eligible(results: dict[str, Any]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and not result.bankrupt
        for result in results.values()
    )


def _portfolio_score(results: dict[str, Any]) -> tuple[Decimal, ...]:
    first = results["selection_2024"]
    second = results["selection_2025"]
    return (
        min(first.target_month_rate, second.target_month_rate),
        first.target_month_rate + second.target_month_rate,
        min(first.positive_month_rate, second.positive_month_rate),
        min(first.worst_month, second.worst_month),
        min(first.net_return, second.net_return),
        min(first.max_drawdown, second.max_drawdown),
    )


def _confirm_portfolio(
    selected_assets: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    bars: dict[str, list[ResearchBar]],
    funding: dict[str, list[list[Any]]],
    *,
    stress: bool,
) -> dict[str, Any] | None:
    row = portfolio.get("_selected_row")
    if row is None:
        return None
    fee = STRESS_FEE_BPS if stress else BASE_FEE_BPS
    slippage = STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS
    components = {}
    for asset in row["allocations"]:
        selected = selected_assets[asset]
        components[asset] = evaluate_weighted_targets(
            bars[asset],
            selected["targets"],
            start_ms=CONFIRMATION[0],
            end_ms=CONFIRMATION[1],
            funding=funding[asset],
            fee_bps=fee,
            slippage_bps=slippage,
            monthly_loss_limit=selected["candidate"].monthly_loss_limit,
        )
    result = evaluate_static_portfolio(
        {name: decimal_returns(value.daily_returns) for name, value in components.items()},
        row["allocations"],
        leverage=row["leverage"],
    )
    return {
        "components": {name: _summary(value) for name, value in components.items()},
        "portfolio": result.as_dict(include_daily=not stress),
    }


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    bars: dict[str, list[ResearchBar]],
    seeds: tuple[int, ...],
    device: str,
    checkpoints: list[str],
    model_metrics: dict[str, dict[str, Any]],
    asset_search: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    confirmation: dict[str, Any] | None,
    stress: dict[str, Any] | None,
) -> dict[str, Any]:
    confirmation_result = confirmation["portfolio"] if confirmation else None
    stress_result = stress["portfolio"] if stress else None
    achieved = bool(
        confirmation_result
        and stress_result
        and confirmation_result["target_25pct_month_rate"] >= 0.5
        and confirmation_result["max_drawdown"] >= -0.35
        and confirmation_result["net_return"] > 0
        and stress_result["net_return"] > 0
        and stress_result["max_drawdown"] >= -0.35
    )
    public_portfolio = {key: value for key, value in portfolio.items() if not key.startswith("_")}
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "GPU XGBoost continuous cross-asset BTC/ETH factor",
        "data": {
            "first_bar": _timestamp(max(bars[name][0].start_ms for name in ASSETS)),
            "last_bar": _timestamp(min(bars[name][-1].end_ms for name in ASSETS)),
            **{f"{name}_bars_15m": len(loaded[name][0]) for name in ASSETS},
            **{f"{name}_bars_4h": len(bars[name]) for name in ASSETS},
        },
        "periods": {
            "model_fit": _period(FIT),
            "early_stop_and_threshold_calibration": _period(CHECKPOINT),
            "selection_2024": _period(SELECTION_2024),
            "selection_2025": _period(SELECTION_2025),
            "confirmation": _period(CONFIRMATION),
        },
        "features": list(FEATURE_NAMES),
        "model": {
            "architecture": "asset/horizon-specific three-seed GPU XGBoost regression ensemble",
            "device": device,
            "seeds": list(seeds),
            "horizons_4h_bars": list(HORIZONS),
            "checkpoint_paths": checkpoints,
            "metrics": model_metrics,
        },
        "execution": {
            "signal_timing": "features and predictions on closed 4h bars",
            "fill_timing": "next 4h open",
            "base_fee_bps_per_fill": float(BASE_FEE_BPS),
            "base_slippage_bps_per_fill": float(BASE_SLIPPAGE_BPS),
            "stress_fee_bps_per_fill": float(STRESS_FEE_BPS),
            "stress_slippage_bps_per_fill": float(STRESS_SLIPPAGE_BPS),
            "funding": "historical funding while positioned",
            "liquidation_modeled": False,
        },
        "asset_search": asset_search,
        "portfolio_selection": {
            **public_portfolio,
            "confirmation_used_for_selection": False,
        },
        "confirmation": confirmation,
        "stress_confirmation": stress,
        "target": {
            "monthly_return": 0.25,
            "minimum_confirmation_target_month_rate": 0.5,
            "achieved": achieved,
        },
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The continuous factor met the reused confirmation gates; genuinely unseen "
                "forward evidence is still required before trading use."
                if achieved
                else "No development-selected continuous factor portfolio passed reused "
                "confirmation monthly coverage, drawdown, and stress-cost gates."
            ),
        },
        "limitations": [
            "2026 has been viewed repeatedly and is confirmation evidence, not a fresh holdout.",
            "The 2023 interval controls boosting duration and calibrates score quantiles.",
            "XGBoost checkpoints are stored under data/ and are not committed.",
            "Liquidation, borrowing costs, market impact, and exchange failure are not modeled.",
        ],
    }


def _asset_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate": row["candidate"].as_dict(),
        "score": list(row["score"]),
        "selection_2024": _summary(row["results"]["selection_2024"]),
        "selection_2025": _summary(row["results"]["selection_2025"]),
    }


def _portfolio_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "allocations": {name: float(value) for name, value in row["allocations"].items()},
        "leverage": float(row["leverage"]),
        "score": [float(value) for value in row["score"]],
        **{name: result.as_dict() for name, result in row["results"].items()},
    }


def _summary(result: ResearchResult) -> dict[str, Any]:
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
        "target_25pct_month_rate": _target_month_rate(result),
        "monthly_returns": [
            {"label": label, "return": value} for label, value in result.monthly_returns
        ],
    }


def _positive_month_rate(result: ResearchResult) -> float:
    return sum(value > 0 for _label, value in result.monthly_returns) / len(result.monthly_returns)


def _target_month_rate(result: ResearchResult) -> float:
    return sum(value >= 0.25 for _label, value in result.monthly_returns) / len(
        result.monthly_returns
    )


def _worst_month(result: ResearchResult) -> float:
    return min(value for _label, value in result.monthly_returns)


def _markdown(payload: dict[str, Any]) -> str:
    portfolio = payload["portfolio_selection"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only GPU XGBoost continuous cross-asset factor.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Portfolio selection: `{portfolio['status']}`.",
    ]
    if portfolio.get("selected"):
        selected = portfolio["selected"]
        allocations = ", ".join(
            f"{name} {weight:.0%}" for name, weight in selected["allocations"].items()
        )
        lines.extend(
            [
                f"Allocation: {allocations}; portfolio leverage `{selected['leverage']:.2f}x`.",
                "",
                "| Split | Return | Max DD | Positive months | 25% months |",
                "|---|---:|---:|---:|---:|",
                _portfolio_metric_row("2024 selection", selected["selection_2024"]),
                _portfolio_metric_row("2025 selection", selected["selection_2025"]),
            ]
        )
    if confirmation and stress:
        base = confirmation["portfolio"]
        stressed = stress["portfolio"]
        lines.extend(
            [
                _portfolio_metric_row("2026 reused confirmation", base),
                _portfolio_metric_row("2026 stress 10+5 bps", stressed),
                "",
                "## 2026 monthly returns",
                "",
                "| Month | Base | Stress |",
                "|---|---:|---:|",
            ]
        )
        stress_months = {row["label"]: row["return"] for row in stressed["monthly_returns"]}
        lines.extend(
            f"| {row['label']} | {row['return']:.2%} | {stress_months[row['label']]:.2%} |"
            for row in base["monthly_returns"]
        )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _portfolio_metric_row(label: str, row: dict[str, Any]) -> str:
    targets = sum(value["return"] >= 0.25 for value in row["monthly_returns"])
    return (
        f"| {label} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | "
        f"{row['positive_month_rate']:.2%} | {targets}/{len(row['monthly_returns'])} |"
    )


def _require_aligned(left: list[ResearchBar], right: list[ResearchBar]) -> None:
    if len(left) != len(right) or any(
        first.start_ms != second.start_ms for first, second in zip(left, right, strict=True)
    ):
        raise ValueError("continuous factor BTC and ETH bars are not aligned")


def _period(value: tuple[int, int]) -> dict[str, str]:
    return {"start": _timestamp(value[0]), "end": _timestamp(value[1])}


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
