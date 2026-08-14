from dataclasses import replace
from decimal import Decimal

import pytest

from mastermind_tick.volatility_spread import (
    SpreadBar,
    SpreadExecution,
    SpreadParameters,
    build_spread_features,
    daily_path_metrics,
    evaluate_spread,
    pearson_correlation,
)


def _bar(index: int, open_: str, high: str, low: str, close: str, volume: str = "0") -> SpreadBar:
    return SpreadBar(
        start_ms=index * 900_000,
        end_ms=(index + 1) * 900_000 - 1,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def test_channel_and_compression_features_only_use_prior_bars() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "102", "99", "101"),
        _bar(2, "101", "110", "100", "109"),
    ]

    features = build_spread_features(
        bars,
        fast_window=1,
        slow_window=2,
        breakout_window=2,
        compression_ratio=1.25,
        compression_lookback=2,
    )

    assert features.prior_highs[2] == Decimal("102")
    assert features.prior_lows[2] == Decimal("99")
    assert features.prior_means[2] == Decimal("100.5")
    assert features.compression_seen[2]


def test_compression_fade_parameters_are_supported() -> None:
    parameters = SpreadParameters(
        variant="compression_fade",
        direction="long_short",
        fast_window=2,
        slow_window=4,
        entry_ratio=1.0,
        exit_ratio=1.2,
        breakout_window=2,
        stop_atr=1.5,
        max_hold_bars=4,
    )

    parameters.validate()


def test_return_volatility_and_volume_spreads_detect_recent_expansion() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100", "1"),
        _bar(1, "100", "101", "99", "100", "1"),
        _bar(2, "100", "101", "99", "100", "1"),
        _bar(3, "100", "111", "99", "110", "10"),
    ]

    features = build_spread_features(
        bars,
        fast_window=2,
        slow_window=3,
        breakout_window=1,
        spread_measure="return_volatility",
    )

    assert features.ratios[3] is not None and features.ratios[3] > 1
    assert features.volume_ratios[3] is not None and features.volume_ratios[3] > 1


def test_breakout_signal_fills_at_next_bar_open() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
        _bar(2, "100", "110", "100", "109"),
        _bar(3, "111", "114", "110", "113"),
        _bar(4, "115", "116", "114", "115"),
    ]
    parameters = SpreadParameters(
        variant="expansion_breakout",
        direction="long_only",
        fast_window=1,
        slow_window=2,
        entry_ratio=1.1,
        exit_ratio=0,
        breakout_window=1,
        stop_atr=10,
        max_hold_bars=1,
        exposure=1,
    )
    features = build_spread_features(
        bars,
        fast_window=1,
        slow_window=2,
        breakout_window=1,
    )

    result = evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].start_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.completed_trades == 1
    assert result.trades[0].entry_at_ms == bars[3].start_ms
    assert result.trades[0].entry_price == bars[3].open
    assert result.trades[0].exit_at_ms == bars[4].start_ms
    assert result.trades[0].exit_price == bars[4].open

    volume_blocked = evaluate_spread(
        bars,
        features,
        replace(parameters, minimum_volume_ratio=1.0),
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].start_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    assert volume_blocked.completed_trades == 0


def test_short_breakout_uses_the_same_next_open_timing() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
        _bar(2, "100", "100", "90", "91"),
        _bar(3, "89", "90", "86", "87"),
        _bar(4, "85", "86", "84", "85"),
    ]
    parameters = SpreadParameters(
        variant="expansion_breakout",
        direction="long_short",
        fast_window=1,
        slow_window=2,
        entry_ratio=1.1,
        exit_ratio=0,
        breakout_window=1,
        stop_atr=10,
        max_hold_bars=1,
        exposure=1,
    )
    features = build_spread_features(
        bars,
        fast_window=1,
        slow_window=2,
        breakout_window=1,
    )

    result = evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].start_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.completed_trades == 1
    assert result.trades[0].direction == "SHORT"
    assert result.trades[0].entry_at_ms == bars[3].start_ms
    assert result.trades[0].entry_price == bars[3].open
    assert result.trades[0].exit_at_ms == bars[4].start_ms
    assert result.trades[0].net_pnl > 0


