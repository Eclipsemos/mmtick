#!/home/spaceaic/env/.venv/bin/python
"""Train annually refreshed causal GPU XGBoost BTC/ETH factors."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_factor_portfolio import (
    LEAD_CANDIDATE,
    LEAD_SIZING,
    _evaluate_candidate,
    _evaluate_lead,
    _event_candidate_library,
)
from train_continuous_factor import (
    ASSETS,
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    CONFIRMATION,
    EXPOSURES,
    HORIZONS,
    MONTHLY_LOSS_LIMITS,
    QUANTILES,
    SELECTION_2024,
    SELECTION_2025,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    _asset_row,
    _base_eligible,
    _confirm_portfolio,
    _libraries,
    _portfolio_eligible,
    _portfolio_score,
    _prediction_metrics,
    _risk_eligible,
    _select_portfolio,
    _selection_score,
    _summary,
)

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
from mastermind_tick.factor_portfolio import (
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
)
from mastermind_tick.lead_lag_factor import (
    causal_shock_scores,
    evaluate_weighted_targets,
    shock_targets,
    shock_weight_targets,
)


def _day_start(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


VINTAGES = {
    2024: {
        "fit": (_day_start(date(2021, 1, 1)), _day_end(date(2022, 12, 31))),
        "checkpoint": (_day_start(date(2023, 1, 1)), _day_end(date(2023, 12, 31))),
        "prediction": SELECTION_2024,
    },
    2025: {
        "fit": (_day_start(date(2021, 1, 1)), _day_end(date(2023, 12, 31))),
        "checkpoint": (_day_start(date(2024, 1, 1)), _day_end(date(2024, 12, 31))),
        "prediction": SELECTION_2025,
    },
    2026: {
        "fit": (_day_start(date(2021, 1, 1)), _day_end(date(2024, 12, 31))),
        "checkpoint": (_day_start(date(2025, 1, 1)), _day_end(date(2025, 12, 31))),
        "prediction": CONFIRMATION,
    },
}

ANCHOR_ALLOCATIONS = {
    "lead_lag": Decimal("0.4"),
    "event-eth_perp-to-eth_perp-continuation-60d-threshold-2p5-hold-12x4h-none-long_only": Decimal(
        "0.15"
    ),
    "event-btc_perp-to-btc_perp-continuation-15d-threshold-2-hold-4x4h-none-long_short": Decimal(
        "0.3"
    ),
    (
        "event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-"
        "hold-12x4h-underreaction-long_short"
    ): Decimal("0.15"),
}
ANCHOR_LEVERAGE = Decimal("4")
ANCHOR_WEIGHTS = tuple(
    Decimal(value) for value in ("0.25", "0.4", "0.5", "0.6", "0.75", "0.9", "1")
)
HYBRID_LEVERAGES = tuple(
    Decimal(value) for value in ("0.5", "0.75", "1", "1.25", "1.5", "1.75", "2", "2.25", "2.5")
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/walk_forward_factor/2026-08-15"),
    )
    parser.add_argument(
        "--checkpoint-dir", type=Path, default=Path("data/continuous_factor_models")
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seeds", default="17,42,73")
    args = parser.parse_args()
    seeds = tuple(int(value) for value in args.seeds.split(",") if value.strip())
    if not seeds:
        raise ValueError("at least one walk-forward factor seed is required")
    xgb, np = _libraries()

    print("loading aligned BTC/ETH 4h bars and funding", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    bars = {asset: aggregate_bars(loaded[asset][0], 240) for asset in ASSETS}
    _require_aligned(bars[ASSETS[0]], bars[ASSETS[1]])
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in ASSETS}
    raw_features = {
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
    labels = _labels_by_asset(np, bars)
    score_sets = {
        asset: {
            horizon: {quantile: [None] * len(bars[asset]) for quantile in QUANTILES}
            for horizon in HORIZONS
        }
        for asset in ASSETS
    }
    model_metrics: dict[str, dict[str, Any]] = {asset: {} for asset in ASSETS}
    checkpoint_paths = []
    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    for vintage, periods in VINTAGES.items():
        print(f"training walk-forward vintage {vintage}", flush=True)
        for asset in ASSETS:
            features = _normalize(np, raw_features[asset], bars[asset], periods["fit"])
            model_metrics[asset][str(vintage)] = {}
            for horizon in HORIZONS:
                predictions, paths, training = _train_vintage(
                    xgb,
                    np,
                    features,
                    labels[asset][horizon],
                    bars[asset],
                    horizon,
                    periods["fit"],
                    periods["checkpoint"],
                    seeds,
                    args.device,
                    args.checkpoint_dir,
                    stamp,
                    asset,
                    vintage,
                )
                checkpoint_paths.extend(paths)
                checkpoint_values = np.asarray(
                    [
                        predictions[index]
                        for index, bar in enumerate(bars[asset])
                        if periods["checkpoint"][0] <= bar.start_ms <= periods["checkpoint"][1]
                    ]
                )
                for quantile in QUANTILES:
                    threshold = max(float(np.quantile(np.abs(checkpoint_values), quantile)), 1e-8)
                    for index, bar in enumerate(bars[asset]):
                        if periods["prediction"][0] <= bar.start_ms <= periods["prediction"][1]:
                            score_sets[asset][horizon][quantile][index] = float(
                                predictions[index] / threshold
                            )
                model_metrics[asset][str(vintage)][str(horizon)] = {
                    "training": training,
                    "checkpoint": _prediction_metrics(
                        np,
                        predictions,
                        labels[asset][horizon],
                        bars[asset],
                        periods["checkpoint"],
                    ),
                    "prediction": _prediction_metrics(
                        np,
                        predictions,
                        labels[asset][horizon],
                        bars[asset],
                        periods["prediction"],
                    ),
                }

    print("selecting walk-forward signal controls on 2024 and 2025", flush=True)
    asset_search = {}
    selected_assets = {}
    eligible_assets = {}
    for asset in ASSETS:
        search, selected, eligible = _select_walk_asset(
            bars[asset], funding[asset], score_sets[asset]
        )
        asset_search[asset] = search
        eligible_assets[asset] = eligible
        if selected is not None:
            selected_assets[asset] = selected
        print(
            f"{asset}: base eligible={search['base_eligible_count']} "
            f"risk eligible={search['risk_eligible_count']}",
            flush=True,
        )
    model_portfolio = _select_portfolio(selected_assets)
    model_confirmation = _confirm_portfolio(
        selected_assets,
        model_portfolio,
        bars,
        funding,
        stress=False,
    )
    model_stress = _confirm_portfolio(
        selected_assets,
        model_portfolio,
        bars,
        funding,
        stress=True,
    )

    print("selecting static-anchor/BTC-model hybrid on 2024 and 2025", flush=True)
    anchor = _anchor_context(bars, loaded)
    anchor_selection = {
        name: _evaluate_anchor(anchor, period, stress=False)
        for name, period in (
            ("selection_2024", SELECTION_2024),
            ("selection_2025", SELECTION_2025),
        )
    }
    hybrid_portfolio = _select_hybrid(anchor_selection, eligible_assets["btc_perp"])
    hybrid_confirmation = _confirm_hybrid(
        anchor,
        hybrid_portfolio,
        bars["btc_perp"],
        funding["btc_perp"],
        stress=False,
    )
    hybrid_stress = _confirm_hybrid(
        anchor,
        hybrid_portfolio,
        bars["btc_perp"],
        funding["btc_perp"],
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
        model_portfolio,
        model_confirmation,
        model_stress,
        hybrid_portfolio,
        hybrid_confirmation,
        hybrid_stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"walk-forward-factor-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _labels_by_asset(np: Any, bars: dict[str, list[ResearchBar]]) -> dict[str, dict[int, Any]]:
    result = {}
    for asset in ASSETS:
        closes = np.asarray([float(bar.close) for bar in bars[asset]], dtype=np.float64)
        returns = np.full(len(closes), np.nan, dtype=np.float64)
        returns[1:] = closes[1:] / closes[:-1] - 1.0
        volatility = _rolling_std(np, returns, 42)
        result[asset] = {}
        for horizon in HORIZONS:
            raw = forward_open_returns(np, bars[asset], horizon)
            result[asset][horizon] = np.clip(
                raw / np.maximum(volatility * np.sqrt(horizon), 1e-6), -6.0, 6.0
            )
    return result


def _normalize(np: Any, values: Any, bars: list[ResearchBar], period: tuple[int, int]) -> Any:
    mask = np.asarray([period[0] <= bar.start_ms <= period[1] for bar in bars], dtype=bool)
    mean = np.nanmean(np.where(mask[:, None], values, np.nan), axis=0)
    std = np.nanstd(np.where(mask[:, None], values, np.nan), axis=0)
    std = np.where(np.isfinite(std) & (std > 1e-8), std, 1.0)
    return np.nan_to_num((values - mean) / std, nan=0.0, posinf=8.0, neginf=-8.0).astype(np.float32)


def _train_vintage(
    xgb: Any,
    np: Any,
    features: Any,
    labels: Any,
    bars: list[ResearchBar],
    horizon: int,
    fit_period: tuple[int, int],
    checkpoint_period: tuple[int, int],
    seeds: tuple[int, ...],
    device: str,
    checkpoint_dir: Path,
    stamp: str,
    asset: str,
    vintage: int,
) -> tuple[Any, list[str], list[dict[str, Any]]]:
    fit = _indices(np, bars, labels, fit_period, horizon)
    stop = _indices(np, bars, labels, checkpoint_period, horizon)
    fit_data = xgb.DMatrix(features[fit], label=labels[fit], feature_names=list(FEATURE_NAMES))
    stop_data = xgb.DMatrix(features[stop], label=labels[stop], feature_names=list(FEATURE_NAMES))
    all_data = xgb.DMatrix(features, feature_names=list(FEATURE_NAMES))
    predictions = []
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
        predictions.append(model.predict(all_data, iteration_range=(0, end)))
        path = (
            checkpoint_dir / f"walk-forward-{stamp}-{asset}-{vintage}-h{horizon}-seed-{seed}.json"
        )
        model.save_model(path)
        paths.append(str(path))
        histories.append(
            {
                "seed": seed,
                "best_iteration": model.best_iteration,
                "best_score": float(model.best_score),
            }
        )
    return np.mean(np.stack(predictions), axis=0), paths, histories


def _indices(
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


def _select_walk_asset(
    bars: list[ResearchBar],
    funding: list[list[Any]],
    scores: dict[int, dict[float, list[float | None]]],
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    base_rows = []
    for horizon, quantile_scores in scores.items():
        for quantile, values in quantile_scores.items():
            for direction in ("long_only", "long_short"):
                for smoothing in (1, 3, 6):
                    for hold in tuple(dict.fromkeys((1, horizon))):
                        for confirmation in (1, 2):
                            candidate = ContinuousSignalCandidate(
                                horizon,
                                direction,
                                1.0,
                                quantile,
                                smoothing,
                                hold,
                                confirmation,
                            )
                            targets = managed_targets(values, candidate)
                            results = _evaluate_selection(bars, funding, targets)
                            base_rows.append(
                                {
                                    "candidate": candidate,
                                    "scores": values,
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
                targets = managed_targets(row["scores"], candidate)
                results = _evaluate_selection(
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
    return (
        {
            "base_candidate_count": len(base_rows),
            "base_eligible_count": len(base_eligible),
            "risk_candidate_count": len(risk_rows),
            "risk_eligible_count": len(risk_eligible),
            "used_fallback_diagnostic": not risk_eligible,
            "confirmation_used_for_selection": False,
            "selected": _asset_row(ranked[0]) if ranked else None,
            "top_development_candidates": [_asset_row(row) for row in ranked[:10]],
        },
        ranked[0] if ranked else None,
        ranked,
    )


def _evaluate_selection(
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
        for name, period in (
            ("selection_2024", SELECTION_2024),
            ("selection_2025", SELECTION_2025),
        )
    }


def _anchor_context(
    bars: dict[str, list[ResearchBar]],
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
) -> dict[str, Any]:
    btc_scores, eth_scores = causal_shock_scores(bars["btc_perp"], bars["eth_perp"], 15 * 6)
    lead_targets = shock_weight_targets(
        shock_targets(btc_scores, eth_scores, LEAD_CANDIDATE),
        btc_scores,
        LEAD_SIZING,
    )
    event_ids = set(ANCHOR_ALLOCATIONS) - {"lead_lag"}
    event_candidates = {
        candidate.id: candidate
        for candidate in _event_candidate_library(
            bars["btc_perp"],
            bars["eth_perp"],
            loaded["btc_perp"][1],
            loaded["eth_perp"][1],
        )
        if candidate.id in event_ids
    }
    missing = event_ids - set(event_candidates)
    if missing:
        raise RuntimeError(f"static anchor candidates are missing: {sorted(missing)}")
    return {
        "lead_bars": bars["eth_perp"],
        "lead_funding": funding_by_bar(bars["eth_perp"], loaded["eth_perp"][1]),
        "lead_targets": lead_targets,
        "events": event_candidates,
    }


def _evaluate_anchor(
    anchor: dict[str, Any],
    period: tuple[int, int],
    *,
    stress: bool,
) -> PortfolioResult:
    fee = STRESS_FEE_BPS if stress else BASE_FEE_BPS
    slippage = STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS
    components = {
        "lead_lag": _evaluate_lead(
            anchor["lead_bars"],
            anchor["lead_funding"],
            anchor["lead_targets"],
            period,
            fee,
            slippage,
        )
    }
    components.update(
        {
            candidate_id: _evaluate_candidate(
                candidate,
                period,
                fee_bps=fee,
                slippage_bps=slippage,
            )
            for candidate_id, candidate in anchor["events"].items()
        }
    )
    return evaluate_static_portfolio(
        {name: decimal_returns(result.daily_returns) for name, result in components.items()},
        ANCHOR_ALLOCATIONS,
        leverage=ANCHOR_LEVERAGE,
    )


def _select_hybrid(
    anchor_results: dict[str, PortfolioResult],
    btc_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if not btc_candidates:
        return {
            "status": "no_btc_walk_forward_component",
            "candidate_count": 0,
            "eligible_count": 0,
            "selected": None,
            "_selected_row": None,
        }
    rows = []
    for btc_candidate in btc_candidates:
        for anchor_weight in ANCHOR_WEIGHTS:
            allocations = {
                "static_anchor": anchor_weight,
                "btc_walk_forward": Decimal("1") - anchor_weight,
            }
            for leverage in HYBRID_LEVERAGES:
                results = {
                    split: evaluate_static_portfolio(
                        {
                            "static_anchor": anchor_results[split].daily_returns,
                            "btc_walk_forward": decimal_returns(
                                btc_candidate["results"][split].daily_returns
                            ),
                        },
                        allocations,
                        leverage=leverage,
                    )
                    for split in ("selection_2024", "selection_2025")
                }
                rows.append(
                    {
                        "btc_candidate": btc_candidate,
                        "allocations": allocations,
                        "leverage": leverage,
                        "results": results,
                        "score": _portfolio_score(results),
                    }
                )
    eligible = [row for row in rows if _portfolio_eligible(row["results"])]
    ranked = sorted(eligible, key=lambda row: row["score"], reverse=True)
    selected = ranked[0] if ranked else None
    return {
        "status": "selected" if selected else "no_valid_hybrid",
        "candidate_count": len(rows),
        "eligible_count": len(eligible),
        "selected": _hybrid_row(selected) if selected else None,
        "_selected_row": selected,
    }


def _hybrid_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "btc_component": _asset_row(row["btc_candidate"]),
        "allocations": {name: float(value) for name, value in row["allocations"].items()},
        "leverage": float(row["leverage"]),
        **{name: result.as_dict() for name, result in row["results"].items()},
    }


def _confirm_hybrid(
    anchor: dict[str, Any],
    portfolio: dict[str, Any],
    btc_bars: list[ResearchBar],
    btc_funding: list[list[Any]],
    *,
    stress: bool,
) -> dict[str, Any] | None:
    row = portfolio.get("_selected_row")
    if row is None:
        return None
    selected_btc = row["btc_candidate"]
    fee = STRESS_FEE_BPS if stress else BASE_FEE_BPS
    slippage = STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS
    anchor_result = _evaluate_anchor(anchor, CONFIRMATION, stress=stress)
    btc_result = evaluate_weighted_targets(
        btc_bars,
        selected_btc["targets"],
        start_ms=CONFIRMATION[0],
        end_ms=CONFIRMATION[1],
        funding=btc_funding,
        fee_bps=fee,
        slippage_bps=slippage,
        monthly_loss_limit=selected_btc["candidate"].monthly_loss_limit,
    )
    result = evaluate_static_portfolio(
        {
            "static_anchor": anchor_result.daily_returns,
            "btc_walk_forward": decimal_returns(btc_result.daily_returns),
        },
        row["allocations"],
        leverage=row["leverage"],
    )
    return {
        "components": {
            "static_anchor": anchor_result.as_dict(),
            "btc_walk_forward": _summary(btc_result),
        },
        "portfolio": result.as_dict(include_daily=not stress),
    }


def _report(
    loaded: dict[str, tuple[list[ResearchBar], list[Any]]],
    bars: dict[str, list[ResearchBar]],
    seeds: tuple[int, ...],
    device: str,
    checkpoints: list[str],
    metrics: dict[str, dict[str, Any]],
    asset_search: dict[str, dict[str, Any]],
    model_portfolio: dict[str, Any],
    model_confirmation: dict[str, Any] | None,
    model_stress: dict[str, Any] | None,
    hybrid_portfolio: dict[str, Any],
    hybrid_confirmation: dict[str, Any] | None,
    hybrid_stress: dict[str, Any] | None,
) -> dict[str, Any]:
    base = hybrid_confirmation["portfolio"] if hybrid_confirmation else None
    stressed = hybrid_stress["portfolio"] if hybrid_stress else None
    achieved = bool(
        base
        and stressed
        and base["target_25pct_month_rate"] >= 0.5
        and base["max_drawdown"] >= -0.35
        and base["net_return"] > 0
        and stressed["net_return"] > 0
        and stressed["max_drawdown"] >= -0.35
    )
    public_model = {key: value for key, value in model_portfolio.items() if not key.startswith("_")}
    public_hybrid = {
        key: value for key, value in hybrid_portfolio.items() if not key.startswith("_")
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "static event anchor plus annual walk-forward GPU XGBoost BTC factor",
        "data": {
            "first_bar": _timestamp(max(bars[name][0].start_ms for name in ASSETS)),
            "last_bar": _timestamp(min(bars[name][-1].end_ms for name in ASSETS)),
            **{f"{name}_bars_15m": len(loaded[name][0]) for name in ASSETS},
            **{f"{name}_bars_4h": len(bars[name]) for name in ASSETS},
        },
        "vintages": {
            str(year): {name: _period(period) for name, period in periods.items()}
            for year, periods in VINTAGES.items()
        },
        "features": list(FEATURE_NAMES),
        "model": {
            "architecture": "annual expanding-window three-seed GPU XGBoost ensembles",
            "device": device,
            "seeds": list(seeds),
            "horizons_4h_bars": list(HORIZONS),
            "checkpoint_paths": checkpoints,
            "metrics": metrics,
        },
        "execution": {
            "signal_timing": "closed 4h bar using the model frozen before its calendar year",
            "fill_timing": "next 4h open",
            "base_fee_bps_per_fill": float(BASE_FEE_BPS),
            "base_slippage_bps_per_fill": float(BASE_SLIPPAGE_BPS),
            "stress_fee_bps_per_fill": float(STRESS_FEE_BPS),
            "stress_slippage_bps_per_fill": float(STRESS_SLIPPAGE_BPS),
            "funding": "historical funding while positioned",
            "liquidation_modeled": False,
        },
        "asset_search": asset_search,
        "model_only_diagnostic": {
            "portfolio_selection": public_model,
            "confirmation": model_confirmation,
            "stress_confirmation": model_stress,
        },
        "static_anchor": {
            "allocations": {name: float(value) for name, value in ANCHOR_ALLOCATIONS.items()},
            "internal_leverage": float(ANCHOR_LEVERAGE),
            "configuration_selected_before_this_experiment": True,
        },
        "portfolio_selection": {
            **public_hybrid,
            "confirmation_used_for_selection": False,
        },
        "confirmation": hybrid_confirmation,
        "stress_confirmation": hybrid_stress,
        "target": {
            "monthly_return": 0.25,
            "minimum_confirmation_target_month_rate": 0.5,
            "achieved": achieved,
        },
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The development-selected hybrid met the reused confirmation gates; "
                "genuinely unseen "
                "forward evidence is still required before trading use."
                if achieved
                else "No development-selected static-anchor/walk-forward hybrid passed reused "
                "confirmation monthly coverage, drawdown, and stress-cost gates."
            ),
        },
        "limitations": [
            "2026 has been viewed repeatedly and is confirmation evidence, not a fresh holdout.",
            "The static anchor configuration predates this hybrid search; only its outer "
            "allocation "
            "and leverage were selected in this experiment.",
            "Models refresh annually; intrayear regime changes are not learned until the next "
            "year.",
            "XGBoost checkpoints are stored under data/ and are not committed.",
            "Liquidation, borrowing costs, market impact, and exchange failure are not modeled.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    portfolio = payload["portfolio_selection"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only static event anchor plus annual walk-forward GPU XGBoost BTC factor.",
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
                _metric_row("2024 selection", selected["selection_2024"]),
                _metric_row("2025 selection", selected["selection_2025"]),
            ]
        )
    if confirmation and stress:
        base = confirmation["portfolio"]
        stressed = stress["portfolio"]
        lines.extend(
            [
                _metric_row("2026 reused confirmation", base),
                _metric_row("2026 stress 10+5 bps", stressed),
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


def _metric_row(label: str, row: dict[str, Any]) -> str:
    targets = sum(value["return"] >= 0.25 for value in row["monthly_returns"])
    return (
        f"| {label} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | "
        f"{row['positive_month_rate']:.2%} | {targets}/{len(row['monthly_returns'])} |"
    )


def _require_aligned(left: list[ResearchBar], right: list[ResearchBar]) -> None:
    if len(left) != len(right) or any(
        first.start_ms != second.start_ms for first, second in zip(left, right, strict=True)
    ):
        raise ValueError("walk-forward factor BTC and ETH bars are not aligned")


def _period(value: tuple[int, int]) -> dict[str, str]:
    return {"start": _timestamp(value[0]), "end": _timestamp(value[1])}


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
