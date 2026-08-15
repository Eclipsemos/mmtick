"""Research-only causal Transformer factor mining.

This module is intentionally importable without PyTorch.  The GPU worker imports it from
``/home/spaceaic/env/.venv`` while the API process only launches and monitors that worker.
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

from mastermind_tick.bar_research import ResearchBar, evaluate_targets, funding_by_bar
from mastermind_tick.factor_mining import load_market, split_periods

DeepProgress = Callable[[str, float], None]


@dataclass(frozen=True)
class DeepFactorConfig:
    """Small model defaults sized for the available history, not for benchmark scores."""

    instruments: tuple[str, ...] = ("btc_perp", "eth_perp")
    sequence_length: int = 96
    horizons: tuple[int, ...] = (4, 16, 96)
    model_dim: int = 192
    layers: int = 6
    heads: int = 6
    batch_size: int = 256
    epochs: int = 12
    patience: int = 3
    learning_rate: float = 0.0003
    weight_decay: float = 0.0001
    fee_bps: float = 5.0
    slippage_bps: float = 2.0
    seed: int = 42


@dataclass(frozen=True)
class _Sample:
    instrument_index: int
    end_index: int
    label: tuple[float, ...]


def run_deep_factor_mining(
    database: Path,
    output_root: Path,
    config: DeepFactorConfig,
    progress_callback: DeepProgress | None = None,
    report_root: Path | None = None,
) -> dict[str, Any]:
    """Train a causal multi-task encoder and evaluate its frozen signal on each split."""
    torch, np = _libraries()
    _seed_everything(torch, config.seed)
    _progress(progress_callback, "读取 BTC / ETH 闭合 15m K 线", 0.03)
    loaded = {instrument: load_market(database, instrument) for instrument in config.instruments}
    series = _prepare_series(torch, np, loaded, config, progress_callback)
    train_samples = [sample for item in series for sample in item["samples"]["train"]]
    if len(train_samples) < 128:
        raise ValueError("not enough causal training windows for deep factor mining")
    _progress(progress_callback, f"准备 {len(train_samples):,} 个训练窗口", 0.16)

    model = _CausalFactorTransformer(
        torch,
        feature_count=series[0]["features"].shape[1],
        sequence_length=config.sequence_length,
        horizon_count=len(config.horizons),
        instrument_count=len(series),
        model_dim=config.model_dim,
        layers=config.layers,
        heads=config.heads,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    loss_fn = torch.nn.BCEWithLogitsLoss()
    train_loader = _loader(
        torch, series, train_samples, config.sequence_length, config.batch_size, shuffle=True
    )
    validation_samples = [sample for item in series for sample in item["samples"]["validation"]]
    validation_loader = _loader(
        torch, series, validation_samples, config.sequence_length, config.batch_size, shuffle=False
    )
    best_state: dict[str, Any] | None = None
    best_loss = math.inf
    stale_epochs = 0
    history: list[dict[str, float]] = []
    for epoch in range(1, config.epochs + 1):
        model.train()
        train_loss = 0.0
        batches = 0
        for features, instrument_ids, labels in train_loader:
            features = features.to(device, non_blocking=True)
            instrument_ids = instrument_ids.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with _autocast(torch, device):
                logits, predicted_returns = model(features, instrument_ids)
                direction_target = (
                    labels > (config.fee_bps + config.slippage_bps) / 10_000
                ).float()
                loss = loss_fn(
                    logits, direction_target
                ) + 0.35 * torch.nn.functional.smooth_l1_loss(predicted_returns, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += float(loss.detach().cpu())
            batches += 1
        validation_loss = _loss(model, validation_loader, device, torch, config, loss_fn)
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss / max(1, batches),
                "validation_loss": validation_loss,
            }
        )
        _progress(
            progress_callback,
            f"训练 epoch {epoch}/{config.epochs} · val loss {validation_loss:.4f}",
            0.16 + 0.45 * epoch / config.epochs,
        )
        if validation_loss < best_loss:
            best_loss = validation_loss
            stale_epochs = 0
            best_state = {
                key: value.detach().cpu().clone() for key, value in model.state_dict().items()
            }
        else:
            stale_epochs += 1
            if stale_epochs >= config.patience:
                break
    if best_state is None:
        raise RuntimeError("deep factor model did not produce a validation checkpoint")
    model.load_state_dict(best_state)
    checkpoint_path = (
        output_root
        / "data"
        / "deep_factor_models"
        / f"deep-factor-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.pt"
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": best_state,
            "config": asdict(config),
            "feature_count": series[0]["features"].shape[1],
        },
        checkpoint_path,
    )
    _progress(progress_callback, "评估冻结模型与成本后交易信号", 0.68)
    metrics = {}
    for index, item in enumerate(series):
        metrics[item["instrument"]] = _evaluate_asset(
            model, item, device, torch, np, config, progress_callback, index, len(series)
        )
    _progress(progress_callback, "生成深度因子研究报告", 0.96)
    generated_at = datetime.now(UTC)
    report_id = f"deep-factor-{generated_at.strftime('%Y%m%d-%H%M%S-%f')}"
    report = {
        "id": report_id,
        "generated_at": generated_at.isoformat(),
        "model": {
            "architecture": "causal temporal Transformer encoder",
            "parameters": sum(parameter.numel() for parameter in model.parameters()),
            "device": str(device),
            "torch_version": torch.__version__,
            "cuda_version": torch.version.cuda,
            "checkpoint": str(checkpoint_path),
            "config": asdict(config),
        },
        "data": {
            instrument: {
                "first_bar": item["first_bar"],
                "last_bar": item["last_bar"],
                "source_bars_15m": len(item["bars"]),
            }
            for instrument, item in ((item["instrument"], item) for item in series)
        },
        "training": {"history": history, "best_validation_loss": best_loss},
        "metrics": metrics,
        "decision": {
            "status": "research_candidate"
            if _candidate_gate(metrics)
            else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "Deep model passed the preliminary multi-asset research gates; forward observation "
                "is required and trading remains disabled."
                if _candidate_gate(metrics)
                else "Deep model did not pass the independent confirmation or drawdown gates."
            ),
        },
    }
    report_base = report_root or output_root / "reports" / "experiments" / "deep_factor"
    report_dir = report_base / generated_at.date().isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{report_id}.json").write_text(
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (report_dir / f"{report_id}.md").write_text(_markdown(report), encoding="utf-8")
    _progress(progress_callback, "深度因子报告已生成", 1.0)
    return report


def _libraries() -> tuple[Any, Any]:
    try:
        import numpy as np
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "deep factor worker requires /home/spaceaic/env/.venv with torch and numpy"
        ) from exc
    return torch, np


def _prepare_series(
    torch: Any,
    np: Any,
    loaded: dict[str, Any],
    config: DeepFactorConfig,
    callback: DeepProgress | None,
) -> list[dict[str, Any]]:
    result = []
    for offset, instrument in enumerate(config.instruments):
        bars, funding = loaded[instrument]
        raw = _raw_features(np, bars)
        periods = split_periods(instrument, bars[-1].end_ms)
        train_mask = np.array(
            [periods["train"][0] <= bar.start_ms <= periods["train"][1] for bar in bars]
        )
        mean = np.nanmean(np.where(train_mask[:, None], raw, np.nan), axis=0)
        std = np.nanstd(np.where(train_mask[:, None], raw, np.nan), axis=0)
        std = np.where(np.isfinite(std) & (std > 1e-8), std, 1.0)
        features = np.nan_to_num((raw - mean) / std, nan=0.0, posinf=8.0, neginf=-8.0).astype(
            np.float32
        )
        samples = {name: [] for name in periods}
        max_horizon = max(config.horizons)
        closes = np.array([float(bar.close) for bar in bars], dtype=np.float64)
        opens = np.array([float(bar.open) for bar in bars], dtype=np.float64)
        for end_index in range(config.sequence_length - 1, len(bars) - max_horizon):
            labels = tuple(
                float(closes[end_index + horizon] / opens[end_index + 1] - 1.0)
                for horizon in config.horizons
            )
            for name, (start_ms, end_ms) in periods.items():
                if (
                    start_ms <= bars[end_index].start_ms <= end_ms
                    and bars[end_index + max_horizon].end_ms <= end_ms
                ):
                    samples[name].append(_Sample(offset, end_index, labels))
        result.append(
            {
                "instrument": instrument,
                "instrument_index": offset,
                "bars": bars,
                "funding": funding,
                "features": features,
                "samples": samples,
                "first_bar": datetime.fromtimestamp(bars[0].start_ms / 1000, UTC).isoformat(),
                "last_bar": datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).isoformat(),
            }
        )
        _progress(
            callback,
            f"构建 {instrument} 因果窗口",
            0.08 + 0.04 * (offset + 1) / len(config.instruments),
        )
    return result


def _raw_features(np: Any, bars: list[ResearchBar]) -> Any:
    close = np.array([float(bar.close) for bar in bars], dtype=np.float64)
    high = np.array([float(bar.high) for bar in bars], dtype=np.float64)
    low = np.array([float(bar.low) for bar in bars], dtype=np.float64)
    volume = np.array([float(bar.volume) for bar in bars], dtype=np.float64)
    result = np.full((len(bars), 9), np.nan, dtype=np.float64)
    result[:, 0] = _lag_return(np, close, 1)
    result[:, 1] = _lag_return(np, close, 4)
    result[:, 2] = _lag_return(np, close, 16)
    result[:, 3] = close / _rolling_mean(np, close, 20) - 1.0
    result[:, 4] = (high - low) / close
    result[:, 5] = np.log1p(volume) - np.log1p(_rolling_mean(np, volume, 20))
    result[:, 6] = (
        np.divide(close - low, high - low, out=np.zeros_like(close), where=(high > low)) - 0.5
    )
    true_range = np.maximum(
        high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1)))
    )
    true_range[0] = np.nan
    result[:, 7] = _rolling_mean(np, true_range, 14) / close
    result[:, 8] = np.sin(np.arange(len(bars), dtype=np.float64) * 2 * np.pi / 96)
    return result


def _lag_return(np: Any, values: Any, lag: int) -> Any:
    result = np.full(len(values), np.nan)
    result[lag:] = values[lag:] / values[:-lag] - 1.0
    return result


def _rolling_mean(np: Any, values: Any, window: int) -> Any:
    result = np.full(len(values), np.nan)
    if len(values) >= window:
        cumulative = np.cumsum(np.nan_to_num(values, nan=0.0))
        counts = np.cumsum(np.isfinite(values).astype(np.int64))
        totals = cumulative[window - 1 :] - np.r_[0.0, cumulative[:-window]]
        valid = counts[window - 1 :] - np.r_[0, counts[:-window]]
        result[window - 1 :] = np.divide(
            totals, valid, out=np.full_like(totals, np.nan), where=valid > 0
        )
    return result


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
            start = end - sequence_length
            return (
                torch.from_numpy(item["features"][start:end]),
                torch.tensor(sample.instrument_index, dtype=torch.long),
                torch.tensor(sample.label, dtype=torch.float32),
            )

    return torch.utils.data.DataLoader(
        WindowDataset(),
        batch_size=batch_size,
        shuffle=shuffle,
        pin_memory=torch.cuda.is_available(),
    )


class _CausalFactorTransformer:
    """Placeholder declaration replaced with a torch module inside the worker."""

    def __new__(cls, torch: Any, **kwargs: Any) -> Any:
        class Model(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                model_dim = kwargs["model_dim"]
                self.input = torch.nn.Linear(kwargs["feature_count"], model_dim)
                self.position = torch.nn.Parameter(
                    torch.zeros(1, kwargs["sequence_length"], model_dim)
                )
                self.instrument = torch.nn.Embedding(kwargs["instrument_count"], model_dim)
                layer = torch.nn.TransformerEncoderLayer(
                    d_model=model_dim,
                    nhead=kwargs["heads"],
                    dim_feedforward=model_dim * 4,
                    dropout=0.1,
                    batch_first=True,
                    norm_first=True,
                    activation="gelu",
                )
                self.encoder = torch.nn.TransformerEncoder(layer, num_layers=kwargs["layers"])
                self.norm = torch.nn.LayerNorm(model_dim)
                self.direction = torch.nn.Linear(model_dim, kwargs["horizon_count"])
                self.returns = torch.nn.Linear(model_dim, kwargs["horizon_count"])

            def forward(self, features: Any, instrument_ids: Any) -> tuple[Any, Any]:
                state = self.input(features) + self.position[:, : features.shape[1]]
                state = state + self.instrument(instrument_ids)[:, None, :]
                state = self.norm(self.encoder(state)[:, -1])
                return self.direction(state), self.returns(state)

        return Model()


def _loss(
    model: Any, loader: Any, device: Any, torch: Any, config: DeepFactorConfig, loss_fn: Any
) -> float:
    model.eval()
    total = 0.0
    count = 0
    with torch.no_grad():
        for features, instrument_ids, labels in loader:
            logits, predicted_returns = model(features.to(device), instrument_ids.to(device))
            targets = (labels.to(device) > (config.fee_bps + config.slippage_bps) / 10_000).float()
            value = loss_fn(logits, targets) + 0.35 * torch.nn.functional.smooth_l1_loss(
                predicted_returns, labels.to(device)
            )
            total += float(value.cpu())
            count += 1
    return total / max(1, count)


def _evaluate_asset(
    model: Any,
    item: dict[str, Any],
    device: Any,
    torch: Any,
    np: Any,
    config: DeepFactorConfig,
    callback: DeepProgress | None,
    offset: int,
    total_assets: int,
) -> dict[str, Any]:
    model.eval()
    output: dict[str, Any] = {}
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
        probabilities: list[Any] = []
        labels: list[Any] = []
        with torch.no_grad():
            for features, _instrument_ids, batch_labels in loader:
                logits, _predicted_returns = model(
                    features.to(device),
                    torch.full(
                        (features.shape[0],),
                        item["instrument_index"],
                        dtype=torch.long,
                        device=device,
                    ),
                )
                probabilities.append(torch.sigmoid(logits).cpu().numpy())
                labels.append(batch_labels.numpy())
        prediction = (
            np.concatenate(probabilities) if probabilities else np.empty((0, len(config.horizons)))
        )
        actual = np.concatenate(labels) if labels else np.empty_like(prediction)
        split_start, split_end = split_periods(item["instrument"], item["bars"][-1].end_ms)[split]
        targets: list[int | None] = [None] * len(item["bars"])
        for sample, probability in zip(samples, prediction, strict=True):
            score = float(probability[1]) if len(config.horizons) > 1 else float(probability[0])
            targets[sample.end_index] = 1 if score > 0.55 else -1 if score < 0.45 else 0
        result = evaluate_targets(
            item["bars"],
            tuple(targets),
            start_ms=split_start,
            end_ms=split_end,
            funding=funding_by_bar(item["bars"], item["funding"]),
            fee_bps=Decimal(str(config.fee_bps)),
            slippage_bps=Decimal(str(config.slippage_bps)),
        )
        output[split] = {
            "samples": len(samples),
            "direction_accuracy": _accuracy(np, prediction[:, 0], actual[:, 0]),
            "information_coefficient": _correlation(np, prediction[:, 0], actual[:, 0]),
            "net_return": result.net_return,
            "max_drawdown": result.max_drawdown,
            "completed_trades": result.completed_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
        }
        _progress(
            callback,
            f"评估 {item['instrument']} {split}",
            0.68 + 0.28 * (offset + (split_index + 1) / 3) / total_assets,
        )
    return output


def _accuracy(np: Any, scores: Any, actual: Any) -> float | None:
    return float(np.mean((scores >= 0.5) == (actual > 0))) if len(scores) else None


def _correlation(np: Any, left: Any, right: Any) -> float | None:
    if len(left) < 2 or np.std(left) == 0 or np.std(right) == 0:
        return None
    return float(np.corrcoef(left, right)[0, 1])


def _candidate_gate(metrics: dict[str, Any]) -> bool:
    confirmations = [value["confirmation"] for value in metrics.values()]
    return bool(confirmations) and all(
        item["net_return"] > 0 and item["max_drawdown"] >= -0.25 and item["completed_trades"] >= 6
        for item in confirmations
    )


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


def _progress(callback: DeepProgress | None, stage: str, value: float) -> None:
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


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['id']}",
        "",
        "Research-only causal Transformer factor mining.",
        "",
        f"Decision: `{report['decision']['status']}`.",
        "",
        "| Instrument | Split | Return | Max DD | Trades | Accuracy | IC |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for instrument, metrics in report["metrics"].items():
        for split, row in metrics.items():
            lines.append(
                f"| {instrument} | {split} | {row['net_return']:.2%} | {row['max_drawdown']:.2%} | "
                f"{row['completed_trades']} | {row['direction_accuracy'] or 0:.2%} | "
                f"{row['information_coefficient'] or 0:.4f} |"
            )
    lines.extend(["", report["decision"]["reason"], ""])
    return "\n".join(lines)
