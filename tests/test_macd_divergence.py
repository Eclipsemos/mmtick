from dataclasses import replace
from decimal import Decimal

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.macd_divergence import (
    DivergenceConfig,
    DivergenceStructure,
    EntrySignal,
    ExecutionConfig,
    IndicatorSeries,
    SignalFilterConfig,
    SwingPoint,
    divergence_structures,
    ema_values,
    entry_signals,
    filter_entry_signals,
    monte_carlo,
    r_distribution,
    replay_signals,
    rolling_period_metrics,
    rsi_values,
    swing_points,
    wilder_atr_values,
)
from mastermind_tick.models import FundingRate


def _bar(
    index: int,
    open_price: str,
    high_price: str | None = None,
    low_price: str | None = None,
    close_price: str | None = None,
) -> ResearchBar:
    open_value = Decimal(open_price)
    close_value = Decimal(close_price or open_price)
    high_value = Decimal(high_price) if high_price is not None else max(open_value, close_value)
    low_value = Decimal(low_price) if low_price is not None else min(open_value, close_value)
    start_ms = index * 15 * 60_000
    return ResearchBar(
        start_ms=start_ms,
        end_ms=start_ms + 15 * 60_000 - 1,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=Decimal("1"),
    )


def _structure(identifier: str = "long-0-1") -> DivergenceStructure:
    return DivergenceStructure(
        id=identifier,
        direction="LONG",
        known_at=0,
        point_indices=(0, 1),
        prices=(100.0, 99.0),
        histograms=(-2.0, -1.0),
        score=0.5,
    )


def _indicators(count: int, atr: float = 1.0) -> IndicatorSeries:
    values = tuple(1.0 for _ in range(count))
    return IndicatorSeries(values, values, values, values, values, tuple(atr for _ in range(count)))


def test_ema_is_causal_and_observes_warmup() -> None:
    original = ema_values([1.0, 2.0, 3.0], period=2)
    extended = ema_values([1.0, 2.0, 3.0, 1000.0], period=2)

    assert original == pytest.approx((None, 5 / 3, 23 / 9), nan_ok=True)
    assert extended[:3] == original


def test_wilder_atr_uses_previous_close() -> None:
    bars = [
        _bar(0, "10", "10", "10", "10"),
        _bar(1, "12", "12", "12", "12"),
        _bar(2, "11", "11", "11", "11"),
    ]

    assert wilder_atr_values(bars, period=2) == pytest.approx((None, 1.0, 1.0), nan_ok=True)


def test_wilder_rsi_is_causal() -> None:
    bars = [_bar(index, str(index + 1)) for index in range(4)]

    original = rsi_values(bars[:3], period=2)
    extended = rsi_values(bars, period=2)

    assert original == (None, None, 100.0)
    assert extended[:3] == original


def test_confirmed_pivot_is_known_only_after_right_bars_complete() -> None:
    bars = [
        _bar(0, "10", "11", "9"),
        _bar(1, "9", "10", "8"),
        _bar(2, "8", "9", "5"),
        _bar(3, "7", "8", "6"),
        _bar(4, "8", "9", "7"),
    ]
    histogram = (-1.0, -2.0, -3.0, -2.0, -1.0)
    config = DivergenceConfig(points=2, pivot_left=1, pivot_right=2)

    points = swing_points(bars, histogram, config, "low")

    assert points == (SwingPoint("low", index=2, known_at=4, price=5.0, histogram=-3.0),)


def test_confirmed_window_histogram_match_uses_only_known_bars() -> None:
    bars = [
        _bar(0, "10", "11", "9"),
        _bar(1, "9", "10", "8"),
        _bar(2, "8", "9", "5"),
        _bar(3, "7", "8", "6"),
        _bar(4, "8", "9", "7"),
    ]
    histogram = (-1.0, -2.0, -3.0, -2.0, -4.0)
    config = DivergenceConfig(
        points=2,
        pivot_left=1,
        pivot_right=2,
        histogram_match="confirmed_window",
    )

    points = swing_points(bars, histogram, config, "low")

    assert points == (SwingPoint("low", index=2, known_at=4, price=5.0, histogram=-4.0),)


def test_rolling_extremum_does_not_change_when_future_bars_are_added() -> None:
    bars = [_bar(index, str(10 - index), low_price=str(10 - index)) for index in range(5)]
    histogram = tuple(float(-index - 1) for index in range(5))
    config = DivergenceConfig(points=2, swing_method="rolling", rolling_window=3)

    original = swing_points(bars[:4], histogram[:4], config, "low")
    extended = swing_points(bars, histogram, config, "low")

    assert extended[: len(original)] == original
    assert all(point.known_at == point.index for point in extended)


