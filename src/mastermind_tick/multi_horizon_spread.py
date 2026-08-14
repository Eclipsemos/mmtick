"""Multi-horizon market aggregation and daily portfolio helpers for spread research."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from mastermind_tick.models import FundingRate
from mastermind_tick.volatility_spread import SpreadBar, SpreadExecution, SpreadFeatures

SOURCE_INTERVAL_MINUTES = 15
SOURCE_BAR_MS = SOURCE_INTERVAL_MINUTES * 60_000


@dataclass(frozen=True)
class AggregatedSpreadMarket:
    interval_minutes: int
    bars: list[SpreadBar]
    funding_by_bar: list[list[FundingRate]]
    executions: list[SpreadExecution | None]


def aggregate_spread_market(
    bars: list[SpreadBar],
    funding_by_bar: list[list[FundingRate]],
    executions: list[SpreadExecution | None],
    *,
    interval_minutes: int,
) -> AggregatedSpreadMarket:
    if len(bars) != len(funding_by_bar) or len(bars) != len(executions):
        raise ValueError("market input lengths differ")
    if interval_minutes < SOURCE_INTERVAL_MINUTES or interval_minutes % 15:
        raise ValueError("interval_minutes must be a multiple of 15")
    if not bars:
        raise ValueError("bars must not be empty")
    if any(bars[index].start_ms >= bars[index + 1].start_ms for index in range(len(bars) - 1)):
        raise ValueError("source bars must be strictly ordered")

    target_ms = interval_minutes * 60_000
    source_count = interval_minutes // SOURCE_INTERVAL_MINUTES
    grouped: dict[int, list[int]] = {}
    for index, bar in enumerate(bars):
        bucket_start = bar.start_ms // target_ms * target_ms
        grouped.setdefault(bucket_start, []).append(index)

    aggregated_bars: list[SpreadBar] = []
    aggregated_funding: list[list[FundingRate]] = []
    aggregated_executions: list[SpreadExecution | None] = []
    buckets = sorted(grouped)
    for bucket_position, bucket_start in enumerate(buckets):
        indices = grouped[bucket_start]
        expected_starts = [bucket_start + offset * SOURCE_BAR_MS for offset in range(source_count)]
        actual_starts = [bars[index].start_ms for index in indices]
        complete = (
            len(indices) == source_count
            and actual_starts == expected_starts
            and bars[indices[-1]].end_ms == bucket_start + target_ms - 1
        )
        if not complete:
            if bucket_position in {0, len(buckets) - 1}:
                continue
            raise ValueError(f"incomplete internal {interval_minutes}m bucket at {bucket_start}")
        components = [bars[index] for index in indices]
        aggregated_bars.append(
            SpreadBar(
                start_ms=bucket_start,
                end_ms=bucket_start + target_ms - 1,
                open=components[0].open,
                high=max(bar.high for bar in components),
                low=min(bar.low for bar in components),
                close=components[-1].close,
                volume=sum((bar.volume for bar in components), Decimal("0")),
            )
        )
        aggregated_funding.append(
            sorted(
                [event for index in indices for event in funding_by_bar[index]],
                key=lambda event: event.timestamp_ms,
            )
        )
        aggregated_executions.append(executions[indices[0]])

    if not aggregated_bars:
        raise ValueError(f"no complete {interval_minutes}m bars")
    return AggregatedSpreadMarket(
        interval_minutes=interval_minutes,
        bars=aggregated_bars,
        funding_by_bar=aggregated_funding,
        executions=aggregated_executions,
    )


def build_15m_state_filter(
    base_bars: list[SpreadBar],
    base_features: SpreadFeatures,
    aggregated_bars: list[SpreadBar],
    *,
    mode: str,
    minimum_ratio: float = 1.0,
) -> tuple[int | None, ...]:
    """Build a filter from the last closed 15m bar at each higher-horizon signal close."""
    valid_modes = {"none", "ratio", "direction_consensus", "consensus_ratio"}
    if mode not in valid_modes:
        raise ValueError(f"unknown 15m state filter: {mode}")
    if minimum_ratio <= 0:
        raise ValueError("minimum_ratio must be positive")
    if len(base_bars) != len(base_features.ratios):
        raise ValueError("base bars and features differ")
    if mode == "none":
        return tuple(None for _ in aggregated_bars)
    base_by_start = {bar.start_ms: index for index, bar in enumerate(base_bars)}
    result: list[int | None] = []
    for aggregated in aggregated_bars:
        last_15m_start = aggregated.end_ms - SOURCE_BAR_MS + 1
        index = base_by_start.get(last_15m_start)
        if index is None:
            result.append(0)
            continue
        ratio = base_features.ratios[index]
        ratio_passed = ratio is not None and ratio >= minimum_ratio
        if mode == "ratio":
            result.append(None if ratio_passed else 0)
            continue
        if not ratio_passed and mode == "consensus_ratio":
            result.append(0)
            continue
        prior_high = base_features.prior_highs[index]
        prior_low = base_features.prior_lows[index]
        close = base_bars[index].close
        if prior_high is not None and close > prior_high:
            result.append(1)
        elif prior_low is not None and close < prior_low:
            result.append(-1)
        else:
            result.append(0)
    return tuple(result)


def inverse_volatility_weights(paths: dict[str, list[tuple[str, float]]]) -> dict[str, float]:
    if not paths:
        raise ValueError("paths must not be empty")
    dates = _aligned_dates(paths)
    inverse: dict[str, float] = {}
    for name, path in paths.items():
        values = [value for _, value in path]
        mean = sum(values) / len(dates)
        variance = sum((value - mean) ** 2 for value in values) / len(dates)
        volatility = math.sqrt(variance)
        if volatility <= 0:
            raise ValueError(f"{name} has zero daily volatility")
        inverse[name] = 1 / volatility
    total = sum(inverse.values())
    return {name: value / total for name, value in inverse.items()}


def combine_daily_paths(
    paths: dict[str, list[tuple[str, float]]],
    weights: dict[str, float],
    *,
    scale: float = 1.0,
) -> list[tuple[str, float]]:
    if scale <= 0:
        raise ValueError("scale must be positive")
    dates = _aligned_dates(paths)
    if set(paths) != set(weights):
        raise ValueError("path and weight names differ")
    if any(value < 0 for value in weights.values()):
        raise ValueError("weights must not be negative")
    if not math.isclose(sum(weights.values()), 1.0, rel_tol=0, abs_tol=1e-12):
        raise ValueError("weights must sum to one")
    mappings = {name: dict(path) for name, path in paths.items()}
    return [
        (
            day,
            scale * sum(weights[name] * mappings[name][day] for name in sorted(paths)),
        )
        for day in dates
    ]


def _aligned_dates(paths: dict[str, list[tuple[str, float]]]) -> list[str]:
    if not paths or any(not path for path in paths.values()):
        raise ValueError("every path must contain daily returns")
    first_dates = [day for day, _ in next(iter(paths.values()))]
    if any([day for day, _ in path] != first_dates for path in paths.values()):
        raise ValueError("daily paths are not aligned")
    return first_dates
