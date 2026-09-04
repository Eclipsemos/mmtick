"""Causal multi-timeframe four-SMA trend signals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.sma_weekly import simple_moving_average

TIMEFRAME_MINUTES = {
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1_440,
    "3d": 4_320,
    "1w": 10_080,
}


def aggregate_complete_periods(
    bars: list[ResearchBar], timeframe: str
) -> tuple[list[ResearchBar], tuple[int, ...]]:
    """Aggregate complete UTC periods and return each period's source end index.

    A period is accepted only when every expected 15-minute bar is present.  Weekly
    periods are Monday through Sunday; all other periods use UTC calendar/fixed buckets.
    """
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    if not bars:
        return [], ()
    minutes = TIMEFRAME_MINUTES[timeframe]
    expected_count = minutes // 15
    groups: dict[int, list[tuple[int, ResearchBar]]] = {}
    for index, bar in enumerate(bars):
        moment = datetime.fromtimestamp(bar.start_ms / 1000, UTC)
        if timeframe == "1w":
            start = (moment - timedelta(days=moment.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            bucket = int(start.timestamp() * 1000)
        elif timeframe in {"1d", "3d"}:
            day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
            if timeframe == "3d":
                day_start -= timedelta(days=day_start.toordinal() % 3)
            bucket = int(day_start.timestamp() * 1000)
        else:
            bucket_ms = minutes * 60_000
            bucket = bar.start_ms // bucket_ms * bucket_ms
        groups.setdefault(bucket, []).append((index, bar))

    result: list[ResearchBar] = []
    source_end_indices: list[int] = []
    period_ms = minutes * 60_000
    for bucket, group in sorted(groups.items()):
        items = [bar for _, bar in group]
        if len(items) != expected_count or items[0].start_ms != bucket:
            continue
        if items[-1].end_ms != bucket + period_ms - 1:
            continue
        if any(
            right.start_ms - left.start_ms != 15 * 60_000
            for left, right in zip(items, items[1:], strict=False)
        ):
            continue
        result.append(
            ResearchBar(
                start_ms=items[0].start_ms,
                end_ms=items[-1].end_ms,
                open=items[0].open,
                high=max(item.high for item in items),
                low=min(item.low for item in items),
                close=items[-1].close,
                volume=sum((item.volume for item in items), Decimal("0")),
            )
        )
        source_end_indices.append(group[-1][0])
    return result, tuple(source_end_indices)


def map_targets_to_source(
    source_count: int, period_targets: tuple[int | None, ...], source_end_indices: tuple[int, ...]
) -> tuple[int | None, ...]:
    """Place completed-period targets on the final source bar of that period."""
    if len(period_targets) != len(source_end_indices):
        raise ValueError("target and source-index lengths differ")
    targets: list[int | None] = [None] * source_count
    for target, index in zip(period_targets, source_end_indices, strict=True):
        if not 0 <= index < source_count:
            raise ValueError("source index is out of range")
        targets[index] = target
    return tuple(targets)


def four_sma_targets(
    bars: list[ResearchBar],
    periods: tuple[int, int, int, int],
    *,
    direction: str = "long_only",
    require_price_confirmation: bool = False,
) -> tuple[int | None, ...]:
    """Return causal targets from strictly ordered fast-to-slow SMAs."""
    if direction not in {"long_only", "long_short"}:
        raise ValueError("direction must be long_only or long_short")
    if len(periods) != 4 or tuple(sorted(periods)) != periods or len(set(periods)) != 4:
        raise ValueError("periods must contain four strictly increasing values")
    if any(period < 1 for period in periods):
        raise ValueError("SMA periods must be positive")
    series = tuple(simple_moving_average(bars, period) for period in periods)
    targets: list[int | None] = []
    for index in range(len(bars)):
        values = tuple(stream[index] for stream in series)
        if any(value is None for value in values):
            targets.append(None)
            continue
        bullish = all(left > right for left, right in zip(values, values[1:], strict=False))
        bearish = all(left < right for left, right in zip(values, values[1:], strict=False))
        if require_price_confirmation:
            bullish = bullish and bars[index].close > max(values)
            bearish = bearish and bars[index].close < min(values)
        targets.append(1 if bullish else -1 if direction == "long_short" and bearish else 0)
    return tuple(targets)