def test_double_and_triple_bullish_divergence_are_strict() -> None:
    points = (
        SwingPoint("low", 1, 2, 100.0, -3.0),
        SwingPoint("low", 3, 4, 95.0, -2.0),
        SwingPoint("low", 5, 6, 90.0, -1.0),
    )
    atr = (None, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0)

    doubles = divergence_structures(points, atr, 2, "LONG")
    triples = divergence_structures(points, atr, 3, "LONG")

    assert len(doubles) == 2
    assert len(triples) == 1
    assert triples[0].known_at == 6
    assert triples[0].score == pytest.approx(4 / 3)


def test_entry_waits_until_after_confirmation_and_fills_next_open() -> None:
    structure = DivergenceStructure(
        id="long-1-3-5",
        direction="LONG",
        known_at=4,
        point_indices=(1, 3, 5),
        prices=(100.0, 95.0, 90.0),
        histograms=(-3.0, -2.0, -1.0),
        score=1.0,
    )
    histogram = (None, -3.0, -2.0, -3.0, -2.0, -4.0, -3.0, -2.0)

    signals = entry_signals((structure,), histogram)

    assert len(signals) == 1
    assert signals[0].trigger_index == 6
    assert signals[0].entry_index == 7


def test_one_structure_emits_only_one_signal() -> None:
    structure = _structure()
    histogram = (-3.0, -2.0, -1.5, -1.0, -0.5, 0.1)

    signals = entry_signals((structure,), histogram)

    assert len(signals) == 1


def test_volume_filter_uses_only_prior_completed_bars() -> None:
    bars = [_bar(index, "100") for index in range(4)]
    bars[2] = replace(bars[2], volume=Decimal("2"))
    signal = EntrySignal(_structure(), trigger_index=2, entry_index=3)

    kept = filter_entry_signals(
        bars,
        _indicators(len(bars)),
        (signal,),
        SignalFilterConfig(volume_mean_window=2),
    )

    assert kept == (signal,)


def test_rsi_filter_rejects_long_when_not_oversold() -> None:
    bars = [_bar(index, str(index + 1)) for index in range(4)]
    signal = EntrySignal(_structure(), trigger_index=2, entry_index=3)

    kept = filter_entry_signals(
        bars,
        _indicators(len(bars)),
        (signal,),
        SignalFilterConfig(rsi_period=2, rsi_long_max=30),
    )

    assert kept == ()


def test_take_profit_executes_and_updates_equity() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
        _bar(2, "101", "104", "100", "103"),
    ]
    signal = EntrySignal(_structure(), trigger_index=0, entry_index=1)

    result = replay_signals(
        bars,
        _indicators(len(bars)),
        (signal,),
        ExecutionConfig(fee_bps=0, slippage_bps=0),
        symbol="BTCUSDT",
        timeframe_minutes=15,
    )

    assert result.total_trades == 1
    assert result.trades[0].exit_reason == "TAKE_PROFIT"
    assert result.trades[0].entry_at_ms == bars[1].start_ms
    assert result.final_equity == pytest.approx(102_000)
    assert result.monthly_returns[0][1] == pytest.approx(0.02)


def test_stop_executes_on_entry_bar() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "98", "99"),
    ]
    signal = EntrySignal(_structure(), trigger_index=0, entry_index=1)

    result = replay_signals(
        bars,
        _indicators(len(bars)),
        (signal,),
        ExecutionConfig(fee_bps=0, slippage_bps=0),
        symbol="BTCUSDT",
        timeframe_minutes=15,
    )

    assert result.trades[0].exit_reason == "STOP"
    assert result.final_equity == pytest.approx(99_000)


def test_ambiguous_bar_assumes_stop_before_take_profit() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "105", "97", "100"),
    ]
    signal = EntrySignal(_structure(), trigger_index=0, entry_index=1)

    result = replay_signals(
        bars,
        _indicators(len(bars)),
        (signal,),
        ExecutionConfig(fee_bps=0, slippage_bps=0),
        symbol="BTCUSDT",
        timeframe_minutes=15,
    )

    assert result.trades[0].exit_reason == "STOP_AMBIGUOUS"
    assert result.trades[0].ambiguous_exit is True
    assert result.ambiguous_bars == 1


