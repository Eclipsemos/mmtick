"""Cross-asset, multi-horizon Transformer factor research.

This module is research-only.  It trains on causal 4h BTC/ETH windows, converts the frozen
predictions into signed target exposures, and evaluates them with historical funding and explicit
transaction costs.  PyTorch remains a worker-only dependency so the API process can import this
module without the GPU environment.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import ResearchBar, ResearchResult, aggregate_bars, funding_by_bar
from mastermind_tick.factor_mining import load_market, split_periods
from mastermind_tick.factor_portfolio import decimal_returns, evaluate_static_portfolio
from mastermind_tick.lead_lag_factor import evaluate_weighted_targets

V2Progress = Callable[[str, float], None]
ASSETS = ("btc_perp", "eth_perp")
BASE_FEE_BPS = Decimal("5")
BASE_SLIPPAGE_BPS = Decimal("2")
STRESS_FEE_BPS = Decimal("10")
STRESS_SLIPPAGE_BPS = Decimal("5")


@dataclass(frozen=True)
class DeepFactorV2Config:
    instruments: tuple[str, ...] = ASSETS
    bar_interval_minutes: int = 240
    sequence_length: int = 64
    horizons: tuple[int, ...] = (1, 6, 18)
    model_dim: int = 128
    layers: int = 4
    heads: int = 4
    batch_size: int = 512
    epochs: int = 8
    patience: int = 2
    learning_rate: float = 0.0003
    weight_decay: float = 0.0003
    seed: int = 42
    ensemble_seeds: tuple[int, ...] = (11, 23, 42)
    fit_end_year: int = 2022
    fee_bps: float = 5.0
    slippage_bps: float = 2.0


@dataclass(frozen=True)
class V2SignalCandidate:
    direction: str
    horizon_bars: int
    score_threshold: float
    smoothing_bars: int
    minimum_hold_bars: int
    cooldown_bars: int
    confirmation_bars: int
    exposure: Decimal = Decimal("1")
    monthly_loss_limit: Decimal | None = None

    def __post_init__(self) -> None:
        if self.direction not in {"long_only", "long_short"}:
            raise ValueError("unsupported v2 signal direction")
        if self.horizon_bars < 1 or self.score_threshold <= 0:
            raise ValueError("v2 signal horizon and threshold must be positive")
        if self.exposure <= 0 or self.exposure > Decimal("10"):
            raise ValueError("v2 signal exposure must be between zero and ten")
        if self.monthly_loss_limit is not None and not Decimal(
            "0"
        ) < self.monthly_loss_limit < Decimal("1"):
            raise ValueError("v2 monthly loss limit must be between zero and one")

    @property
    def id(self) -> str:
        direction = "long" if self.direction == "long_only" else "long-short"
        threshold = f"{self.score_threshold:.2f}".replace(".", "p")
        exposure = f"{self.exposure:g}".replace(".", "p")
        loss = (
            "none"
            if self.monthly_loss_limit is None
            else f"{self.monthly_loss_limit:g}".replace(".", "p")
        )
        return (
            f"{direction}-h{self.horizon_bars}-z{threshold}-ema{self.smoothing_bars}"
            f"-hold{self.minimum_hold_bars}-cool{self.cooldown_bars}"
            f"-confirm{self.confirmation_bars}-exp{exposure}-loss{loss}"
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "id": self.id,
                "exposure": float(self.exposure),
                "monthly_loss_limit": (
                    float(self.monthly_loss_limit) if self.monthly_loss_limit is not None else None
                ),
            }
        )
        return payload


@dataclass(frozen=True)
class _Sample:
    instrument_index: int
    end_index: int
    label: tuple[float, ...]


def run_deep_factor_v2_mining(
    database: Path,
    output_root: Path,
    config: DeepFactorV2Config,
    progress_callback: V2Progress | None = None,
    report_root: Path | None = None,
) -> dict[str, Any]:
    """Train the v2 model, freeze predictions, and write a reproducible report."""
    torch, np = _libraries()
    if tuple(config.instruments) != ASSETS:
        raise ValueError("deep factor v2 currently requires aligned BTC and ETH")
    if config.bar_interval_minutes < 15 or config.bar_interval_minutes % 15:
        raise ValueError("deep factor v2 bar interval must be a positive 15m multiple")
    _seed_everything(torch, config.seed)
    _progress(progress_callback, "读取 BTC / ETH 15m 数据并聚合 4h", 0.03)
    loaded = {instrument: load_market(database, instrument) for instrument in config.instruments}
    bars = {
        instrument: aggregate_bars(loaded[instrument][0], config.bar_interval_minutes)
        for instrument in config.instruments
    }
    _require_aligned(bars[ASSETS[0]], bars[ASSETS[1]])
    funding = {
        instrument: funding_by_bar(bars[instrument], loaded[instrument][1])
        for instrument in config.instruments
    }
    series = _prepare_series(np, bars, funding, config, progress_callback)
    fit_samples = [sample for item in series for sample in item["samples"]["fit"]]
    checkpoint_samples = [sample for item in series for sample in item["samples"]["checkpoint"]]
    if len(fit_samples) < 128 or len(checkpoint_samples) < 32:
        raise ValueError("not enough causal windows for deep factor v2")
    _progress(progress_callback, f"准备 {len(fit_samples):,} 个 4h 训练窗口", 0.15)

    models: list[Any] = []
    histories: list[dict[str, Any]] = []
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    for model_index, seed in enumerate(config.ensemble_seeds):
        _seed_everything(torch, seed)
        model, history = _train_model(
            torch,
            series,
            fit_samples,
            checkpoint_samples,
            config,
            device,
            seed,
            progress_callback,
            model_index,
        )
        models.append(model)
        histories.append({"seed": seed, "epochs": history})
    _progress(progress_callback, "评估冻结集成预测与成本后信号", 0.67)

    metrics: dict[str, Any] = {}
    signal_search: dict[str, Any] = {}
    selected_results: dict[str, dict[str, ResearchResult]] = {}
    stress_results: dict[str, ResearchResult] = {}
    for index, item in enumerate(series):
        output, selected, stress = _evaluate_asset(
            torch,
            np,
            models,
            item,
            bars,
            funding,
            config,
            device,
            progress_callback,
            index,
        )
        metrics[item["instrument"]] = output["metrics"]
        signal_search[item["instrument"]] = output["signal_search"]
        selected_results[item["instrument"]] = selected
        stress_results[item["instrument"]] = stress

    portfolio = _select_portfolio(selected_results, stress_results, signal_search)
    report = _build_report(
        report_root or output_root / "reports" / "experiments" / "deep_factor_v2",
        config,
        bars,
        device,
        torch,
        sum(parameter.numel() for parameter in models[0].parameters()),
        histories,
        metrics,
        signal_search,
        portfolio,
    )
    _progress(progress_callback, "深度因子 v2 报告已生成", 1.0)
    return report


def _libraries() -> tuple[Any, Any]:
    try:
        import numpy as np
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "deep factor v2 requires /home/spaceaic/env/.venv with torch and numpy"
        ) from exc
    return torch, np


def _prepare_series(
    np: Any,
    bars: dict[str, list[ResearchBar]],
    funding: dict[str, list[list[Any]]],
    config: DeepFactorV2Config,
    callback: V2Progress | None,
) -> list[dict[str, Any]]:
    raw = {
        instrument: _raw_features(np, bars[instrument], funding[instrument])
        for instrument in ASSETS
    }
    periods = split_periods(ASSETS[0], bars[ASSETS[0]][-1].end_ms)
    train_start, train_end = periods["train"]
    fit_end = min(train_end, _year_end_ms(config.fit_end_year))
    result: list[dict[str, Any]] = []
    for instrument_index, instrument in enumerate(ASSETS):
        own = raw[instrument]
        other = raw[ASSETS[1 - instrument_index]]
        relative = np.column_stack(
            (
                own[:, 0] - other[:, 0],
                own[:, 1] - other[:, 1],
                own[:, 2] - other[:, 2],
                own[:, 3] - other[:, 3],
                own[:, 7] - other[:, 7],
            )
        )
        combined = np.column_stack((own, other, relative))
        fit_mask = np.array(
            [train_start <= bar.start_ms <= fit_end for bar in bars[instrument]], dtype=bool
        )
        mean = np.nanmean(np.where(fit_mask[:, None], combined, np.nan), axis=0)
        std = np.nanstd(np.where(fit_mask[:, None], combined, np.nan), axis=0)
        std = np.where(np.isfinite(std) & (std > 1e-8), std, 1.0)
        features = np.nan_to_num((combined - mean) / std, nan=0.0, posinf=8.0, neginf=-8.0).astype(
            np.float32
        )
        closes = np.array([float(bar.close) for bar in bars[instrument]], dtype=np.float64)
        opens = np.array([float(bar.open) for bar in bars[instrument]], dtype=np.float64)
        returns = np.full(len(closes), np.nan, dtype=np.float64)
        returns[1:] = closes[1:] / closes[:-1] - 1.0
        volatility = _rolling_std(np, returns, 42)
        samples = {
            name: [] for name in ("fit", "checkpoint", "train", "validation", "confirmation")
        }
        max_horizon = max(config.horizons)
        for end_index in range(config.sequence_length - 1, len(bars[instrument]) - max_horizon):
            labels = tuple(
                _normalized_return(
                    np,
                    closes[end_index + horizon] / opens[end_index + 1] - 1.0,
                    volatility[end_index],
                    horizon,
                )
                for horizon in config.horizons
            )
            bar_start = bars[instrument][end_index].start_ms
            sample = _Sample(instrument_index, end_index, labels)
            if (
                train_start <= bar_start <= fit_end
                and bars[instrument][end_index + max_horizon].end_ms <= fit_end
            ):
                samples["fit"].append(sample)
            if (
                fit_end < bar_start <= train_end
                and bars[instrument][end_index + max_horizon].end_ms <= train_end
            ):
                samples["checkpoint"].append(sample)
            for name, (start_ms, end_ms) in periods.items():
                if (
                    start_ms <= bar_start <= end_ms
                    and bars[instrument][end_index + max_horizon].end_ms <= end_ms
                ):
                    samples[name].append(sample)
        result.append(
            {
                "instrument": instrument,
                "instrument_index": instrument_index,
                "bars": bars[instrument],
                "features": features,
                "samples": samples,
                "first_bar": _timestamp(bars[instrument][0].start_ms),
                "last_bar": _timestamp(bars[instrument][-1].end_ms),
            }
        )
        _progress(
            callback, f"构建 {instrument} 跨资产 4h 特征", 0.08 + 0.05 * (instrument_index + 1)
        )
    return result


def _raw_features(np: Any, bars: list[ResearchBar], funding: list[list[Any]]) -> Any:
    close = np.array([float(bar.close) for bar in bars], dtype=np.float64)
    high = np.array([float(bar.high) for bar in bars], dtype=np.float64)
    low = np.array([float(bar.low) for bar in bars], dtype=np.float64)
    volume = np.array([float(bar.volume) for bar in bars], dtype=np.float64)
    returns = np.full(len(close), np.nan, dtype=np.float64)
    returns[1:] = close[1:] / close[:-1] - 1.0
    result = np.column_stack(
        (
            returns,
            _lag_return(np, close, 3),
            _lag_return(np, close, 6),
            _lag_return(np, close, 18),
            _lag_return(np, close, 42),
            _rolling_mean(np, returns, 6),
            _rolling_std(np, returns, 6),
            _rolling_std(np, returns, 42),
            _rolling_mean(np, (high - low) / close, 6),
            np.log1p(volume) - np.log1p(_rolling_mean(np, volume, 18)),
            close / _rolling_mean(np, close, 42) - 1.0,
            np.array([float(sum((event.rate for event in row), Decimal("0"))) for row in funding]),
        )
    )
    return result


def _lag_return(np: Any, values: Any, lag: int) -> Any:
    result = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) > lag:
        result[lag:] = values[lag:] / values[:-lag] - 1.0
    return result


def _rolling_mean(np: Any, values: Any, window: int) -> Any:
    result = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) < window:
        return result
    valid = np.isfinite(values).astype(np.int64)
    total = np.cumsum(np.nan_to_num(values, nan=0.0))
    count = np.cumsum(valid)
    sums = total[window - 1 :] - np.r_[0.0, total[:-window]]
    counts = count[window - 1 :] - np.r_[0, count[:-window]]
    result[window - 1 :] = np.divide(sums, counts, out=np.full_like(sums, np.nan), where=counts > 0)
    return result


def _rolling_std(np: Any, values: Any, window: int) -> Any:
    mean = _rolling_mean(np, values, window)
    squared = _rolling_mean(np, values * values, window)
    return np.sqrt(np.maximum(squared - mean * mean, 0.0))


def _normalized_return(np: Any, value: float, volatility: float, horizon: int) -> float:
    if not np.isfinite(volatility) or volatility <= 1e-8:
        return 0.0
    return float(np.clip(value / (volatility * math.sqrt(horizon)), -6.0, 6.0))


def _train_model(
    torch: Any,
    series: list[dict[str, Any]],
    fit_samples: list[_Sample],
    checkpoint_samples: list[_Sample],
    config: DeepFactorV2Config,
    device: Any,
    seed: int,
    callback: V2Progress | None,
    model_index: int,
) -> tuple[Any, list[dict[str, float]]]:
    model = _CrossAssetTransformer(
        torch,
        feature_count=series[0]["features"].shape[1],
        sequence_length=config.sequence_length,
        horizon_count=len(config.horizons),
        instrument_count=len(series),
        model_dim=config.model_dim,
        layers=config.layers,
        heads=config.heads,
    ).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    train_loader = _loader(
        torch, series, fit_samples, config.sequence_length, config.batch_size, True
    )
    checkpoint_loader = _loader(
        torch, series, checkpoint_samples, config.sequence_length, config.batch_size, False
    )
    best_loss = math.inf
    best_state: dict[str, Any] | None = None
    stale = 0
    history: list[dict[str, float]] = []
    for epoch in range(1, config.epochs + 1):
        model.train()
        train_loss = 0.0
        batch_count = 0
        for features, instrument_ids, labels in train_loader:
            features = features.to(device, non_blocking=True)
            instrument_ids = instrument_ids.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with _autocast(torch, device):
                logits, predicted = model(features, instrument_ids)
                direction = (labels > 0).float()
                loss = torch.nn.functional.binary_cross_entropy_with_logits(logits, direction)
                loss = loss + 0.7 * torch.nn.functional.smooth_l1_loss(predicted, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += float(loss.detach().cpu())
            batch_count += 1
        validation_loss = _loss(torch, model, checkpoint_loader, device)
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss / max(1, batch_count),
                "validation_loss": validation_loss,
            }
        )
        _progress(
            callback,
            (
                f"集成 {model_index + 1}/{len(config.ensemble_seeds)} · seed {seed} · "
                f"epoch {epoch}/{config.epochs}"
            ),
            0.16 + 0.45 * (model_index + (epoch / config.epochs)) / len(config.ensemble_seeds),
        )
        if validation_loss < best_loss:
            best_loss = validation_loss
            stale = 0
            best_state = {
                key: value.detach().cpu().clone() for key, value in model.state_dict().items()
            }
        else:
            stale += 1
            if stale >= config.patience:
                break
    if best_state is None:
        raise RuntimeError("deep factor v2 model did not produce a checkpoint")
    model.load_state_dict(best_state)
    return model, history


def _loader(
    torch: Any,
    series: list[dict[str, Any]],
    samples: list[_Sample],
    sequence_length: int,
    batch_size: int,
    shuffle: bool,
) -> Any:
    class WindowDataset(torch.utils.data.Dataset):
        def __len__(self) -> int:
            return len(samples)

        def __getitem__(self, index: int) -> tuple[Any, Any, Any]:
            sample = samples[index]
            item = series[sample.instrument_index]
            end = sample.end_index + 1
            return (
                torch.from_numpy(item["features"][end - sequence_length : end]),
                torch.tensor(sample.instrument_index, dtype=torch.long),
                torch.tensor(sample.label, dtype=torch.float32),
            )

    return torch.utils.data.DataLoader(
        WindowDataset(),
        batch_size=batch_size,
        shuffle=shuffle,
        pin_memory=torch.cuda.is_available(),
        num_workers=0,
    )


def _loss(torch: Any, model: Any, loader: Any, device: Any) -> float:
    model.eval()
    total = 0.0
    count = 0
    with torch.no_grad():
        for features, instrument_ids, labels in loader:
            logits, predicted = model(features.to(device), instrument_ids.to(device))
            labels = labels.to(device)
            value = torch.nn.functional.binary_cross_entropy_with_logits(
                logits, (labels > 0).float()
            )
            value = value + 0.7 * torch.nn.functional.smooth_l1_loss(predicted, labels)
            total += float(value.cpu())
            count += 1
    return total / max(1, count)


def _CrossAssetTransformer(torch: Any, **kwargs: Any) -> Any:
    class Model(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            dimension = kwargs["model_dim"]
            self.input = torch.nn.Linear(kwargs["feature_count"], dimension)
            self.position = torch.nn.Parameter(torch.zeros(1, kwargs["sequence_length"], dimension))
            self.instrument = torch.nn.Embedding(kwargs["instrument_count"], dimension)
            layer = torch.nn.TransformerEncoderLayer(
                d_model=dimension,
                nhead=kwargs["heads"],
                dim_feedforward=dimension * 4,
                dropout=0.1,
                batch_first=True,
                norm_first=True,
                activation="gelu",
            )
            self.encoder = torch.nn.TransformerEncoder(layer, num_layers=kwargs["layers"])
            self.norm = torch.nn.LayerNorm(dimension)
            self.direction = torch.nn.Linear(dimension, kwargs["horizon_count"])
            self.returns = torch.nn.Linear(dimension, kwargs["horizon_count"])

        def forward(self, features: Any, instrument_ids: Any) -> tuple[Any, Any]:
            state = self.input(features) + self.position[:, : features.shape[1]]
            state = state + self.instrument(instrument_ids)[:, None, :]
            state = self.norm(self.encoder(state)[:, -1])
            return self.direction(state), self.returns(state)

    return Model()


def _evaluate_asset(
    torch: Any,
    np: Any,
    models: list[Any],
    item: dict[str, Any],
    bars: dict[str, list[ResearchBar]],
    funding: dict[str, list[list[Any]]],
    config: DeepFactorV2Config,
    device: Any,
    callback: V2Progress | None,
    asset_index: int,
) -> tuple[dict[str, Any], dict[str, ResearchResult], ResearchResult]:
    predictions: dict[str, tuple[list[_Sample], Any, Any]] = {}
    for split_index, split in enumerate(("train", "validation", "confirmation")):
        samples = item["samples"][split]
        loader = _loader(
            torch,
            [item],
            [_Sample(0, sample.end_index, sample.label) for sample in samples],
            config.sequence_length,
            config.batch_size,
            False,
        )
        scores: list[Any] = []
        labels: list[Any] = []
        with torch.no_grad():
            for features, _ids, batch_labels in loader:
                batch_predictions = []
                for model in models:
                    logits, predicted = model(
                        features.to(device),
                        torch.full(
                            (features.shape[0],),
                            item["instrument_index"],
                            dtype=torch.long,
                            device=device,
                        ),
                    )
                    # The classifier supplies the signed factor. Regression is retained as a
                    # regularizer and contributes only a small stabilizing term.
                    batch_predictions.append((logits + 0.15 * predicted).cpu().numpy())
                scores.append(np.mean(batch_predictions, axis=0))
                labels.append(batch_labels.numpy())
        prediction = np.concatenate(scores) if scores else np.empty((0, len(config.horizons)))
        actual = np.concatenate(labels) if labels else np.empty_like(prediction)
        predictions[split] = (samples, prediction, actual)
        _progress(
            callback,
            f"评估 {item['instrument']} {split}",
            0.67 + 0.25 * (asset_index + (split_index + 1) / 3) / len(ASSETS),
        )

    score_by_horizon = {horizon: [None] * len(item["bars"]) for horizon in config.horizons}
    for samples, prediction, _actual in predictions.values():
        for sample, values in zip(samples, prediction, strict=True):
            for horizon_index, horizon in enumerate(config.horizons):
                score_by_horizon[horizon][sample.end_index] = float(values[horizon_index])
    periods = split_periods(item["instrument"], item["bars"][-1].end_ms)
    base_rows: list[dict[str, Any]] = []
    for candidate in _signal_candidates(config.horizons):
        targets = _managed_targets(score_by_horizon[candidate.horizon_bars], candidate)
        results = _evaluate_candidate(item["bars"], funding[item["instrument"]], targets, periods)
        base_rows.append(
            {
                "candidate": candidate,
                "targets": targets,
                "results": results,
                "score": _selection_score(results),
            }
        )
    eligible = [
        row
        for row in base_rows
        if row["results"]["train"].net_return > 0
        and row["results"]["validation"].net_return > 0
        and row["results"]["train"].completed_trades >= 6
        and row["results"]["validation"].completed_trades >= 6
    ]
    ranked_base = sorted(eligible or base_rows, key=lambda row: row["score"], reverse=True)
    risk_rows: list[dict[str, Any]] = []
    for row in ranked_base[:12]:
        base = row["candidate"]
        for exposure in (
            Decimal("1"),
            Decimal("1.5"),
            Decimal("2"),
            Decimal("2.5"),
            Decimal("3"),
            Decimal("4"),
        ):
            for loss in (None, Decimal("0.10"), Decimal("0.15")):
                candidate = V2SignalCandidate(
                    **{**asdict(base), "exposure": exposure, "monthly_loss_limit": loss}
                )
                targets = tuple(
                    None if value is None else Decimal(value) * exposure for value in row["targets"]
                )
                results = _evaluate_candidate(
                    item["bars"], funding[item["instrument"]], targets, periods, loss
                )
                risk_rows.append(
                    {
                        "candidate": candidate,
                        "targets": targets,
                        "results": results,
                        "score": _selection_score(results),
                    }
                )
    eligible_risk = [row for row in risk_rows if _risk_eligible(row["results"])]
    ranked = sorted(eligible_risk, key=lambda row: row["score"], reverse=True) or sorted(
        risk_rows, key=lambda row: row["score"], reverse=True
    )
    selected = ranked[0]
    selected_results = selected["results"]
    stress = _evaluate_candidate(
        item["bars"],
        funding[item["instrument"]],
        selected["targets"],
        periods,
        selected["candidate"].monthly_loss_limit,
        stress=True,
    )["confirmation"]
    metrics = {
        split: {
            "samples": len(predictions[split][0]),
            "direction_accuracy": _accuracy(
                np,
                predictions[split][1][:, config.horizons.index(selected["candidate"].horizon_bars)],
                predictions[split][2][:, config.horizons.index(selected["candidate"].horizon_bars)],
            ),
            "information_coefficient": _correlation(
                np,
                predictions[split][1][:, config.horizons.index(selected["candidate"].horizon_bars)],
                predictions[split][2][:, config.horizons.index(selected["candidate"].horizon_bars)],
            ),
            **_result_summary(selected_results[split]),
        }
        for split in ("train", "validation", "confirmation")
    }
    search = {
        "candidate_count": len(_signal_candidates(config.horizons)),
        "development_eligible_count": len(eligible),
        "risk_configuration_count": len(risk_rows),
        "risk_eligible_count": len(eligible_risk),
        "used_fallback_diagnostic": not eligible_risk,
        "selection_rule": (
            "Train (2020-2023) and validation (2024-2025) only; 2026 confirmation excluded."
        ),
        "selected": {
            "parameters": selected["candidate"].as_dict(),
            "score": list(selected["score"]),
        },
        "stress_confirmation": {
            "fee_bps": float(STRESS_FEE_BPS),
            "slippage_bps": float(STRESS_SLIPPAGE_BPS),
            **_result_summary(stress),
        },
        "top_development_candidates": [
            {
                "parameters": row["candidate"].as_dict(),
                "score": list(row["score"]),
                "validation": _result_summary(row["results"]["validation"]),
            }
            for row in ranked[:10]
        ],
    }
    return {"metrics": metrics, "signal_search": search}, selected_results, stress


def _signal_candidates(horizons: tuple[int, ...]) -> tuple[V2SignalCandidate, ...]:
    return tuple(
        V2SignalCandidate(direction, horizon, threshold, smoothing, hold, cooldown, confirmation)
        for direction in ("long_only", "long_short")
        for horizon in horizons
        for threshold in (0.05, 0.15, 0.25)
        for smoothing in (1, 4)
        for hold in (1, 6)
        for cooldown in (0, 4)
        for confirmation in (1, 2)
    )


def _managed_targets(
    scores: list[float | None], candidate: V2SignalCandidate
) -> tuple[int | None, ...]:
    result: list[int | None] = []
    state = 0
    hold_count = 0
    cooldown = 0
    pending = 0
    pending_count = 0
    ema: float | None = None
    alpha = 1.0 if candidate.smoothing_bars <= 1 else 2.0 / (candidate.smoothing_bars + 1)
    for score in scores:
        if score is None:
            result.append(None)
            continue
        ema = score if ema is None else ema + alpha * (score - ema)
        desired = (
            1
            if ema >= candidate.score_threshold
            else -1
            if candidate.direction == "long_short" and ema <= -candidate.score_threshold
            else 0
        )
        if state:
            hold_count += 1
            if desired == state:
                pending = 0
                pending_count = 0
            elif hold_count >= candidate.minimum_hold_bars:
                state = 0
                hold_count = 0
                cooldown = candidate.cooldown_bars
                pending = 0
                pending_count = 0
        if not state:
            if cooldown:
                cooldown -= 1
            elif desired:
                pending_count = pending_count + 1 if pending == desired else 1
                pending = desired
                if pending_count >= candidate.confirmation_bars:
                    state = desired
                    hold_count = 0
                    pending = 0
                    pending_count = 0
        result.append(state)
    return tuple(result)


def _evaluate_candidate(
    bars: list[ResearchBar],
    funding: list[list[Any]],
    targets: tuple[Decimal | None, ...] | tuple[int | None, ...],
    periods: dict[str, tuple[int, int]],
    monthly_loss_limit: Decimal | None = None,
    stress: bool = False,
) -> dict[str, ResearchResult]:
    fee = STRESS_FEE_BPS if stress else BASE_FEE_BPS
    slippage = STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS
    decimal_targets = tuple(None if value is None else Decimal(value) for value in targets)
    return {
        name: evaluate_weighted_targets(
            bars,
            decimal_targets,
            start_ms=start,
            end_ms=end,
            funding=funding,
            fee_bps=fee,
            slippage_bps=slippage,
            monthly_loss_limit=monthly_loss_limit,
        )
        for name, (start, end) in periods.items()
    }


def _risk_eligible(results: dict[str, ResearchResult]) -> bool:
    return bool(
        results["train"].net_return > 0
        and results["validation"].net_return > 0
        and results["train"].max_drawdown >= -0.35
        and results["validation"].max_drawdown >= -0.35
        and results["train"].completed_trades >= 12
        and results["validation"].completed_trades >= 12
        and _result_positive_rate(results["train"]) >= 0.5
        and _result_positive_rate(results["validation"]) >= 0.5
    )


def _selection_score(results: dict[str, ResearchResult]) -> tuple[float, ...]:
    train = _result_summary(results["train"])
    validation = _result_summary(results["validation"])
    return (
        min(train["target_25pct_month_rate"], validation["target_25pct_month_rate"]),
        min(train["positive_month_rate"], validation["positive_month_rate"]),
        min(results["train"].net_return, results["validation"].net_return),
        results["train"].net_return + results["validation"].net_return,
        min(results["train"].max_drawdown, results["validation"].max_drawdown),
    )


def _result_summary(result: ResearchResult) -> dict[str, Any]:
    monthly = [{"label": label, "return": value} for label, value in result.monthly_returns]
    returns = [row["return"] for row in monthly]
    return {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
        "ending_position": result.ending_position,
        "positive_month_rate": sum(value > 0 for value in returns) / len(returns)
        if returns
        else 0.0,
        "target_25pct_month_rate": sum(value >= 0.25 for value in returns) / len(returns)
        if returns
        else 0.0,
        "monthly_returns": monthly,
    }


def _select_portfolio(
    selected: dict[str, dict[str, ResearchResult]],
    stress: dict[str, ResearchResult],
    searches: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    eligible_components = tuple(
        name for name in ASSETS if searches[name]["risk_eligible_count"] > 0
    )
    if not eligible_components:
        return {
            "selection_status": "no_valid_components",
            "used_fallback_diagnostic": False,
            "eligible_components": [],
            "candidate_count": 0,
            "eligible_count": 0,
            "selected": None,
            "confirmation": None,
            "stress_confirmation": None,
        }

    rows: list[dict[str, Any]] = []
    validation = {
        name: decimal_returns(result["validation"].daily_returns)
        for name, result in selected.items()
        if name in eligible_components
    }
    confirmation = {
        name: decimal_returns(result["confirmation"].daily_returns)
        for name, result in selected.items()
        if name in eligible_components
    }
    stress_returns = {
        name: decimal_returns(result.daily_returns)
        for name, result in stress.items()
        if name in eligible_components
    }
    btc_weights = (
        (Decimal("1"),)
        if eligible_components == ("btc_perp",)
        else (Decimal("0"),)
        if eligible_components == ("eth_perp",)
        else (
            Decimal("0"),
            Decimal("0.25"),
            Decimal("0.5"),
            Decimal("0.75"),
            Decimal("1"),
        )
    )
    for btc_weight in btc_weights:
        for leverage in (
            Decimal("1"),
            Decimal("1.5"),
            Decimal("2"),
            Decimal("2.5"),
            Decimal("3"),
            Decimal("4"),
        ):
            weights = {
                name: btc_weight if name == "btc_perp" else Decimal("1") - btc_weight
                for name in eligible_components
            }
            result = evaluate_static_portfolio(validation, weights, leverage=leverage)
            rows.append(
                {
                    "btc_weight": btc_weight,
                    "leverage": leverage,
                    "result": result,
                    "score": _portfolio_score(result),
                }
            )
    eligible = [row for row in rows if _portfolio_eligible(row["result"])]
    ranked = sorted(eligible or rows, key=lambda row: row["score"], reverse=True)
    chosen = ranked[0]
    weights = {
        name: (chosen["btc_weight"] if name == "btc_perp" else Decimal("1") - chosen["btc_weight"])
        for name in eligible_components
    }
    confirmation_result = evaluate_static_portfolio(
        confirmation, weights, leverage=chosen["leverage"]
    )
    stress_result = evaluate_static_portfolio(stress_returns, weights, leverage=chosen["leverage"])
    return {
        "selection_status": "selected_from_valid_components",
        "used_fallback_diagnostic": False,
        "eligible_components": list(eligible_components),
        "candidate_count": len(rows),
        "eligible_count": len(eligible),
        "selected": {
            "btc_weight": float(chosen["btc_weight"]),
            "eth_weight": float(weights.get("eth_perp", Decimal("0"))),
            "leverage": float(chosen["leverage"]),
            "selection": chosen["result"].as_dict(),
        },
        "confirmation": confirmation_result.as_dict(include_daily=True),
        "stress_confirmation": stress_result.as_dict(),
    }


def _portfolio_score(result: Any) -> tuple[Decimal, ...]:
    return (
        result.target_month_rate,
        result.positive_month_rate,
        result.net_return,
        result.max_drawdown,
    )


def _portfolio_eligible(result: Any) -> bool:
    return bool(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
    )


def _build_report(
    report_root: Path,
    config: DeepFactorV2Config,
    bars: dict[str, list[ResearchBar]],
    device: Any,
    torch: Any,
    parameter_count: int,
    histories: list[dict[str, Any]],
    metrics: dict[str, Any],
    searches: dict[str, Any],
    portfolio: dict[str, Any],
) -> dict[str, Any]:
    generated = datetime.now(UTC)
    report_id = f"deep-factor-v2-{generated.strftime('%Y%m%d-%H%M%S-%f')}"
    confirmation = portfolio["confirmation"]
    stress = portfolio["stress_confirmation"]
    achieved = bool(
        confirmation is not None
        and stress is not None
        and confirmation["net_return"] > 0
        and confirmation["max_drawdown"] >= -0.35
        and confirmation["positive_month_rate"] >= 0.5
        and confirmation["target_25pct_month_rate"] >= 0.5
        and stress["net_return"] > 0
        and stress["max_drawdown"] >= -0.35
    )
    report = {
        "id": report_id,
        "generated_at": generated.isoformat(),
        "schema_version": 2,
        "model": {
            "architecture": "cross-asset causal multi-horizon Transformer encoder",
            "parameters": parameter_count,
            "ensemble_size": len(histories),
            "device": str(device),
            "torch_version": torch.__version__,
            "cuda_version": torch.version.cuda,
            "config": asdict(config),
            "training_protocol": (
                "fit 2020-2022; checkpoint 2023; signal selection 2024-2025; confirmation 2026"
            ),
        },
        "data": {
            instrument: {
                "first_bar": _timestamp(item[0].start_ms),
                "last_bar": _timestamp(item[-1].end_ms),
                "bars": len(item),
                "interval_minutes": config.bar_interval_minutes,
            }
            for instrument, item in bars.items()
        },
        "training": {
            "ensemble": histories,
            "best_validation_loss": min(
                (epoch["validation_loss"] for row in histories for epoch in row["epochs"]),
                default=None,
            ),
        },
        "metrics": metrics,
        "signal_search": searches,
        "portfolio_selection": portfolio,
        "target": {"monthly_return": 0.25, "minimum_confirmation_target_month_rate": 0.5},
        "execution": {
            "signal_timing": "closed 4h bar",
            "fill_timing": "next 4h open",
            "fee_bps": float(BASE_FEE_BPS),
            "slippage_bps": float(BASE_SLIPPAGE_BPS),
            "stress_fee_bps": float(STRESS_FEE_BPS),
            "stress_slippage_bps": float(STRESS_SLIPPAGE_BPS),
            "funding": "historical funding",
        },
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "v2 passed all confirmation and stress gates; forward evidence is still required."
            )
            if achieved
            else (
                "No asset passed the development risk gates, so no portfolio or confirmation "
                "candidate was selected."
                if portfolio["selection_status"] == "no_valid_components"
                else (
                    "v2 did not pass the independent confirmation, drawdown, target-month, "
                    "or stress gates."
                )
            ),
        },
    }
    report_dir = report_root / generated.date().isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{report_id}.json").write_text(
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (report_dir / f"{report_id}.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _markdown(report: dict[str, Any]) -> str:
    confirmation = report["portfolio_selection"]["confirmation"]
    stress = report["portfolio_selection"]["stress_confirmation"]
    selected = report["portfolio_selection"]["selected"]
    if selected is None or confirmation is None or stress is None:
        searches = report["signal_search"]
        lines = [
            f"# {report['id']}",
            "",
            "Research-only cross-asset 4h Transformer factor mining.",
            "",
            f"Decision: `{report['decision']['status']}`.",
            "",
            "No portfolio was selected because no asset produced a development-eligible signal.",
            "Fallback diagnostics below are not strategy candidates.",
            "",
            "| Asset | Development eligible | Risk eligible | Fallback diagnostic |",
            "|---|---:|---:|---:|",
        ]
        lines.extend(
            (
                f"| {instrument} | {search['development_eligible_count']} | "
                f"{search['risk_eligible_count']} | "
                f"{'yes' if search['used_fallback_diagnostic'] else 'no'} |"
            )
            for instrument, search in searches.items()
        )
        lines.extend(["", report["decision"]["reason"], ""])
        return "\n".join(lines)
    lines = [
        f"# {report['id']}",
        "",
        "Research-only cross-asset 4h Transformer factor mining.",
        "",
        f"Decision: `{report['decision']['status']}`.",
        (
            f"Portfolio: BTC `{selected['btc_weight']:.0%}`, "
            f"ETH `{selected['eth_weight']:.0%}`, leverage `{selected['leverage']:.2f}x`."
        ),
        "",
        "| Split | Return | Max DD | Positive months | 25% months |",
        "|---|---:|---:|---:|---:|",
        _markdown_row("2024-2025 selection", selected["selection"]),
        _markdown_row("2026 confirmation", confirmation),
        _markdown_row("2026 stress", stress),
        "",
        "## Confirmation monthly returns",
        "",
        "| Month | Return |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {row['label']} | {row['return']:.2%} |" for row in confirmation["monthly_returns"]
    )
    lines.extend(["", report["decision"]["reason"], ""])
    return "\n".join(lines)


def _markdown_row(label: str, result: dict[str, Any]) -> str:
    return (
        f"| {label} | {result['net_return']:.2%} | {result['max_drawdown']:.2%} | "
        f"{result['positive_month_rate']:.2%} | {result['target_25pct_month_rate']:.2%} |"
    )


def _result_target_rate(result: ResearchResult) -> float:
    return sum(value >= 0.25 for _label, value in result.monthly_returns) / max(
        1, len(result.monthly_returns)
    )


def _result_positive_rate(result: ResearchResult) -> float:
    return sum(value > 0 for _label, value in result.monthly_returns) / max(
        1, len(result.monthly_returns)
    )


def _accuracy(np: Any, predicted: Any, actual: Any) -> float | None:
    return float(np.mean((predicted >= 0) == (actual > 0))) if len(predicted) else None


def _correlation(np: Any, left: Any, right: Any) -> float | None:
    if len(left) < 2 or np.std(left) == 0 or np.std(right) == 0:
        return None
    return float(np.corrcoef(left, right)[0, 1])


def _require_aligned(left: list[ResearchBar], right: list[ResearchBar]) -> None:
    if len(left) != len(right) or any(
        first.start_ms != second.start_ms for first, second in zip(left, right, strict=True)
    ):
        raise ValueError("BTC and ETH deep factor v2 bars are not aligned")


def _year_end_ms(year: int) -> int:
    return int(datetime(year, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000)


def _timestamp(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def _seed_everything(torch: Any, seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _autocast(torch: Any, device: Any) -> Any:
    return (
        torch.autocast(device_type="cuda", dtype=torch.bfloat16)
        if device.type == "cuda"
        else nullcontext()
    )


def _progress(callback: V2Progress | None, stage: str, value: float) -> None:
    if callback is not None:
        callback(stage, max(0.0, min(1.0, value)))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value
