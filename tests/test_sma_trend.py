from datetime import UTC, datetime
from decimal import Decimal

from mastermind_tick.bar_research import ResearchBar, evaluate_targets
from mastermind_tick.sma_trend import (
    aggregate_complete_periods,
    four_sma_targets,
    map_targets_to_source,
)


def _bars(count: int) -> list[ResearchBar]:
    start = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    result = []
    for index in range(count):
        value = Decimal(100 + index)
        begin = start + index * 15 * 60_000
        result.append(ResearchBar(begin, begin + 15 * 60_000 - 1, value, value, value, value))
    return result


def test_hour_aggregation_is_complete_and_maps_final_source_bar() -> None:
    bars = _bars(4)
    aggregated, ends = aggregate_complete_periods(bars, "1h")
    assert len(aggregated) == 1
    assert ends == (3,)
    mapped = map_targets_to_source(4, (1,), ends)
    assert mapped == (None, None, None, 1)


def test_incomplete_period_is_discarded() -> None:
    aggregated, ends = aggregate_complete_periods(_bars(3), "1h")
    assert aggregated == []
    assert ends == ()


def test_four_sma_ordering_and_price_confirmation() -> None:
    bars = _bars(10)
    targets = four_sma_targets(bars, (2, 3, 4, 5))
    assert targets[-1] == 1
    confirmed = four_sma_targets(bars, (2, 3, 4, 5), require_price_confirmation=True)
    assert confirmed[-1] == 1


def test_target_executes_on_next_source_open() -> None:
    bars = _bars(4)
    targets = (None, 1, None, 0)
    result = evaluate_targets(
        bars,
        targets,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    assert result.trades[0].entry_at_ms == bars[2].start_ms
