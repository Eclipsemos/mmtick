"""Research-only volatility-spread breakout replay on closed OHLCV bars."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class SpreadBar:
    start_ms: int
    end_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")


@dataclass(frozen=True)
class SpreadExecution:
    timestamp_ms: int
    price: Decimal


@dataclass(frozen=True)
class SpreadParameters:
    variant: str
    direction: str
    fast_window: int
    slow_window: int
    entry_ratio: float
    exit_ratio: float
    breakout_window: int
    stop_atr: float
    max_hold_bars: int
    exposure: float = 1.25
    compression_ratio: float = 0.85
    compression_lookback: int = 16
    spread_measure: str = "true_range"
    minimum_volume_ratio: float | None = None

    def validate(self) -> None:
        if self.variant not in {
            "expansion_breakout",
            "compression_release",
            "compression_fade",
        }:
            raise ValueError(f"unknown volatility-spread variant: {self.variant}")
        if self.direction not in {"long_only", "long_short"}:
            raise ValueError(f"unknown direction: {self.direction}")
        if self.spread_measure not in {"true_range", "return_volatility", "body_range"}:
            raise ValueError(f"unknown spread measure: {self.spread_measure}")
        if min(self.fast_window, self.slow_window, self.breakout_window) < 1:
            raise ValueError("indicator windows must be positive")
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be shorter than slow_window")
        if self.entry_ratio <= 0 or self.exit_ratio < 0 or self.stop_atr <= 0:
            raise ValueError("spread thresholds and stop_atr must be positive")
        if self.max_hold_bars < 1 or self.exposure <= 0:
            raise ValueError("max_hold_bars and exposure must be positive")
        if self.minimum_volume_ratio is not None and self.minimum_volume_ratio <= 0:
            raise ValueError("minimum_volume_ratio must be positive when enabled")


@dataclass(frozen=True)
class SpreadFeatures:
    ratios: tuple[float | None, ...]
    slow_ranges: tuple[Decimal | None, ...]
    prior_highs: tuple[Decimal | None, ...]
    prior_lows: tuple[Decimal | None, ...]
    compression_seen: tuple[bool, ...]
    volume_ratios: tuple[float | None, ...]
    prior_means: tuple[Decimal | None, ...]


@dataclass(frozen=True)
class SpreadTrade:
    direction: str
    entry_at_ms: int
    exit_at_ms: int
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal


@dataclass(frozen=True)
class SpreadResult:
    initial_equity: float
    final_equity: float
    net_return: float
    max_drawdown: float
    completed_trades: int
    win_rate: float | None
    profit_factor: float | None
    total_fees: float
    total_funding: float
    exposure_fraction: float
    average_daily_return: float
    geometric_daily_return: float
    profitable_day_rate: float
    target_day_rate: float
    top_five_profit_concentration: float | None
    active_days: int
    bankrupt: bool
    daily_returns: tuple[tuple[str, float], ...]
    trades: tuple[SpreadTrade, ...]


def build_spread_features(
    bars: list[SpreadBar],
    *,
    fast_window: int,
    slow_window: int,
    breakout_window: int,
    compression_ratio: float = 0.85,
    compression_lookback: int = 16,
    spread_measure: str = "true_range",
) -> SpreadFeatures:
    if fast_window >= slow_window:
        raise ValueError("fast_window must be shorter than slow_window")
    if spread_measure not in {"true_range", "return_volatility", "body_range"}:
        raise ValueError(f"unknown spread measure: {spread_measure}")
    true_ranges: list[Decimal] = []
    normalized_ranges: list[float] = []
    body_ranges: list[float] = []
    returns: list[float] = []
    volumes: list[float] = []
    previous_close: Decimal | None = None
    for bar in bars:
        values = [bar.high - bar.low]
        if previous_close is not None:
            values.extend([abs(bar.high - previous_close), abs(bar.low - previous_close)])
        true_range = max(values)
        denominator = previous_close or bar.close
        true_ranges.append(true_range)
        normalized_ranges.append(float(true_range / denominator) if denominator else 0.0)
        body_ranges.append(float(abs(bar.close - bar.open) / denominator) if denominator else 0.0)
        returns.append(float(bar.close / previous_close - Decimal("1")) if previous_close else 0.0)
        volumes.append(float(bar.volume))
        previous_close = bar.close

    if spread_measure == "return_volatility":
        fast_means = _rolling_float_std(returns, fast_window)
        slow_means = _rolling_float_std(returns, slow_window)
    else:
        spread_values = normalized_ranges if spread_measure == "true_range" else body_ranges
        fast_means = _rolling_float_mean(spread_values, fast_window)
        slow_means = _rolling_float_mean(spread_values, slow_window)
    slow_ranges = _rolling_decimal_mean(true_ranges, slow_window)
    ratios: list[float | None] = []
    for fast, slow in zip(fast_means, slow_means, strict=True):
        ratios.append(fast / slow if fast is not None and slow not in {None, 0.0} else None)
    fast_volumes = _rolling_float_mean(volumes, fast_window)
    slow_volumes = _rolling_float_mean(volumes, slow_window)
    volume_ratios = tuple(
        fast / slow if fast is not None and slow not in {None, 0.0} else None
        for fast, slow in zip(fast_volumes, slow_volumes, strict=True)
    )

    prior_highs: list[Decimal | None] = []
    prior_lows: list[Decimal | None] = []
    close_means = _rolling_decimal_mean([bar.close for bar in bars], breakout_window)
    prior_means: list[Decimal | None] = [
        close_means[index - 1] if index > 0 else None for index in range(len(bars))
    ]
    compression_seen: list[bool] = []
    for index in range(len(bars)):
        channel_start = index - breakout_window
        if channel_start < 0:
            prior_highs.append(None)
            prior_lows.append(None)
        else:
            channel = bars[channel_start:index]
            prior_highs.append(max(item.high for item in channel))
            prior_lows.append(min(item.low for item in channel))
        compression_start = max(0, index - compression_lookback)
        prior_ratios = [item for item in ratios[compression_start:index] if item is not None]
        compression_seen.append(bool(prior_ratios) and min(prior_ratios) <= compression_ratio)

    return SpreadFeatures(
        ratios=tuple(ratios),
        slow_ranges=tuple(slow_ranges),
        prior_highs=tuple(prior_highs),
        prior_lows=tuple(prior_lows),
        compression_seen=tuple(compression_seen),
        volume_ratios=volume_ratios,
        prior_means=tuple(prior_means),
    )


def evaluate_spread(
    bars: list[SpreadBar],
    features: SpreadFeatures,
    parameters: SpreadParameters,
    *,
    start_ms: int,
    end_ms: int,
    funding_by_bar: list[list[FundingRate]] | None = None,
    execution_by_bar: list[SpreadExecution | None] | None = None,
    entry_direction_filter: tuple[int | None, ...] | None = None,
    entry_exposure_multipliers: tuple[float, ...] | None = None,
    initial_equity: Decimal = Decimal("100000"),
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
    quantity_step: Decimal = Decimal("0.01"),
) -> SpreadResult:
    parameters.validate()
    if len(features.ratios) != len(bars):
        raise ValueError("feature and bar lengths differ")
    if funding_by_bar is None:
        funding_by_bar = [[] for _ in bars]
    if len(funding_by_bar) != len(bars):
        raise ValueError("funding and bar lengths differ")
    if execution_by_bar is not None and len(execution_by_bar) != len(bars):
        raise ValueError("execution and bar lengths differ")
    if entry_direction_filter is not None and len(entry_direction_filter) != len(bars):
        raise ValueError("entry filter and bar lengths differ")
    if entry_direction_filter is not None and any(
        value not in {-1, 0, 1, None} for value in entry_direction_filter
    ):
        raise ValueError("entry filter values must be -1, 0, 1, or None")
    if entry_exposure_multipliers is not None and len(entry_exposure_multipliers) != len(bars):
        raise ValueError("entry exposure multipliers and bar lengths differ")
    if entry_exposure_multipliers is not None and any(
        not math.isfinite(value) or value <= 0 for value in entry_exposure_multipliers
    ):
        raise ValueError("entry exposure multipliers must be finite and positive")

    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        raise ValueError("no bars in requested volatility-spread range")

    fee_rate = fee_bps / Decimal("10000")
    slippage_rate = slippage_bps / Decimal("10000")
    exposure = Decimal(str(parameters.exposure))
    cash = initial_equity
    position = Decimal("0")
    entry_price = Decimal("0")
    entry_at_ms = 0
    entry_fee = Decimal("0")
    trade_funding = Decimal("0")
    favorable_extreme = Decimal("0")
    bars_held = 0
    pending_target = 0
    pending_exposure_multiplier = Decimal("1")
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    peak_equity = initial_equity
    max_drawdown = Decimal("0")
    positioned_bars = 0
    bankrupt = False
    trades: list[SpreadTrade] = []
    daily_equity: dict[str, Decimal] = {}

    def close_position(market_price: Decimal, timestamp_ms: int) -> None:
        nonlocal cash, position, entry_price, entry_fee, trade_funding, total_fees
        nonlocal favorable_extreme, bars_held
        if position == 0:
            return
        fill_price = market_price * (
            Decimal("1") - slippage_rate if position > 0 else Decimal("1") + slippage_rate
        )
        exit_fee = abs(position) * fill_price * fee_rate
        gross_pnl = position * (fill_price - entry_price)
        cash += gross_pnl - exit_fee
        total_fees += exit_fee
        trades.append(
            SpreadTrade(
                direction="LONG" if position > 0 else "SHORT",
                entry_at_ms=entry_at_ms,
                exit_at_ms=timestamp_ms,
                entry_price=entry_price,
                exit_price=fill_price,
                quantity=abs(position),
                fees=entry_fee + exit_fee,
                funding=trade_funding,
                net_pnl=gross_pnl - entry_fee - exit_fee + trade_funding,
            )
        )
        position = Decimal("0")
        entry_price = Decimal("0")
        entry_fee = Decimal("0")
        trade_funding = Decimal("0")
        favorable_extreme = Decimal("0")
        bars_held = 0

    def open_position(
        target: int,
        market_price: Decimal,
        timestamp_ms: int,
        exposure_multiplier: Decimal,
    ) -> None:
        nonlocal cash, position, entry_price, entry_at_ms, entry_fee, total_fees
        nonlocal favorable_extreme, bars_held
        if target == 0 or cash <= 0:
            return
        fill_price = market_price * (
            Decimal("1") + slippage_rate if target > 0 else Decimal("1") - slippage_rate
        )
        quantity = _floor_step(cash * exposure * exposure_multiplier / fill_price, quantity_step)
        fee = quantity * fill_price * fee_rate
        if quantity <= 0 or fee >= cash:
            return
        cash -= fee
        total_fees += fee
        position = quantity if target > 0 else -quantity
        entry_price = fill_price
        entry_at_ms = timestamp_ms
        entry_fee = fee
        favorable_extreme = fill_price
        bars_held = 0

    def apply_funding(funding: FundingRate) -> None:
        nonlocal cash, total_funding, trade_funding
        if position == 0:
            return
        amount = -(position * funding.mark_price * funding.rate)
        cash += amount
        total_funding += amount
        trade_funding += amount

    last_index = selected[-1]
    for index in selected:
        bar = bars[index]
        execution = (
            execution_by_bar[index]
            if execution_by_bar is not None
            else SpreadExecution(timestamp_ms=bar.start_ms, price=bar.open)
        )
        funding_events = funding_by_bar[index]
        before_execution = (
            funding_events
            if execution is None
            else [item for item in funding_events if item.timestamp_ms < execution.timestamp_ms]
        )
        after_execution = (
            []
            if execution is None
            else [item for item in funding_events if item.timestamp_ms >= execution.timestamp_ms]
        )
        for funding in before_execution:
            apply_funding(funding)

        current_sign = 1 if position > 0 else -1 if position < 0 else 0
        if execution is not None and pending_target != current_sign:
            close_position(execution.price, execution.timestamp_ms)
            open_position(
                pending_target,
                execution.price,
                execution.timestamp_ms,
                pending_exposure_multiplier,
            )

        for funding in after_execution:
            apply_funding(funding)

        current_sign = 1 if position > 0 else -1 if position < 0 else 0

        if position:
            positioned_bars += 1
            bars_held += 1
            favorable_extreme = (
                max(favorable_extreme, bar.high)
                if position > 0
                else min(favorable_extreme, bar.low)
            )

        equity = cash + position * (bar.close - entry_price)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, equity / peak_equity - Decimal("1"))
        day = _utc_date(bar.end_ms)
        daily_equity[day] = equity
        if equity <= 0:
            bankrupt = True
            last_index = index
            break

        ratio = features.ratios[index]
        slow_range = features.slow_ranges[index]
        if position:
            if ratio is None or slow_range is None:
                pending_target = current_sign
                continue
            stop_distance = slow_range * Decimal(str(parameters.stop_atr))
            stop_crossed = (
                bar.close <= favorable_extreme - stop_distance
                if position > 0
                else bar.close >= favorable_extreme + stop_distance
            )
            mean_reversion_exit = False
            if parameters.variant == "compression_fade":
                prior_mean = features.prior_means[index]
                mean_reversion_exit = prior_mean is not None and (
                    (position > 0 and bar.close >= prior_mean)
                    or (position < 0 and bar.close <= prior_mean)
                )
            ratio_exit = (
                ratio >= parameters.exit_ratio
                if parameters.variant == "compression_fade"
                else ratio <= parameters.exit_ratio
            )
            if (
                stop_crossed
                or mean_reversion_exit
                or ratio_exit
                or bars_held >= parameters.max_hold_bars
            ):
                pending_target = 0
            else:
                pending_target = current_sign
            continue

        pending_target = 0
        pending_exposure_multiplier = Decimal("1")
        prior_high = features.prior_highs[index]
        prior_low = features.prior_lows[index]
        ready = ratio is not None and (
            ratio <= parameters.entry_ratio
            if parameters.variant == "compression_fade"
            else ratio >= parameters.entry_ratio
        )
        if parameters.minimum_volume_ratio is not None:
            volume_ratio = features.volume_ratios[index]
            ready = (
                ready
                and volume_ratio is not None
                and (volume_ratio >= parameters.minimum_volume_ratio)
            )
        if parameters.variant in {"compression_release", "compression_fade"}:
            ready = ready and features.compression_seen[index]
        if not ready or prior_high is None or prior_low is None:
            continue
        if parameters.variant == "compression_fade":
            prior_mean = features.prior_means[index]
            if prior_mean is not None and bar.close < prior_low:
                pending_target = 1
            elif (
                prior_mean is not None
                and parameters.direction == "long_short"
                and bar.close > prior_high
            ):
                pending_target = -1
        elif bar.close > prior_high:
            pending_target = 1
        elif parameters.direction == "long_short" and bar.close < prior_low:
            pending_target = -1
        if (
            pending_target
            and entry_direction_filter is not None
            and entry_direction_filter[index] != pending_target
            and entry_direction_filter[index] is not None
        ):
            pending_target = 0
        if pending_target and entry_exposure_multipliers is not None:
            pending_exposure_multiplier = Decimal(str(entry_exposure_multipliers[index]))

    final_bar = bars[last_index]
    if position and not bankrupt:
        close_position(final_bar.close, final_bar.end_ms)
        final_equity = cash
        daily_equity[_utc_date(final_bar.end_ms)] = final_equity
        peak_equity = max(peak_equity, final_equity)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, final_equity / peak_equity - Decimal("1"))
    else:
        final_equity = cash + position * (final_bar.close - entry_price)

    daily_returns: list[tuple[str, float]] = []
    previous_equity = initial_equity
    for day, day_equity in daily_equity.items():
        value = day_equity / previous_equity - Decimal("1") if previous_equity else Decimal("0")
        daily_returns.append((day, float(value)))
        previous_equity = day_equity

    winning_pnls = sorted((trade.net_pnl for trade in trades if trade.net_pnl > 0), reverse=True)
    losing_pnls = [-trade.net_pnl for trade in trades if trade.net_pnl < 0]
    gross_profit = sum(winning_pnls, Decimal("0"))
    gross_loss = sum(losing_pnls, Decimal("0"))
    active_days = len(daily_returns)
    final_ratio = float(final_equity / initial_equity) if initial_equity else 0.0
    geometric_daily = (
        final_ratio ** (1 / active_days) - 1 if final_ratio > 0 and active_days else -1.0
    )
    return SpreadResult(
        initial_equity=float(initial_equity),
        final_equity=float(final_equity),
        net_return=float(final_equity / initial_equity - Decimal("1")),
        max_drawdown=float(max_drawdown),
        completed_trades=len(trades),
        win_rate=(sum(trade.net_pnl > 0 for trade in trades) / len(trades) if trades else None),
        profit_factor=float(gross_profit / gross_loss) if gross_loss else None,
        total_fees=float(total_fees),
        total_funding=float(total_funding),
        exposure_fraction=positioned_bars / len(selected),
        average_daily_return=(
            sum(value for _, value in daily_returns) / active_days if active_days else 0.0
        ),
        geometric_daily_return=geometric_daily,
        profitable_day_rate=(
            sum(value > 0 for _, value in daily_returns) / active_days if active_days else 0.0
        ),
        target_day_rate=(
            sum(value >= 0.05 for _, value in daily_returns) / active_days if active_days else 0.0
        ),
        top_five_profit_concentration=(
            float(sum(winning_pnls[:5], Decimal("0")) / gross_profit) if gross_profit else None
        ),
        active_days=active_days,
        bankrupt=bankrupt,
        daily_returns=tuple(daily_returns),
        trades=tuple(trades),
    )


def _rolling_float_mean(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= window:
            total -= values[index - window]
        if index >= window - 1:
            result[index] = total / window
    return result


def _rolling_float_std(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    total = 0.0
    squared_total = 0.0
    for index, value in enumerate(values):
        total += value
        squared_total += value * value
        if index >= window:
            removed = values[index - window]
            total -= removed
            squared_total -= removed * removed
        if index >= window - 1:
            mean = total / window
            variance = max(0.0, squared_total / window - mean * mean)
            result[index] = math.sqrt(variance)
    return result


def _rolling_decimal_mean(values: list[Decimal], window: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    total = Decimal("0")
    divisor = Decimal(window)
    for index, value in enumerate(values):
        total += value
        if index >= window:
            total -= values[index - window]
        if index >= window - 1:
            result[index] = total / divisor
    return result


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _utc_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).date().isoformat()


def daily_path_metrics(values: list[float]) -> dict[str, float]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in values:
        equity *= 1 + value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1)
    return {
        "net_return": equity - 1,
        "geometric_daily_return": equity ** (1 / len(values)) - 1 if values else 0.0,
        "max_daily_close_drawdown": max_drawdown,
    }


def pearson_correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left)
        * sum((value - right_mean) ** 2 for value in right)
    )
    return numerator / denominator if denominator else None