def test_pending_action_can_fill_on_the_first_persisted_tick() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
        _bar(2, "100", "110", "100", "109"),
        _bar(3, "111", "114", "110", "113"),
        _bar(4, "115", "116", "114", "115"),
    ]
    parameters = SpreadParameters(
        variant="expansion_breakout",
        direction="long_only",
        fast_window=1,
        slow_window=2,
        entry_ratio=1.1,
        exit_ratio=0,
        breakout_window=1,
        stop_atr=10,
        max_hold_bars=1,
        exposure=1,
    )
    features = build_spread_features(
        bars,
        fast_window=1,
        slow_window=2,
        breakout_window=1,
    )
    executions = [
        SpreadExecution(timestamp_ms=bar.start_ms + 125, price=bar.open + Decimal("0.25"))
        for bar in bars
    ]

    result = evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].start_ms,
        execution_by_bar=executions,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.trades[0].entry_at_ms == bars[3].start_ms + 125
    assert result.trades[0].entry_price == bars[3].open + Decimal("0.25")
    assert result.trades[0].exit_at_ms == bars[4].start_ms + 125


def test_entry_direction_filter_blocks_mismatched_breakout() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
        _bar(2, "100", "110", "100", "109"),
        _bar(3, "111", "114", "110", "113"),
        _bar(4, "115", "116", "114", "115"),
    ]
    parameters = SpreadParameters(
        variant="expansion_breakout",
        direction="long_only",
        fast_window=1,
        slow_window=2,
        entry_ratio=1.1,
        exit_ratio=0,
        breakout_window=1,
        stop_atr=10,
        max_hold_bars=1,
        exposure=1,
    )
    features = build_spread_features(bars, fast_window=1, slow_window=2, breakout_window=1)

    result = evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].start_ms,
        entry_direction_filter=(0, 0, -1, -1, -1),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.completed_trades == 0


def test_entry_exposure_multiplier_is_fixed_from_the_closed_signal_bar() -> None:
    bars = [
        _bar(0, "100", "101", "99", "100"),
        _bar(1, "100", "101", "99", "100"),
        _bar(2, "100", "110", "100", "109"),
        _bar(3, "111", "114", "110", "113"),
        _bar(4, "115", "116", "114", "115"),
    ]
    parameters = SpreadParameters(
        variant="expansion_breakout",
        direction="long_only",
        fast_window=1,
        slow_window=2,
        entry_ratio=1.1,
        exit_ratio=0,
        breakout_window=1,
        stop_atr=10,
        max_hold_bars=1,
        exposure=1,
    )
    features = build_spread_features(bars, fast_window=1, slow_window=2, breakout_window=1)
    base = evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].start_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    doubled = evaluate_spread(
        bars,
        features,
        parameters,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].start_ms,
        entry_exposure_multipliers=(1, 1, 2, 1, 1),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert doubled.trades[0].quantity == base.trades[0].quantity * 2
    assert doubled.trades[0].net_pnl == base.trades[0].net_pnl * 2


def test_entry_filter_length_and_values_are_validated() -> None:
    bars = [_bar(0, "100", "101", "99", "100")]
    features = build_spread_features(bars, fast_window=1, slow_window=2, breakout_window=1)
    parameters = SpreadParameters(
        variant="expansion_breakout",
        direction="long_only",
        fast_window=1,
        slow_window=2,
        entry_ratio=1,
        exit_ratio=0,
        breakout_window=1,
        stop_atr=1,
        max_hold_bars=1,
    )

    with pytest.raises(ValueError, match="entry filter and bar lengths"):
        evaluate_spread(bars, features, parameters, start_ms=0, end_ms=0, entry_direction_filter=())
    with pytest.raises(ValueError, match="entry filter values"):
        evaluate_spread(
            bars,
            features,
            parameters,
            start_ms=0,
            end_ms=0,
            entry_direction_filter=(2,),
        )
    with pytest.raises(ValueError, match="entry exposure multipliers"):
        evaluate_spread(
            bars,
            features,
            parameters,
            start_ms=0,
            end_ms=0,
            entry_exposure_multipliers=(),
        )


def test_daily_path_metrics_compound_and_measure_close_drawdown() -> None:
    metrics = daily_path_metrics([0.10, -0.10, 0.20])

    assert metrics["net_return"] == pytest.approx(0.188)
    assert metrics["max_daily_close_drawdown"] == pytest.approx(-0.10)
    assert pearson_correlation([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