def test_leverage_cap_reduces_actual_risk() -> None:
    bars = [
        _bar(0, "100", "100", "99.9", "100"),
        _bar(1, "100", "100", "99.8", "100"),
    ]
    signal = EntrySignal(_structure(), trigger_index=0, entry_index=1)

    result = replay_signals(
        bars,
        _indicators(len(bars), atr=0.0),
        (signal,),
        ExecutionConfig(fee_bps=0, slippage_bps=0, max_leverage=2.0),
        symbol="BTCUSDT",
        timeframe_minutes=15,
    )

    assert result.trades[0].leverage_capped is True
    assert result.trades[0].quantity == pytest.approx(2_000)
    assert result.trades[0].risk_fraction == pytest.approx(0.002)


def test_exit_does_not_allow_retroactive_same_open_reentry() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
        _bar(2, "100", "101", "98", "100"),
        _bar(3, "100", "101", "99", "100"),
    ]
    signals = (
        EntrySignal(_structure("first"), trigger_index=0, entry_index=1),
        EntrySignal(_structure("second"), trigger_index=1, entry_index=2),
    )

    result = replay_signals(
        bars,
        _indicators(len(bars)),
        signals,
        ExecutionConfig(fee_bps=0, slippage_bps=0),
        symbol="BTCUSDT",
        timeframe_minutes=15,
    )

    assert result.total_trades == 1
    assert result.trades[0].exit_at_ms == bars[2].end_ms


def test_replay_range_uses_full_history_signal_but_independent_equity_period() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
        _bar(2, "101", "104", "100", "103"),
    ]
    signal = EntrySignal(_structure(), trigger_index=0, entry_index=1)

    result = replay_signals(
        bars,
        _indicators(len(bars)),
        (signal,),
        ExecutionConfig(fee_bps=0, slippage_bps=0),
        symbol="BTCUSDT",
        timeframe_minutes=15,
        start_index=1,
        end_index=3,
    )

    assert result.total_trades == 1
    assert len(result.equity_curve) == 2
    assert result.equity_curve[0][0] == bars[1].end_ms
    assert result.final_equity == pytest.approx(102_000)


def test_monte_carlo_ruin_uses_path_not_only_final_equity() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "98", "99"),
    ]
    signal = EntrySignal(_structure(), trigger_index=0, entry_index=1)
    result = replay_signals(
        bars,
        _indicators(len(bars)),
        (signal,),
        ExecutionConfig(fee_bps=0, slippage_bps=0),
        symbol="BTCUSDT",
        timeframe_minutes=15,
    )
    loss = replace(result.trades[0], pnl_percent=-0.95)
    recovery = replace(result.trades[0], pnl_percent=20.0)

    simulation = monte_carlo((loss, recovery), simulations=2_000, seed=7)

    assert 0.45 < simulation["probability_of_ruin"] < 0.55


def test_funding_is_charged_while_position_is_open_and_logged() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
        _bar(2, "100", "101", "99", "100"),
    ]
    signal = EntrySignal(_structure(), trigger_index=0, entry_index=1)
    funding = [
        FundingRate(timestamp_ms=bars[1].start_ms, rate=Decimal("0.01"), mark_price=Decimal("100"))
    ]

    result = replay_signals(
        bars,
        _indicators(len(bars)),
        (signal,),
        ExecutionConfig(fee_bps=0, slippage_bps=0, max_leverage=1.0),
        symbol="BTCUSDT",
        timeframe_minutes=15,
        funding=funding,
    )

    assert result.funding_paid == pytest.approx(-500.0)
    assert result.trades[0].funding == pytest.approx(-500.0)
    assert result.final_equity == pytest.approx(99_500.0)


def test_r_distribution_uses_all_fixed_bins() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
    ]
    signal = EntrySignal(_structure(), trigger_index=0, entry_index=1)
    result = replay_signals(
        bars,
        _indicators(len(bars)),
        (signal,),
        ExecutionConfig(fee_bps=0, slippage_bps=0),
        symbol="BTCUSDT",
        timeframe_minutes=15,
    )

    distribution = r_distribution(result.trades)

    assert distribution["trade_count"] == 1
    assert len(distribution["bins"]) == 7
    assert sum(bucket["count"] for bucket in distribution["bins"]) == 1


def test_rolling_period_metrics_uses_completed_periods() -> None:
    curve = tuple((index * 86_400_000, 100.0 + index) for index in range(4))

    rows = rolling_period_metrics(curve, 100.0, window=2)

    assert len(rows) == 3
    assert rows[0]["period_end"] == "1970-01-02"
    assert rows[0]["rolling_win_rate"] == pytest.approx(0.5)
