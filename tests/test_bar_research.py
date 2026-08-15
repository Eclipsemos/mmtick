from decimal import Decimal

import pytest

from mastermind_tick.bar_research import (
    ResearchBar,
    aggregate_bars,
    atr_mean_reversion_targets,
    atr_trailing_stop_targets,
    bollinger_reversion_targets,
    chandelier_breakout_targets,
    ema_targets,
    evaluate_targets,
    keltner_breakout_targets,
    macd_targets,
    momentum_targets,
    wilder_atr_values,
)
from mastermind_tick.models import FundingRate


def _bar(index: int, open_price: str, close_price: str) -> ResearchBar:
    start_ms = index * 15 * 60_000
    open_value = Decimal(open_price)
    close_value = Decimal(close_price)
    return ResearchBar(
        start_ms=start_ms,
        end_ms=start_ms + 15 * 60_000 - 1,
        open=open_value,
        high=max(open_value, close_value),
        low=min(open_value, close_value),
        close=close_value,
        volume=Decimal("1"),
    )


def test_aggregate_bars_builds_complete_ohlcv_interval() -> None:
    bars = [
        _bar(0, "100", "102"),
        _bar(1, "102", "101"),
        _bar(2, "101", "105"),
        _bar(3, "105", "104"),
    ]

    aggregated = aggregate_bars(bars, 60)

    assert len(aggregated) == 1
    assert aggregated[0].open == Decimal("100")
    assert aggregated[0].high == Decimal("105")
    assert aggregated[0].low == Decimal("100")
    assert aggregated[0].close == Decimal("104")
    assert aggregated[0].volume == Decimal("4")


def test_closed_bar_target_fills_at_the_next_bar_open_and_applies_funding() -> None:
    bars = [
        _bar(0, "100", "110"),
        _bar(1, "120", "130"),
        _bar(2, "140", "150"),
        _bar(3, "160", "160"),
    ]
    funding = [
        [],
        [],
        [
            FundingRate(
                timestamp_ms=bars[2].start_ms,
                rate=Decimal("0.01"),
                mark_price=Decimal("150"),
            )
        ],
        [],
    ]

    result = evaluate_targets(
        bars,
        (1, 1, 0, 0),
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].start_ms,
        funding=funding,
        initial_equity=Decimal("1000"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        quantity_step=Decimal("1"),
    )

    assert result.completed_trades == 1
    assert result.trades[0].entry_at_ms == bars[1].start_ms
    assert result.trades[0].exit_at_ms == bars[3].start_ms
    assert result.total_funding == pytest.approx(-12.0)
    assert result.final_equity == pytest.approx(1308.0)


def test_momentum_uses_only_the_requested_lookback() -> None:
    bars = [
        _bar(0, "100", "100"),
        _bar(1, "100", "110"),
        _bar(2, "110", "90"),
    ]

    targets = momentum_targets(bars, lookback=1, threshold=0.05, direction="long_short")

    assert targets == (None, 1, -1)


def test_ema_deadband_moves_to_cash_when_trend_is_too_weak() -> None:
    bars = [_bar(index, str(100 + index), str(100 + index)) for index in range(5)]

    targets = ema_targets(
        bars,
        fast_period=2,
        slow_period=3,
        direction="long_short",
        minimum_separation=0.10,
    )

    assert targets[-1] == 0


def test_macd_targets_waits_for_signal_warmup_then_follows_trend() -> None:
    bars = [_bar(index, str(100 + index), str(100 + index)) for index in range(6)]

    targets = macd_targets(
        bars,
        fast_period=2,
        slow_period=3,
        signal_period=2,
        direction="long_short",
    )

    assert targets[:3] == (None, None, None)
    assert targets[-1] == 1


def test_bollinger_reversion_enters_an_extreme_and_exits_at_mean() -> None:
    bars = [
        _bar(0, "100", "100"),
        _bar(1, "100", "100"),
        _bar(2, "100", "90"),
        _bar(3, "90", "100"),
    ]

    targets = bollinger_reversion_targets(
        bars,
        period=3,
        standard_deviations=1.0,
        direction="long_only",
    )

    assert targets == (None, None, 1, 0)


def test_wilder_atr_values_uses_previous_close_for_true_range() -> None:
    bars = [
        _bar(0, "10", "10"),
        _bar(1, "12", "12"),
        _bar(2, "11", "11"),
    ]

    values = wilder_atr_values(bars, period=2)

    assert values == (None, Decimal("1"), Decimal("1"))


def test_atr_trailing_stop_can_reverse_to_short() -> None:
    bars = [
        _bar(0, "100", "100"),
        _bar(1, "100", "110"),
        _bar(2, "110", "120"),
        _bar(3, "120", "90"),
    ]

    targets = atr_trailing_stop_targets(bars, period=2, multiplier=1.0, direction="long_short")

    assert targets[-1] == -1


def test_atr_channel_families_emit_entry_signals_after_warmup() -> None:
    bars = [_bar(index, str(100 + index), str(100 + index)) for index in range(10)]
    bars[-1] = _bar(9, "109", "160")

    keltner = keltner_breakout_targets(
        bars, ema_period=3, atr_period=2, multiplier=0.1, direction="long_only"
    )
    chandelier = chandelier_breakout_targets(
        bars, entry_window=3, atr_period=2, exit_multiplier=3.0, direction="long_only"
    )

    assert keltner[-1] == 1
    assert chandelier[-1] == 1


def test_atr_mean_reversion_enters_and_exits_at_center() -> None:
    bars = [
        _bar(0, "100", "100"),
        _bar(1, "100", "100"),
        _bar(2, "100", "90"),
        _bar(3, "90", "100"),
    ]

    targets = atr_mean_reversion_targets(
        bars,
        center_period=3,
        atr_period=2,
        entry_distance_atr=1.0,
        direction="long_only",
    )

    assert targets == (None, None, 1, 0)


def test_forward_replay_can_mark_an_open_position_without_forced_exit() -> None:
    bars = [_bar(0, "100", "100"), _bar(1, "100", "110")]

    result = evaluate_targets(
        bars,
        (1, 1),
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].start_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        close_final_position=False,
    )

    assert result.completed_trades == 0
    assert result.ending_position == "LONG"
    assert result.final_equity > result.initial_equity
