"""Research-only single-factor stability tests across market regimes."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import ResearchBar, aggregate_bars, funding_by_bar
from mastermind_tick.continuous_factor import (
    FEATURE_NAMES,
    cross_asset_features,
    forward_open_returns,
)
from mastermind_tick.factor_mining import load_market
from mastermind_tick.market_metrics import (
    METRIC_FEATURES,
    causal_metric_features,
    load_metric_archives,
)

ASSETS = ("btc_perp", "eth_perp")
MILLISECONDS_PER_SECOND = 1_000


@dataclass(frozen=True)
class FactorStabilityConfig:
    """Frozen protocol for single-factor walk-forward evaluation."""

    interval_minutes: int = 240
    horizons: tuple[int, ...] = (1, 3, 6, 18)
    first_test_year: int = 2022
    development_end_year: int = 2025
    confirmation_year: int = 2026
    fee_bps_per_fill: float = 5.0
    slippage_bps_per_fill: float = 2.0
    metric_normalization_window: int = 180
    trend_window: int = 180
    volatility_window: int = 180
    volatility_history: int = 1080
    trend_strength_threshold: float = 0.5
    minimum_fold_samples: int = 100
    minimum_regime_samples: int = 100
    minimum_cost_ic: float = 0.02
    minimum_positive_fold_rate: float = 0.75
    minimum_positive_regime_rate: float = 2 / 3
    worst_allowed_ic: float = -0.02
    minimum_confirmation_retention: float = 0.5
    familywise_alpha: float = 0.05

    def __post_init__(self) -> None:
        if self.interval_minutes < 15 or self.interval_minutes % 15:
            raise ValueError("factor stability interval must be a positive 15m multiple")
        if not self.horizons or any(value < 1 for value in self.horizons):
            raise ValueError("factor stability horizons must be positive")
        if not self.first_test_year <= self.development_end_year < self.confirmation_year:
            raise ValueError("factor stability years are inconsistent")
        if self.metric_normalization_window < 12:
            raise ValueError("metric normalization window must be at least 12")
        if not 0 < self.minimum_confirmation_retention <= 1:
            raise ValueError("confirmation IC retention must be in (0, 1]")


def run_factor_stability_study(
    database: Path,
    metric_root: Path,
    config: FactorStabilityConfig | None = None,
) -> dict[str, Any]:
    """Evaluate individual BTC/ETH factors without fitting a multivariate model."""
    config = config or FactorStabilityConfig()
    np = _numpy()
    loaded = {asset: load_market(database, asset) for asset in ASSETS}
    bars = {asset: aggregate_bars(loaded[asset][0], config.interval_minutes) for asset in ASSETS}
    _require_aligned(bars)
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in ASSETS}
    features = _factor_universe(np, bars, funding, metric_root, config)
    regimes = {asset: causal_market_regimes(np, bars[asset], config) for asset in ASSETS}
    candidate_count = sum(len(values) * len(config.horizons) for values in features.values())
    results: dict[str, Any] = {}
    for asset in ASSETS:
        rows = []
        labels = {
            horizon: cost_aware_forward_returns(
                np,
                bars[asset],
                funding[asset],
                horizon,
                config.fee_bps_per_fill,
                config.slippage_bps_per_fill,
            )
            for horizon in config.horizons
        }
        for factor_name, values in features[asset].items():
            for horizon in config.horizons:
                rows.append(
                    evaluate_factor_walk_forward(
                        np,
                        bars[asset],
                        values,
                        labels[horizon]["raw"],
                        labels[horizon]["cost_adjusted"],
                        regimes[asset],
                        factor_name,
                        horizon,
                        candidate_count,
                        config,
                    )
                )
        _apply_cross_horizon_gate(rows, config)
        eligible = [row for row in rows if all(row["development_gates"].values())]
        ranked = sorted(eligible or rows, key=_candidate_score, reverse=True)
        selected = ranked[0] if ranked else None
        confirmation_gates = _confirmation_gates(selected, config)
        confirmation_passed = bool(confirmation_gates and all(confirmation_gates.values()))
        results[asset] = {
            "factor_count": len(features[asset]),
            "candidate_count": len(rows),
            "development_eligible_count": len(eligible),
            "selected": selected,
            "confirmation_gates": confirmation_gates,
            "top_candidates": ranked[:20],
            "all_candidates": rows,
            "decision": {
                "status": (
                    "awaiting_fresh_forward_data"
                    if eligible and confirmation_passed
                    else "rejected_after_reused_confirmation"
                    if eligible
                    else "rejected_in_development"
                ),
                "approved_for_trading": False,
                "reason": (
                    "The development-stable factor retained at least half of its IC in reused "
                    "2026 data; it still requires a new untouched month before combination."
                    if eligible and confirmation_passed
                    else (
                        "The development-stable factor did not retain the required IC strength "
                        "in reused 2026 confirmation."
                    )
                    if eligible
                    else "No individual factor passed the predeclared walk-forward stability gates."
                ),
            },
        }
    generated_at = datetime.now(UTC)
    return {
        "schema_version": 1,
        "id": f"factor-stability-{generated_at.strftime('%Y%m%d-%H%M%S-%f')}",
        "generated_at": generated_at.isoformat(),
        "scope": "research_only_single_factor_stability",
        "protocol": {
            **asdict(config),
            "round_trip_cost_bps": 2 * (config.fee_bps_per_fill + config.slippage_bps_per_fill),
            "orientation": (
                "For each test year, factor polarity is selected only from observations whose "
                "label endpoint precedes that year."
            ),
            "sampling": (
                "Forward labels are sampled every horizon bars within each year to avoid "
                "overlapping-return inflation."
            ),
            "confirmation_warning": (
                "2026 has been reused by prior studies and is not a fresh holdout."
            ),
            "multiple_testing": "One-sided normal IC p-values with Bonferroni familywise control.",
        },
        "data": {
            asset: {
                "first_bar": _timestamp(bars[asset][0].start_ms),
                "last_bar": _timestamp(bars[asset][-1].end_ms),
                "bars": len(bars[asset]),
                "interval_minutes": config.interval_minutes,
            }
            for asset in ASSETS
        },
        "results": results,
        "decision": {
            "transformer_combination_allowed": False,
            "reason": (
                "A deep factor combination remains blocked until a development-stable single "
                "factor retains the required IC strength in a genuinely new forward month."
            ),
        },
    }


def cost_aware_forward_returns(
    np: Any,
    bars: list[ResearchBar],
    funding: list[list[Any]],
    horizon: int,
    fee_bps_per_fill: float,
    slippage_bps_per_fill: float,
) -> dict[str, Any]:
    """Build next-open labels after round-trip costs and realized funding."""
    if len(bars) != len(funding):
        raise ValueError("bars and funding must be aligned")
    raw = forward_open_returns(np, bars, horizon)
    adjusted = np.full(len(bars), np.nan, dtype=np.float64)
    round_trip_cost = 2.0 * (fee_bps_per_fill + slippage_bps_per_fill) / 10_000.0
    funding_rates = np.array(
        [float(sum((event.rate for event in events), Decimal("0"))) for events in funding],
        dtype=np.float64,
    )
    for index, value in enumerate(raw):
        if not np.isfinite(value) or index + horizon >= len(bars):
            continue
        realized_funding = float(np.sum(funding_rates[index + 1 : index + horizon + 1]))
        long_edge = value - round_trip_cost - realized_funding
        short_edge = -value - round_trip_cost + realized_funding
        if long_edge >= max(short_edge, 0.0):
            adjusted[index] = long_edge
        elif short_edge > 0:
            adjusted[index] = -short_edge
        else:
            adjusted[index] = 0.0
    return {"raw": raw, "cost_adjusted": adjusted}


def causal_market_regimes(
    np: Any,
    bars: list[ResearchBar],
    config: FactorStabilityConfig,
) -> list[str | None]:
    """Classify trend and volatility using trailing data available at each bar close."""
    closes = np.array([float(bar.close) for bar in bars], dtype=np.float64)
    returns = np.full(len(bars), np.nan, dtype=np.float64)
    returns[1:] = closes[1:] / closes[:-1] - 1.0
    volatility = _rolling_std(np, returns, config.volatility_window)
    result: list[str | None] = []
    warmup = max(config.trend_window, config.volatility_window)
    for index in range(len(bars)):
        history_start = max(0, index - config.volatility_history)
        history = volatility[history_start:index]
        history = history[np.isfinite(history)]
        if index < warmup or len(history) < config.volatility_window:
            result.append(None)
            continue
        denominator = volatility[index] * math.sqrt(config.trend_window)
        if not np.isfinite(denominator) or denominator <= 1e-12:
            result.append(None)
            continue
        trend_strength = math.log(closes[index] / closes[index - config.trend_window]) / denominator
        trend = (
            "bull"
            if trend_strength > config.trend_strength_threshold
            else "bear"
            if trend_strength < -config.trend_strength_threshold
            else "sideways"
        )
        vol_state = "high_vol" if volatility[index] > float(np.median(history)) else "low_vol"
        result.append(f"{trend}_{vol_state}")
    return result


def evaluate_factor_walk_forward(
    np: Any,
    bars: list[ResearchBar],
    values: Any,
    raw_labels: Any,
    cost_labels: Any,
    regimes: list[str | None],
    factor_name: str,
    horizon: int,
    candidate_count: int,
    config: FactorStabilityConfig,
) -> dict[str, Any]:
    """Apply expanding yearly polarity selection with an embargo at every boundary."""
    if not (len(bars) == len(values) == len(raw_labels) == len(cost_labels) == len(regimes)):
        raise ValueError("factor walk-forward inputs must be aligned")
    years = np.array([datetime.fromtimestamp(bar.start_ms / 1000, UTC).year for bar in bars])
    folds = []
    development_records: list[tuple[float, float, float, str]] = []
    confirmation_records: list[tuple[float, float, float, str]] = []
    for year in range(config.first_test_year, config.confirmation_year + 1):
        test_positions = np.flatnonzero(years == year)
        if not len(test_positions):
            continue
        test_start = int(test_positions[0])
        train_indices = [
            index
            for index in range(test_start)
            if index + horizon + 1 < test_start
            and np.isfinite(values[index])
            and np.isfinite(cost_labels[index])
        ]
        test_indices = [
            int(index)
            for index in test_positions[::horizon]
            if index + horizon + 1 < len(bars)
            and years[index + horizon + 1] == year
            and regimes[index] is not None
            and np.isfinite(values[index])
            and np.isfinite(raw_labels[index])
            and np.isfinite(cost_labels[index])
        ]
        train_ic = spearman_ic(
            [float(values[index]) for index in train_indices],
            [float(cost_labels[index]) for index in train_indices],
        )
        orientation = 1 if train_ic is None or train_ic >= 0 else -1
        oriented = [orientation * float(values[index]) for index in test_indices]
        raw = [float(raw_labels[index]) for index in test_indices]
        adjusted = [float(cost_labels[index]) for index in test_indices]
        percentile_scores = percentile_ranks(oriented)
        records = [
            (score, raw_value, adjusted_value, str(regimes[index]))
            for score, raw_value, adjusted_value, index in zip(
                percentile_scores, raw, adjusted, test_indices, strict=True
            )
        ]
        fold = {
            "year": year,
            "train_samples": len(train_indices),
            "test_samples": len(test_indices),
            "training_ic": train_ic,
            "orientation": orientation,
            "raw_ic": spearman_ic(oriented, raw),
            "cost_adjusted_ic": spearman_ic(oriented, adjusted),
        }
        folds.append(fold)
        if year <= config.development_end_year:
            development_records.extend(records)
        elif year == config.confirmation_year:
            confirmation_records.extend(records)
    development = _record_summary(development_records)
    confirmation = _record_summary(confirmation_records)
    regime_metrics = _regime_summaries(development_records, config.minimum_regime_samples)
    eligible_regimes = [
        value
        for value in regime_metrics.values()
        if value["eligible"] and value["cost_adjusted_ic"] is not None
    ]
    development_folds = [
        fold
        for fold in folds
        if fold["year"] <= config.development_end_year
        and fold["test_samples"] >= config.minimum_fold_samples
        and fold["cost_adjusted_ic"] is not None
    ]
    fold_values = [float(fold["cost_adjusted_ic"]) for fold in development_folds]
    positive_fold_rate = (
        sum(value > 0 for value in fold_values) / len(fold_values) if fold_values else 0.0
    )
    positive_regime_rate = (
        sum(float(value["cost_adjusted_ic"]) > 0 for value in eligible_regimes)
        / len(eligible_regimes)
        if eligible_regimes
        else 0.0
    )
    adjusted_ic = development["cost_adjusted_ic"]
    p_value = normal_ic_p_value(adjusted_ic, development["samples"])
    gates = {
        "cost_adjusted_ic": bool(adjusted_ic is not None and adjusted_ic >= config.minimum_cost_ic),
        "bonferroni_significance": bool(
            p_value is not None and p_value * candidate_count <= config.familywise_alpha
        ),
        "annual_consistency": bool(
            len(development_folds) == config.development_end_year - config.first_test_year + 1
            and positive_fold_rate >= config.minimum_positive_fold_rate
        ),
        "annual_no_sign_reversal": bool(
            fold_values and min(fold_values) >= config.worst_allowed_ic
        ),
        "regime_coverage": len(eligible_regimes) >= 4,
        "regime_consistency": bool(
            eligible_regimes and positive_regime_rate >= config.minimum_positive_regime_rate
        ),
        "regime_no_sign_reversal": bool(
            eligible_regimes
            and min(float(value["cost_adjusted_ic"]) for value in eligible_regimes)
            >= config.worst_allowed_ic
        ),
        "cross_horizon_support": False,
    }
    return {
        "id": f"{factor_name}-h{horizon}",
        "factor": factor_name,
        "horizon_bars": horizon,
        "development": development,
        "confirmation": confirmation,
        "folds": folds,
        "regimes": regime_metrics,
        "positive_fold_rate": positive_fold_rate,
        "worst_development_fold_ic": min(fold_values) if fold_values else None,
        "positive_regime_rate": positive_regime_rate,
        "one_sided_p_value": p_value,
        "bonferroni_p_value": min(1.0, p_value * candidate_count) if p_value is not None else None,
        "development_gates": gates,
    }


def spearman_ic(left: list[float], right: list[float]) -> float | None:
    """Return Spearman rank correlation with average ranks for ties."""
    if len(left) != len(right):
        raise ValueError("IC inputs must have equal lengths")
    if len(left) < 3:
        return None
    left_rank = percentile_ranks(left)
    right_rank = percentile_ranks(right)
    left_mean = sum(left_rank) / len(left_rank)
    right_mean = sum(right_rank) / len(right_rank)
    covariance = sum(
        (first - left_mean) * (second - right_mean)
        for first, second in zip(left_rank, right_rank, strict=True)
    )
    left_variance = sum((value - left_mean) ** 2 for value in left_rank)
    right_variance = sum((value - right_mean) ** 2 for value in right_rank)
    denominator = math.sqrt(left_variance * right_variance)
    return covariance / denominator if denominator > 1e-15 else None


def percentile_ranks(values: list[float]) -> list[float]:
    """Rank values into [0, 1], assigning tied values their average rank."""
    if not values:
        return []
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        rank = (cursor + end - 1) / 2
        for position in order[cursor:end]:
            ranks[position] = rank
        cursor = end
    denominator = max(1, len(values) - 1)
    return [rank / denominator for rank in ranks]


def normal_ic_p_value(ic: float | None, samples: int) -> float | None:
    """Approximate one-sided positive-correlation p-value on non-overlapping samples."""
    if ic is None or samples <= 3:
        return None
    bounded = max(-0.999999, min(0.999999, ic))
    z_score = math.atanh(bounded) * math.sqrt(samples - 3)
    return 0.5 * math.erfc(z_score / math.sqrt(2))


def write_factor_stability_report(report: dict[str, Any], output_root: Path) -> tuple[Path, Path]:
    """Persist the full audit JSON and a concise Markdown decision report."""
    generated = datetime.fromisoformat(report["generated_at"])
    output_dir = output_root / generated.date().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report['id']}.json"
    markdown_path = output_dir / f"{report['id']}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_factor_stability_markdown(report), encoding="utf-8")
    readme = output_dir / "README.md"
    readme.write_text(render_factor_stability_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def render_factor_stability_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Single-Factor Regime Stability",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "This study changes the research question from maximizing backtest return to proving",
        "that an individual causal factor retains out-of-sample IC across years and market states.",
        "It is research-only and cannot create orders.",
        "",
        "## Protocol",
        "",
        "- Aligned BTCUSDT and ETHUSDT 4h bars; horizons: 1, 3, 6, and 18 bars.",
        "- Expanding yearly walk-forward polarity; forward-label embargo at every year boundary.",
        "- Non-overlapping horizon samples for IC and Bonferroni familywise significance control.",
        "- Regimes: causal bull/bear/sideways crossed with high/low trailing volatility.",
        (
            f"- Cost-aware labels include {report['protocol']['round_trip_cost_bps']:g} bps "
            "round-trip fees/slippage and realized funding."
        ),
        "- 2026 is reused confirmation, not a fresh holdout.",
        "",
        "## Results",
        "",
        (
            "| Asset | Factors | Candidates | Development eligible | Selected | "
            "Development net IC | 2026 net IC | Decision |"
        ),
        "|---|---:|---:|---:|---|---:|---:|---|",
    ]
    for asset, result in report["results"].items():
        selected = result["selected"]
        lines.append(
            f"| {asset} | {result['factor_count']} | {result['candidate_count']} | "
            f"{result['development_eligible_count']} | `{selected['id'] if selected else '-'}` | "
            f"{_format_ic(selected['development']['cost_adjusted_ic'] if selected else None)} | "
            f"{_format_ic(selected['confirmation']['cost_adjusted_ic'] if selected else None)} | "
            f"`{result['decision']['status']}` |"
        )
    for asset, result in report["results"].items():
        lines.extend(["", f"## {asset} Development Ranking", ""])
        lines.extend(
            [
                (
                    "| Rank | Factor | Horizon | Net IC | Positive years | Positive regimes | "
                    "Adjusted p | All gates |"
                ),
                "|---:|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for rank, row in enumerate(result["top_candidates"][:10], start=1):
            lines.append(
                f"| {rank} | `{row['factor']}` | {row['horizon_bars']} | "
                f"{_format_ic(row['development']['cost_adjusted_ic'])} | "
                f"{row['positive_fold_rate']:.0%} | {row['positive_regime_rate']:.0%} | "
                f"{_format_p(row['bonferroni_p_value'])} | "
                f"{'yes' if all(row['development_gates'].values()) else 'no'} |"
            )
        selected = result["selected"]
        if selected:
            lines.extend(["", "Selected-candidate gate audit:", ""])
            for gate, passed in selected["development_gates"].items():
                lines.append(f"- `{gate}`: {'pass' if passed else 'fail'}")
            lines.extend(["", "Reused-confirmation gate audit:", ""])
            for gate, passed in result["confirmation_gates"].items():
                lines.append(f"- `{gate}`: {'pass' if passed else 'fail'}")
            lines.extend(["", "Yearly walk-forward IC:", ""])
            for fold in selected["folds"]:
                lines.append(
                    f"- {fold['year']}: net IC {_format_ic(fold['cost_adjusted_ic'])}, "
                    f"samples {fold['test_samples']}, training polarity {fold['orientation']:+d}."
                )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            (
                "Transformer combination allowed: "
                f"`{str(report['decision']['transformer_combination_allowed']).lower()}`."
            ),
            "",
            report["decision"]["reason"],
            "",
        ]
    )
    return "\n".join(lines)


def _factor_universe(
    np: Any,
    bars: dict[str, list[ResearchBar]],
    funding: dict[str, list[list[Any]]],
    metric_root: Path,
    config: FactorStabilityConfig,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for asset, other in (("btc_perp", "eth_perp"), ("eth_perp", "btc_perp")):
        matrix = cross_asset_features(np, bars[asset], bars[other], funding[asset], funding[other])
        factors = {name: matrix[:, index] for index, name in enumerate(FEATURE_NAMES)}
        symbol = "BTCUSDT" if asset == "btc_perp" else "ETHUSDT"
        metric_values = causal_metric_features(
            bars[asset],
            load_metric_archives(metric_root, symbol),
            normalization_window=config.metric_normalization_window,
        )
        factors.update(
            {
                f"metric_{name}": np.array(
                    [
                        float(value) if value is not None else np.nan
                        for value in metric_values[name]
                    ],
                    dtype=np.float64,
                )
                for name in METRIC_FEATURES
            }
        )
        result[asset] = factors
    return result


def _record_summary(records: list[tuple[float, float, float, str]]) -> dict[str, Any]:
    return {
        "samples": len(records),
        "raw_ic": spearman_ic([record[0] for record in records], [record[1] for record in records]),
        "cost_adjusted_ic": spearman_ic(
            [record[0] for record in records], [record[2] for record in records]
        ),
    }


def _regime_summaries(
    records: list[tuple[float, float, float, str]], minimum_samples: int
) -> dict[str, Any]:
    names = (
        "bull_high_vol",
        "bull_low_vol",
        "bear_high_vol",
        "bear_low_vol",
        "sideways_high_vol",
        "sideways_low_vol",
    )
    result = {}
    for name in names:
        subset = [record for record in records if record[3] == name]
        result[name] = {
            **_record_summary(subset),
            "eligible": len(subset) >= minimum_samples,
        }
    return result


def _apply_cross_horizon_gate(rows: list[dict[str, Any]], config: FactorStabilityConfig) -> None:
    by_factor: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_factor.setdefault(row["factor"], []).append(row)
    for factor_rows in by_factor.values():
        support = sum(
            row["development"]["cost_adjusted_ic"] is not None
            and row["development"]["cost_adjusted_ic"] > 0
            for row in factor_rows
        )
        passed = support >= min(2, len(config.horizons))
        for row in factor_rows:
            row["development_gates"]["cross_horizon_support"] = passed


def _confirmation_gates(
    selected: dict[str, Any] | None, config: FactorStabilityConfig
) -> dict[str, bool]:
    if selected is None:
        return {}
    development_ic = selected["development"]["cost_adjusted_ic"]
    confirmation_ic = selected["confirmation"]["cost_adjusted_ic"]
    return {
        "minimum_confirmation_ic": bool(
            confirmation_ic is not None and confirmation_ic >= config.minimum_cost_ic
        ),
        "minimum_ic_retention": bool(
            development_ic is not None
            and confirmation_ic is not None
            and confirmation_ic >= development_ic * config.minimum_confirmation_retention
        ),
    }


def _candidate_score(row: dict[str, Any]) -> tuple[Any, ...]:
    development_ic = row["development"]["cost_adjusted_ic"]
    return (
        sum(row["development_gates"].values()),
        row["positive_fold_rate"],
        row["worst_development_fold_ic"] if row["worst_development_fold_ic"] is not None else -1.0,
        development_ic if development_ic is not None else -1.0,
    )


def _rolling_std(np: Any, values: Any, window: int) -> Any:
    result = np.full(len(values), np.nan, dtype=np.float64)
    for index in range(window - 1, len(values)):
        sample = values[index - window + 1 : index + 1]
        sample = sample[np.isfinite(sample)]
        if len(sample) == window:
            result[index] = float(np.std(sample))
    return result


def _require_aligned(bars: dict[str, list[ResearchBar]]) -> None:
    if not bars[ASSETS[0]] or len(bars[ASSETS[0]]) != len(bars[ASSETS[1]]):
        raise ValueError("BTC and ETH factor stability bars are empty or unaligned")
    if any(
        left.start_ms != right.start_ms
        for left, right in zip(bars[ASSETS[0]], bars[ASSETS[1]], strict=True)
    ):
        raise ValueError("BTC and ETH factor stability bars are not timestamp-aligned")


def _numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("factor stability research requires numpy") from exc
    return np


def _timestamp(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / MILLISECONDS_PER_SECOND, UTC).isoformat()


def _format_ic(value: float | None) -> str:
    return "-" if value is None else f"{value:+.4f}"


def _format_p(value: float | None) -> str:
    return "-" if value is None else f"{value:.3g}"
