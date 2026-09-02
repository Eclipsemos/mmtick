from datetime import UTC, datetime
from decimal import Decimal

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.sma_weekly import (
    aggregate_complete_utc_weeks,
    map_weekly_targets_to_source,
    simple_moving_average,
    weekly_sma_targets,
)


def _bar(index: int, close: int) -> ResearchBar:
    value = Decimal(close)
    return ResearchBar(
        start_ms=index * 60_000,
        end_ms=index * 60_000 + 59_999,
        open=value,
        high=value,
        low=value,
        close=value,
    )


def test_sma_has_warmup_and_is_causal() -> None:
    original = simple_moving_average([_bar(i, i + 1) for i in range(3)], 2)
    extended = simple_moving_average([_bar(i, i + 1) for i in range(4)], 2)

    assert original == (None, Decimal("1.5"), Decimal("2.5"))
    assert extended[:3] == original


def test_weekly_sma_bullish_alignment_and_next_target() -> None:
    bars = [_bar(i, 100 + i) for i in range(40)]

    targets = weekly_sma_targets(bars, periods=(2, 3, 4, 5))

    assert all(value is None for value in targets[:4])
    assert targets[-1] == 1


def test_weekly_sma_direction_and_optional_confirmations() -> None:
    bars = [_bar(i, 100 + i) for i in range(12)] + [_bar(i, 111 - i) for i in range(12, 24)]

    long_only = weekly_sma_targets(bars, periods=(2, 3, 4, 5), direction="long_only")
    long_short = weekly_sma_targets(bars, periods=(2, 3, 4, 5), direction="long_short")

    assert 1 in long_only
    assert -1 in long_short
    with pytest.raises(ValueError):
        weekly_sma_targets(bars, periods=(5, 4, 3, 2))


def test_week_aggregation_requires_complete_monday_sunday_data() -> None:
    monday_ms = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    bars = [
        ResearchBar(
            start_ms=monday_ms + i * 15 * 60_000,
            end_ms=monday_ms + (i + 1) * 15 * 60_000 - 1,
            open=Decimal(i + 1),
            high=Decimal(i + 1),
            low=Decimal(i + 1),
            close=Decimal(i + 1),
        )
        for i in range(7 * 24 * 4)
    ]

    weeks, ends = aggregate_complete_utc_weeks(bars)

    assert len(weeks) == 1
    assert ends == (len(bars) - 1,)
    mapped = map_weekly_targets_to_source(len(bars), (1,), ends)
    assert mapped[-1] == 1
    assert sum(value is not None for value in mapped) == 1
