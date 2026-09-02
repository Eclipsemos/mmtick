"""Causal weekly simple-moving-average signal primitives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mastermind_tick.bar_research import ResearchBar

SMA_PERIODS = (10, 20, 30, 40)


def aggregate_complete_utc_weeks(
    bars: list[ResearchBar],
) -> tuple[list[ResearchBar], tuple[int, ...]]:
    """Aggregate complete Monday-Sunday UTC weeks and return source end indices."""
    if not bars:
        return [], ()
    expected_step = 15 * 60_000
    expected_count = 7 * 24 * 4
    groups: dict[int, list[tuple[int, ResearchBar]]] = {}
    for index, bar in enumerate(bars):
        moment = datetime.fromtimestamp(bar.start_ms / 1000, UTC)
        monday = (moment - timedelta(days=moment.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        groups.setdefault(int(monday.timestamp() * 1000), []).append((index, bar))
    result: list[ResearchBar] = []
    source_end_indices: list[int] = []
    for monday_ms, group in sorted(groups.items()):
        if len(group) != expected_count:
            continue
        items = [bar for _, bar in group]
        if items[0].start_ms != monday_ms:
            continue
        if items[-1].end_ms != monday_ms + 7 * 86_400_000 - 1:
            continue
        if any(
            right.start_ms - left.start_ms != expected_step
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


def map_weekly_targets_to_source(
    source_count: int,
    weekly_targets: tuple[int | None, ...],
    source_end_indices: tuple[int, ...],
) -> tuple[int | None, ...]:
    """Place each completed-week signal on its final source bar."""
    if len(weekly_targets) != len(source_end_indices):
        raise ValueError("weekly target and source-index lengths differ")
    targets: list[int | None] = [None] * source_count
    for target, index in zip(weekly_targets, source_end_indices, strict=True):
        if not 0 <= index < source_count:
            raise ValueError("weekly source index is out of range")
        targets[index] = target
    return tuple(targets)


def simple_moving_average(bars: list[ResearchBar], period: int) -> tuple[Decimal | None, ...]:
    """Return a close-based SMA, available only after ``period`` completed bars."""
    if period < 1:
        raise ValueError("SMA period must be positive")
    values: list[Decimal | None] = [None] * len(bars)
    running = Decimal("0")
    for index, bar in enumerate(bars):
        running += bar.close
        if index >= period:
            running -= bars[index - period].close
        if index + 1 >= period:
            values[index] = running / Decimal(period)
    return tuple(values)


def weekly_sma_targets(
    bars: list[ResearchBar],
    periods: tuple[int, int, int, int] = SMA_PERIODS,
    *,
    direction: str = "long_only",
    require_slope: bool = False,
    require_price_confirmation: bool = False,
) -> tuple[int | None, ...]:
    """Return causal weekly trend targets from four ordered SMAs.

    Targets are calculated on each completed weekly close. Consumers should execute a
    changed target at the next bar open. A bullish state is fast-to-slow ordering; a
    bearish state is the reverse. Optional confirmations use only the current and prior
    completed weekly values.
    """
    _validate_direction(direction)
    if len(periods) != 4 or any(period < 1 for period in periods):
        raise ValueError("exactly four positive SMA periods are required")
    if tuple(sorted(periods)) != periods or len(set(periods)) != len(periods):
        raise ValueError("SMA periods must be strictly increasing")
    series = tuple(simple_moving_average(bars, period) for period in periods)
    warmup = max(periods)
    targets: list[int | None] = []
    for index in range(len(bars)):
        current = [values[index] for values in series]
        if index + 1 < warmup or any(value is None for value in current):
            targets.append(None)
            continue
        current_values = tuple(value for value in current if value is not None)
        bullish = all(
            left > right for left, right in zip(current_values, current_values[1:], strict=False)
        )
        bearish = all(
            left < right for left, right in zip(current_values, current_values[1:], strict=False)
        )
        if require_slope:
            previous = [values[index - 1] for values in series]
            if any(value is None for value in previous):
                bullish = bearish = False
            else:
                previous_values = tuple(value for value in previous if value is not None)
                bullish = bullish and all(
                    current_value > previous_value
                    for current_value, previous_value in zip(
                        current_values, previous_values, strict=True
                    )
                )
                bearish = bearish and all(
                    current_value < previous_value
                    for current_value, previous_value in zip(
                        current_values, previous_values, strict=True
                    )
                )
        if require_price_confirmation:
            bullish = bullish and bars[index].close > max(current_values)
            bearish = bearish and bars[index].close < min(current_values)
        if bullish:
            targets.append(1)
        elif direction == "long_short" and bearish:
            targets.append(-1)
        else:
            targets.append(0)
    return tuple(targets)


def _validate_direction(direction: str) -> None:
    if direction not in {"long_only", "long_short"}:
        raise ValueError("direction must be long_only or long_short")
