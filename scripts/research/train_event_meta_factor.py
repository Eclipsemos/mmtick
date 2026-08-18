#!/home/spaceaic/env/.venv/bin/python
"""Train a GPU XGBoost meta-label model for BTC-shock-to-ETH events."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mastermind_tick.bar_research import ResearchBar, ResearchResult, aggregate_bars, funding_by_bar
from mastermind_tick.event_meta_factor import (
    FEATURE_NAMES,
    EventSample,
    build_event_samples,
    filtered_event_targets,
)
from mastermind_tick.factor_mining import load_market
from mastermind_tick.lead_lag_factor import (
    LeadLagCandidate,
    causal_shock_scores,
    evaluate_weighted_targets,
    shock_targets,
)
from mastermind_tick.models import FundingRate


def _day_start(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


MODEL_TRAIN = (_day_start(date(2021, 1, 1)), _day_end(date(2022, 12, 31)))
EARLY_STOP = (_day_start(date(2023, 1, 1)), _day_end(date(2023, 12, 31)))
SELECTION = (_day_start(date(2024, 1, 1)), _day_end(date(2025, 12, 31)))
CONFIRMATION = (_day_start(date(2026, 1, 1)), _day_end(date(2026, 8, 10)))
BASE_FEE_BPS = Decimal("5")
BASE_SLIPPAGE_BPS = Decimal("2")
STRESS_FEE_BPS = Decimal("10")
STRESS_SLIPPAGE_BPS = Decimal("5")
BASE_CANDIDATE = LeadLagCandidate(15, Decimal("1.5"), 12, "long_short", "underreaction")
PROBABILITY_THRESHOLDS = tuple(
    Decimal(value) for value in ("0.45", "0.5", "0.55", "0.6", "0.65", "0.7", "0.75")
)
EXPOSURES = tuple(Decimal(value) for value in ("1", "1.5", "2", "2.5", "3", "4", "5"))
MONTHLY_LOSS_LIMITS = tuple(Decimal(value) for value in ("0.05", "0.075", "0.10", "0.15"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/event_meta_factor/2026-08-15"),
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path("data/event_meta_models"),
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seeds", default="17,42,73")
    args = parser.parse_args()
    seeds = tuple(int(value) for value in args.seeds.split(",") if value.strip())
    if not seeds:
        raise ValueError("at least one event meta-label seed is required")

    xgb, np, metrics = _libraries()
    print("loading aligned BTC/ETH 4h bars and ETH funding", flush=True)
    btc_source, _btc_rates = load_market(args.database, "btc_perp")
    eth_source, eth_rates = load_market(args.database, "eth_perp")
    btc = aggregate_bars(btc_source, 240)
    eth = aggregate_bars(eth_source, 240)
    _require_aligned_bars(btc, eth)
    eth_funding = funding_by_bar(eth, eth_rates)
    btc_scores, eth_scores = causal_shock_scores(btc, eth, 15 * 6)
    base_targets = shock_targets(btc_scores, eth_scores, BASE_CANDIDATE)
    samples = build_event_samples(
        btc,
        eth,
        btc_scores,
        eth_scores,
        base_targets,
        eth_funding,
        hold_bars=BASE_CANDIDATE.hold_bars,
        fee_bps=BASE_FEE_BPS,
        slippage_bps=BASE_SLIPPAGE_BPS,
    )
    split_samples = {
        "model_train": _samples_in_period(samples, MODEL_TRAIN),
        "early_stop": _samples_in_period(samples, EARLY_STOP),
        "selection": _samples_in_period(samples, SELECTION),
        "confirmation": _samples_in_period(samples, CONFIRMATION),
    }
    for name, rows in split_samples.items():
        positive = sum(sample.profitable for sample in rows)
        print(f"{name}: events={len(rows)} positive={positive}", flush=True)
        if len(rows) < 20 or positive in {0, len(rows)}:
            raise ValueError(f"{name} has insufficient event labels")

    print(f"training {len(seeds)} GPU XGBoost meta-label models", flush=True)
    models = []
    checkpoint_paths = []
    train_matrix, train_labels = _matrix(np, split_samples["model_train"])
    stop_matrix, stop_labels = _matrix(np, split_samples["early_stop"])
    train_data = xgb.DMatrix(train_matrix, label=train_labels, feature_names=list(FEATURE_NAMES))
    stop_data = xgb.DMatrix(stop_matrix, label=stop_labels, feature_names=list(FEATURE_NAMES))
    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    for seed in seeds:
        positive = max(1, int(train_labels.sum()))
        negative = max(1, len(train_labels) - positive)
        model = xgb.train(
            {
                "objective": "binary:logistic",
                "eval_metric": "logloss",
                "tree_method": "hist",
                "device": args.device,
                "eta": 0.03,
                "max_depth": 3,
                "min_child_weight": 5,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "lambda": 10,
                "alpha": 1,
                "scale_pos_weight": negative / positive,
                "seed": seed,
            },
            train_data,
            num_boost_round=500,
            evals=[(stop_data, "early_stop")],
            early_stopping_rounds=40,
            verbose_eval=False,
        )
        checkpoint = args.checkpoint_dir / f"event-meta-{checkpoint_stamp}-seed-{seed}.json"
        model.save_model(checkpoint)
        checkpoint_paths.append(str(checkpoint))
        models.append(model)
        print(
            f"seed={seed} best_iteration={model.best_iteration} best_score={model.best_score}",
            flush=True,
        )

    probabilities = _ensemble_probabilities(xgb, np, models, samples)
    split_probabilities = {
        name: tuple(probabilities[sample.index] for sample in rows)
        for name, rows in split_samples.items()
    }
    classification = {
        name: _classification_summary(np, metrics, rows, split_probabilities[name])
        for name, rows in split_samples.items()
    }
    print("selecting probability, exposure, and monthly loss limit on 2024-2025", flush=True)
    selection_rows = []
    sample_probabilities = tuple(
        probabilities.get(sample.index, Decimal("0")) for sample in samples
    )
    for probability_threshold in PROBABILITY_THRESHOLDS:
        for exposure in EXPOSURES:
            targets = filtered_event_targets(
                base_targets,
                samples,
                sample_probabilities,
                probability_threshold=probability_threshold,
                exposure=exposure,
            )
            for monthly_loss_limit in MONTHLY_LOSS_LIMITS:
                result = _evaluate(
                    eth,
                    eth_funding,
                    targets,
                    SELECTION,
                    BASE_FEE_BPS,
                    BASE_SLIPPAGE_BPS,
                    monthly_loss_limit,
                )
                selection_rows.append(
                    {
                        "probability_threshold": probability_threshold,
                        "exposure": exposure,
                        "monthly_loss_limit": monthly_loss_limit,
                        "targets": targets,
                        "result": result,
                        "score": _selection_score(result),
                    }
                )
    eligible = [row for row in selection_rows if _selection_eligible(row["result"])]
    ranked = sorted(eligible or selection_rows, key=lambda row: row["score"], reverse=True)
    selected = ranked[0]
    confirmation = _evaluate(
        eth,
        eth_funding,
        selected["targets"],
        CONFIRMATION,
        BASE_FEE_BPS,
        BASE_SLIPPAGE_BPS,
        selected["monthly_loss_limit"],
    )
    stress = _evaluate(
        eth,
        eth_funding,
        selected["targets"],
        CONFIRMATION,
        STRESS_FEE_BPS,
        STRESS_SLIPPAGE_BPS,
        selected["monthly_loss_limit"],
    )
    base_confirmation = _evaluate_base(eth, eth_funding, base_targets, CONFIRMATION)
    feature_importance = _feature_importance(models)
    payload = _report(
        btc_source,
        eth_source,
        samples,
        split_samples,
        classification,
        seeds,
        checkpoint_paths,
        models,
        feature_importance,
        selection_rows,
        eligible,
        ranked[:20],
        selected,
        base_confirmation,
        confirmation,
        stress,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"event-meta-factor-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _libraries() -> tuple[Any, Any, Any]:
    try:
        import numpy as np
        import xgboost as xgb
        from sklearn import metrics
    except ImportError as exc:
        raise RuntimeError(
            "event meta-factor worker requires /home/spaceaic/env/.venv with xgboost"
        ) from exc
    return xgb, np, metrics


def _samples_in_period(
    samples: tuple[EventSample, ...], period: tuple[int, int]
) -> tuple[EventSample, ...]:
    return tuple(
        sample
        for sample in samples
        if period[0] <= sample.timestamp_ms and sample.exit_timestamp_ms <= period[1]
    )


def _matrix(np: Any, samples: tuple[EventSample, ...]) -> tuple[Any, Any]:
    features = np.asarray(
        [[float(value) for value in sample.features] for sample in samples], dtype=np.float32
    )
    labels = np.asarray([sample.profitable for sample in samples], dtype=np.float32)
    return features, labels


def _ensemble_probabilities(
    xgb: Any,
    np: Any,
    models: list[Any],
    samples: tuple[EventSample, ...],
) -> dict[int, Decimal]:
    matrix, _labels = _matrix(np, samples)
    data = xgb.DMatrix(matrix, feature_names=list(FEATURE_NAMES))
    predictions = []
    for model in models:
        end = model.best_iteration + 1 if model.best_iteration is not None else 0
        predictions.append(model.predict(data, iteration_range=(0, end)))
    mean = np.mean(np.stack(predictions), axis=0)
    return {
        sample.index: Decimal(str(float(probability)))
        for sample, probability in zip(samples, mean, strict=True)
    }


def _classification_summary(
    np: Any,
    metrics: Any,
    samples: tuple[EventSample, ...],
    probabilities: tuple[Decimal, ...],
) -> dict[str, Any]:
    labels = np.asarray([sample.profitable for sample in samples], dtype=np.int8)
    predicted = np.asarray([float(value) for value in probabilities], dtype=np.float64)
    return {
        "events": len(samples),
        "positive_rate": float(labels.mean()),
        "roc_auc": float(metrics.roc_auc_score(labels, predicted)),
        "average_precision": float(metrics.average_precision_score(labels, predicted)),
        "brier_score": float(metrics.brier_score_loss(labels, predicted)),
        "accuracy_at_0p5": float(metrics.accuracy_score(labels, predicted >= 0.5)),
    }


def _evaluate(
    bars: list[ResearchBar],
    funding: list[list[FundingRate]],
    targets: tuple[Decimal | None, ...],
    period: tuple[int, int],
    fee_bps: Decimal,
    slippage_bps: Decimal,
    monthly_loss_limit: Decimal,
) -> ResearchResult:
    return evaluate_weighted_targets(
        bars,
        targets,
        start_ms=period[0],
        end_ms=period[1],
        funding=funding,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        monthly_loss_limit=monthly_loss_limit,
    )


def _evaluate_base(
    bars: list[ResearchBar],
    funding: list[list[FundingRate]],
    targets: tuple[int | None, ...],
    period: tuple[int, int],
) -> ResearchResult:
    weighted = tuple(Decimal(value) if value is not None else None for value in targets)
    return _evaluate(
        bars,
        funding,
        weighted,
        period,
        BASE_FEE_BPS,
        BASE_SLIPPAGE_BPS,
        Decimal("0.15"),
    )


def _selection_eligible(result: ResearchResult) -> bool:
    return bool(
        result.net_return > 0
        and result.max_drawdown >= -0.35
        and result.completed_trades >= 8
        and _positive_month_rate(result) >= 0.5
        and not result.bankrupt
    )


def _selection_score(result: ResearchResult) -> tuple[float, ...]:
    monthly = [value for _label, value in result.monthly_returns]
    return (
        sum(value >= 0.25 for value in monthly) / len(monthly),
        sum(value > 0 for value in monthly) / len(monthly),
        min(monthly),
        result.net_return,
        result.max_drawdown,
    )


def _feature_importance(models: list[Any]) -> list[dict[str, Any]]:
    totals = {name: 0.0 for name in FEATURE_NAMES}
    for model in models:
        scores = model.get_score(importance_type="gain")
        for name in FEATURE_NAMES:
            totals[name] += float(scores.get(name, 0.0))
    return [
        {"feature": name, "mean_gain": value / len(models)}
        for name, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]


def _report(
    btc_source: list[ResearchBar],
    eth_source: list[ResearchBar],
    samples: tuple[EventSample, ...],
    split_samples: dict[str, tuple[EventSample, ...]],
    classification: dict[str, dict[str, Any]],
    seeds: tuple[int, ...],
    checkpoints: list[str],
    models: list[Any],
    feature_importance: list[dict[str, Any]],
    selection_rows: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    top_rows: list[dict[str, Any]],
    selected: dict[str, Any],
    base_confirmation: ResearchResult,
    confirmation: ResearchResult,
    stress: ResearchResult,
) -> dict[str, Any]:
    achieved = bool(
        _target_month_rate(confirmation) >= 0.5
        and confirmation.max_drawdown >= -0.35
        and confirmation.net_return > 0
        and stress.net_return > 0
        and stress.max_drawdown >= -0.35
        and not confirmation.bankrupt
        and not stress.bankrupt
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "GPU XGBoost meta-label filter for BTC-shock-to-ETH events",
        "data": {
            "first_bar": _timestamp(max(btc_source[0].start_ms, eth_source[0].start_ms)),
            "last_bar": _timestamp(min(btc_source[-1].end_ms, eth_source[-1].end_ms)),
            "btc_bars_15m": len(btc_source),
            "eth_bars_15m": len(eth_source),
            "events": len(samples),
        },
        "periods": {
            "model_train": _period(MODEL_TRAIN),
            "early_stop": _period(EARLY_STOP),
            "selection": _period(SELECTION),
            "confirmation": _period(CONFIRMATION),
        },
        "base_factor": BASE_CANDIDATE.as_dict(),
        "features": list(FEATURE_NAMES),
        "model": {
            "architecture": "three-seed GPU XGBoost ensemble",
            "seeds": list(seeds),
            "device": "cuda",
            "checkpoint_paths": checkpoints,
            "best_iterations": [model.best_iteration for model in models],
            "feature_importance": feature_importance,
        },
        "classification": classification,
        "event_counts": {name: len(rows) for name, rows in split_samples.items()},
        "execution": {
            "signal_timing": "closed 4h bar",
            "fill_timing": "next 4h open",
            "fee_bps_per_fill": float(BASE_FEE_BPS),
            "slippage_bps_per_fill": float(BASE_SLIPPAGE_BPS),
            "funding": "historical ETH funding while positioned",
            "liquidation_modeled": False,
        },
        "selection": {
            "candidate_count": len(selection_rows),
            "eligible_count": len(eligible),
            "used_fallback_diagnostic": not eligible,
            "confirmation_used_for_selection": False,
            "rule": (
                "on 2024-2025 require positive return, at least eight trades, at least half of "
                "months positive, and max drawdown no worse than 35%; rank by 25% month coverage, "
                "positive months, worst month, return, and drawdown"
            ),
            "selected": {
                "probability_threshold": float(selected["probability_threshold"]),
                "exposure": float(selected["exposure"]),
                "monthly_loss_limit": float(selected["monthly_loss_limit"]),
                "result": _summary(selected["result"]),
            },
            "top_candidates": [_selection_row(row) for row in top_rows],
        },
        "base_confirmation": _summary(base_confirmation),
        "confirmation": _summary(confirmation, include_daily=True),
        "stress_confirmation": _summary(stress),
        "target": {
            "monthly_return": 0.25,
            "minimum_confirmation_target_month_rate": 0.5,
            "achieved": achieved,
        },
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The meta-label factor reached the research return gate, but 2026 is a reused "
                "holdout and forward evidence is required."
                if achieved
                else "No meta-label configuration passed development selection; the "
                "highest-ranked fallback diagnostic also failed confirmation and cost stress."
            ),
        },
        "limitations": [
            "2026 has been viewed in prior studies and is not a fresh independent holdout.",
            "The sparse event sample is small relative to ordinary bar-level ML datasets.",
            "Liquidation, market impact, exchange failure, and shared margin are not modeled.",
        ],
    }


def _selection_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "probability_threshold": float(row["probability_threshold"]),
        "exposure": float(row["exposure"]),
        "monthly_loss_limit": float(row["monthly_loss_limit"]),
        "score": list(row["score"]),
        "result": _summary(row["result"]),
    }


def _summary(result: ResearchResult, *, include_daily: bool = False) -> dict[str, Any]:
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
        "daily_returns": (
            [{"label": label, "return": value} for label, value in result.daily_returns]
            if include_daily
            else []
        ),
        "monthly_returns": [
            {"label": label, "return": value} for label, value in result.monthly_returns
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only GPU event meta-label factor.",
        "",
        f"Decision: `{payload['decision']['status']}`.",
        f"Events: `{payload['data']['events']}`; selection candidates: "
        f"`{payload['selection']['candidate_count']}`; eligible: "
        f"`{payload['selection']['eligible_count']}`.",
        "",
        f"Selected probability: `{selected['probability_threshold']:.2f}`; exposure: "
        f"`{selected['exposure']:.1f}x`; monthly loss limit: "
        f"`{selected['monthly_loss_limit']:.1%}`.",
        "",
        "| Replay | Return | Max DD | Trades | Positive months | 25% months |",
        "|---|---:|---:|---:|---:|---:|",
        _markdown_row(
            "selection fallback"
            if payload["selection"]["used_fallback_diagnostic"]
            else "selection",
            selected["result"],
        ),
        _markdown_row("base confirmation", payload["base_confirmation"]),
        _markdown_row("meta confirmation", confirmation),
        _markdown_row("stress confirmation", stress),
        "",
        "## Confirmation monthly returns",
        "",
        "| Month | Return |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {row['label']} | {row['return']:.2%} |" for row in confirmation["monthly_returns"]
    )
    lines.extend(
        [
            "",
            "## Classification",
            "",
            "| Split | Events | Positive | ROC AUC | Average precision |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name, row in payload["classification"].items():
        lines.append(
            f"| {name} | {row['events']} | {row['positive_rate']:.2%} | "
            f"{row['roc_auc']:.3f} | {row['average_precision']:.3f} |"
        )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _markdown_row(label: str, row: dict[str, Any]) -> str:
    return (
        f"| {label} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | "
        f"{row['completed_trades']} | {row['positive_month_rate']:.2%} | "
        f"{row['target_25pct_month_rate']:.2%} |"
    )


def _positive_month_rate(result: ResearchResult) -> float:
    return sum(value > 0 for _label, value in result.monthly_returns) / len(result.monthly_returns)


def _target_month_rate(result: ResearchResult) -> float:
    return sum(value >= 0.25 for _label, value in result.monthly_returns) / len(
        result.monthly_returns
    )


def _require_aligned_bars(left: list[ResearchBar], right: list[ResearchBar]) -> None:
    if len(left) != len(right) or any(
        first.start_ms != second.start_ms for first, second in zip(left, right, strict=True)
    ):
        raise ValueError("BTC and ETH meta-label bars are not aligned")


def _period(value: tuple[int, int]) -> dict[str, str]:
    return {"start": _timestamp(value[0]), "end": _timestamp(value[1])}


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
