"""Causal MACD divergence research primitives and closed-bar execution replay."""

from __future__ import annotations

import math
import random
from bisect import bisect_left, bisect_right, insort
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from statistics import median
from typing import Literal

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.models import FundingRate

Direction = Literal["LONG", "SHORT"]
SwingKind = Literal["low", "high"]
SwingMethod = Literal["pivot", "rolling"]
HistogramMatch = Literal["at_swing", "confirmed_window"]
TrendFilter = Literal["with_ema", "against_ema"]


@dataclass(frozen=True)
class IndicatorConfig:
    macd_fast: int = 13
    macd_slow: int = 34
    macd_signal: int = 9
    atr_period: int = 13


@dataclass(frozen=True)
class DivergenceConfig:
    points: int = 3
    swing_method: SwingMethod = "pivot"
    pivot_left: int = 3
    pivot_right: int = 3
    rolling_window: int = 10
    histogram_match: HistogramMatch = "at_swing"


@dataclass(frozen=True)
class ExecutionConfig:
    stop_atr: float = 1.0
    reward_risk: float = 2.0
    risk_fraction: float = 0.01
    fee_bps: float = 4.0
    slippage_bps: float = 2.0
    max_leverage: float | None = 5.0
    initial_equity: float = 100_000.0


@dataclass(frozen=True)
class SignalFilterConfig:
    trend: TrendFilter | None = None
    trend_period: int = 200
    rsi_period: int = 14
    rsi_long_max: float | None = None
    rsi_short_min: float | None = None
    atr_percentile: float | None = None
    atr_percentile_window: int = 30 * 96
    volume_mean_window: int | None = None
    minimum_histogram_atr: float | None = None


@dataclass(frozen=True)
class IndicatorSeries:
    ema_fast: tuple[float | None, ...]
    ema_slow: tuple[float | None, ...]
    macd: tuple[float | None, ...]
    signal: tuple[float | None, ...]
    histogram: tuple[float | None, ...]
    atr: tuple[float | None, ...]


@dataclass(frozen=True)
class SwingPoint:
    kind: SwingKind
    index: int
    known_at: int
    price: float
    histogram: float


@dataclass(frozen=True)
class DivergenceStructure:
    id: str
    direction: Direction
    known_at: int
    point_indices: tuple[int, ...]
    prices: tuple[float, ...]
    histograms: tuple[float, ...]
    score: float


@dataclass(frozen=True)
class EntrySignal:
    structure: DivergenceStructure
    trigger_index: int
    entry_index: int


@dataclass(frozen=True)
class DivergenceTrade:
    symbol: str
    timeframe_minutes: int
    direction: Direction
    signal_at_ms: int
    entry_at_ms: int
    exit_at_ms: int
    entry_price: float
    stop_price: float
    take_profit: float
    exit_price: float
    exit_reason: str
    atr: float
    macd: float
    macd_signal: float
    histogram: float
    point_indices: tuple[int, ...]
    prices: tuple[float, ...]
    histograms: tuple[float, ...]
    divergence_score: float
    quantity: float
    risk_fraction: float
    pnl: float
    pnl_percent: float
    r_multiple: float
    fees: float
    funding: float
    slippage_cost: float
    holding_bars: int
    ambiguous_exit: bool
    leverage_capped: bool


@dataclass(frozen=True)
class ReplaySummary:
    initial_equity: float
    final_equity: float
    net_return: float
    total_trades: int
    win_rate: float | None
    average_win_r: float | None
    average_loss_r: float | None
    average_r: float | None
    expectancy_r: float | None
    profit_factor: float | None
    sharpe: float | None
    sortino: float | None
    cagr: float | None
    max_drawdown: float
    calmar: float | None
    longest_losing_streak: int
    longest_winning_streak: int
    average_holding_bars: float | None
    median_holding_bars: float | None
    exposure: float
    fees_paid: float
    funding_paid: float
    slippage_cost: float
    ambiguous_bars: int
    leverage_capped_trades: int
    trades: tuple[DivergenceTrade, ...]
    equity_curve: tuple[tuple[int, float], ...]
    drawdown_curve: tuple[tuple[int, float], ...]
    monthly_returns: tuple[tuple[str, float], ...]
    yearly_returns: tuple[tuple[str, float], ...]


def indicator_series(
    bars: list[ResearchBar], config: IndicatorConfig | None = None
) -> IndicatorSeries:
    config = config or IndicatorConfig()
    if not 1 <= config.macd_fast < config.macd_slow or config.macd_signal < 1:
        raise ValueError("MACD periods must satisfy 1 <= fast < slow and signal >= 1")
    closes = [float(bar.close) for bar in bars]
    fast = ema_values(closes, config.macd_fast)
    slow = ema_values(closes, config.macd_slow)
    macd = tuple(
        None if left is None or right is None else left - right
        for left, right in zip(fast, slow, strict=True)
    )
    signal = ema_values(macd, config.macd_signal)
    histogram = tuple(
        None if value is None or average is None else value - average
        for value, average in zip(macd, signal, strict=True)
    )
    return IndicatorSeries(
        ema_fast=fast,
        ema_slow=slow,
        macd=macd,
        signal=signal,
        histogram=histogram,
        atr=wilder_atr_values(bars, config.atr_period),
    )


def ema_values(
    values: list[float] | tuple[float | None, ...], period: int
) -> tuple[float | None, ...]:
    """Return a causal EMA seeded by the first available value."""
    if period < 1:
        raise ValueError("EMA period must be positive")
    alpha = 2.0 / (period + 1)
    result: list[float | None] = []
    ema: float | None = None
    available = 0
    for value in values:
        if value is None:
            result.append(None)
            continue
        ema = value if ema is None else ema + alpha * (value - ema)
        available += 1
        result.append(ema if available >= period else None)
    return tuple(result)


def wilder_atr_values(bars: list[ResearchBar], period: int) -> tuple[float | None, ...]:
    if period < 1:
        raise ValueError("ATR period must be positive")
    result: list[float | None] = []
    ranges: list[float] = []
    atr: float | None = None
    previous_close: float | None = None
    for bar in bars:
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)
        true_range = high - low
        if previous_close is not None:
            true_range = max(true_range, abs(high - previous_close), abs(low - previous_close))
        ranges.append(true_range)
        if len(ranges) < period:
            result.append(None)
        elif atr is None:
            atr = sum(ranges[-period:]) / period
            result.append(atr)
        else:
            atr = (atr * (period - 1) + true_range) / period
            result.append(atr)
        previous_close = close
    return tuple(result)


def rsi_values(bars: list[ResearchBar], period: int = 14) -> tuple[float | None, ...]:
    """Return a causal Wilder RSI series."""
    if period < 1:
        raise ValueError("RSI period must be positive")
    result: list[float | None] = [None] * len(bars)
    if len(bars) <= period:
        return tuple(result)
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, period + 1):
        change = float(bars[index].close - bars[index - 1].close)
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    result[period] = _rsi(average_gain, average_loss)
    for index in range(period + 1, len(bars)):
        change = float(bars[index].close - bars[index - 1].close)
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
        result[index] = _rsi(average_gain, average_loss)
    return tuple(result)


def filter_entry_signals(
    bars: list[ResearchBar],
    indicators: IndicatorSeries,
    signals: tuple[EntrySignal, ...],
    config: SignalFilterConfig,
) -> tuple[EntrySignal, ...]:
    """Apply closed-trigger-bar filters without changing entry timing."""
    _validate_filter(config)
    closes = [float(bar.close) for bar in bars]
    trend = ema_values(closes, config.trend_period) if config.trend is not None else None
    rsi = (
        rsi_values(bars, config.rsi_period)
        if config.rsi_long_max is not None or config.rsi_short_min is not None
        else None
    )
    atr_percentiles = (
        _rolling_atr_percentiles(bars, indicators.atr, config.atr_percentile_window)
        if config.atr_percentile is not None
        else None
    )
    volume_means = (
        _prior_volume_means(bars, config.volume_mean_window)
        if config.volume_mean_window is not None
        else None
    )
    kept = []
    for signal in signals:
        index = signal.trigger_index
        direction = signal.structure.direction
        close = closes[index]
        if trend is not None:
            average = trend[index]
            if average is None:
                continue
            with_trend = close > average if direction == "LONG" else close < average
            if config.trend == "with_ema" and not with_trend:
                continue
            if config.trend == "against_ema" and with_trend:
                continue
        if rsi is not None:
            value = rsi[index]
            if value is None:
                continue
            if direction == "LONG" and config.rsi_long_max is not None:
                if value >= config.rsi_long_max:
                    continue
            if direction == "SHORT" and config.rsi_short_min is not None:
                if value <= config.rsi_short_min:
                    continue
        if atr_percentiles is not None:
            percentile = atr_percentiles[index]
            if percentile is None or percentile < config.atr_percentile:
                continue
        if volume_means is not None:
            average_volume = volume_means[index]
            if average_volume is None or float(bars[index].volume) <= average_volume:
                continue
        if config.minimum_histogram_atr is not None:
            histogram = indicators.histogram[index]
            atr = indicators.atr[index]
            if histogram is None or atr is None or atr <= 0:
                continue
            if abs(histogram) / atr < config.minimum_histogram_atr:
                continue
        kept.append(signal)
    return tuple(kept)


def _rsi(average_gain: float, average_loss: float) -> float:
    if average_gain == 0 and average_loss == 0:
        return 50.0
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _rolling_atr_percentiles(
    bars: list[ResearchBar],
    atr: tuple[float | None, ...],
    window: int,
) -> tuple[float | None, ...]:
    ordered: list[float] = []
    history: deque[float | None] = deque()
    result: list[float | None] = []
    for bar, value in zip(bars, atr, strict=True):
        normalized = value / float(bar.close) if value is not None and bar.close > 0 else None
        if normalized is None or not ordered:
            result.append(None)
        else:
            result.append(bisect_right(ordered, normalized) / len(ordered))
        history.append(normalized)
        if normalized is not None:
            insort(ordered, normalized)
        if len(history) > window:
            removed = history.popleft()
            if removed is not None:
                del ordered[bisect_left(ordered, removed)]
    return tuple(result)


def _prior_volume_means(bars: list[ResearchBar], window: int) -> tuple[float | None, ...]:
    values: deque[float] = deque()
    total = 0.0
    result: list[float | None] = []
    for bar in bars:
        result.append(total / len(values) if len(values) == window else None)
        value = float(bar.volume)
        values.append(value)
        total += value
        if len(values) > window:
            total -= values.popleft()
    return tuple(result)


def swing_points(
    bars: list[ResearchBar],
    histogram: tuple[float | None, ...],
    config: DivergenceConfig,
    kind: SwingKind,
) -> tuple[SwingPoint, ...]:
    if config.points not in {2, 3}:
        raise ValueError("divergence points must be two or three")
    if config.swing_method == "pivot":
        return _confirmed_pivots(bars, histogram, config, kind)
    if config.swing_method == "rolling":
        return _rolling_extrema(bars, histogram, config, kind)
    raise ValueError(f"unsupported swing method: {config.swing_method}")


def _confirmed_pivots(
    bars: list[ResearchBar],
    histogram: tuple[float | None, ...],
    config: DivergenceConfig,
    kind: SwingKind,
) -> tuple[SwingPoint, ...]:
    left = config.pivot_left
    right = config.pivot_right
    if left < 1 or right < 1:
        raise ValueError("pivot left/right must be positive")
    points: list[SwingPoint] = []
    prices = [float(bar.low if kind == "low" else bar.high) for bar in bars]
    for index in range(left, len(bars) - right):
        value = prices[index]
        prior = prices[index - left : index]
        future = prices[index + 1 : index + right + 1]
        confirmed = (
            value < min(prior) and value <= min(future)
            if kind == "low"
            else value > max(prior) and value >= max(future)
        )
        if not confirmed:
            continue
        matched = _matched_histogram(histogram, index, index + right, config, kind)
        if matched is None:
            continue
        points.append(SwingPoint(kind, index, index + right, value, matched))
    return tuple(points)


def _rolling_extrema(
    bars: list[ResearchBar],
    histogram: tuple[float | None, ...],
    config: DivergenceConfig,
    kind: SwingKind,
) -> tuple[SwingPoint, ...]:
    window = config.rolling_window
    if window < 2:
        raise ValueError("rolling window must be at least two")
    prices = [float(bar.low if kind == "low" else bar.high) for bar in bars]
    points: list[SwingPoint] = []
    for index in range(window - 1, len(bars)):
        value = prices[index]
        prior = prices[index - window + 1 : index]
        confirmed = value < min(prior) if kind == "low" else value > max(prior)
        if not confirmed:
            continue
        matched = _matched_histogram(histogram, index, index, config, kind)
        if matched is not None:
            points.append(SwingPoint(kind, index, index, value, matched))
    return tuple(points)


def _matched_histogram(
    histogram: tuple[float | None, ...],
    swing_index: int,
    known_at: int,
    config: DivergenceConfig,
    kind: SwingKind,
) -> float | None:
    if config.histogram_match == "at_swing":
        return histogram[swing_index]
    if config.histogram_match != "confirmed_window":
        raise ValueError(f"unsupported histogram match: {config.histogram_match}")
    start = max(0, swing_index - config.pivot_left)
    values = [value for value in histogram[start : known_at + 1] if value is not None]
    if not values:
        return None
    return min(values) if kind == "low" else max(values)


def divergence_structures(
    points: tuple[SwingPoint, ...],
    atr: tuple[float | None, ...],
    point_count: int,
    direction: Direction,
) -> tuple[DivergenceStructure, ...]:
    if point_count not in {2, 3}:
        raise ValueError("point count must be two or three")
    expected_kind = "low" if direction == "LONG" else "high"
    structures: list[DivergenceStructure] = []
    for end in range(point_count - 1, len(points)):
        selected = points[end - point_count + 1 : end + 1]
        if any(point.kind != expected_kind for point in selected):
            continue
        prices = tuple(point.price for point in selected)
        histograms = tuple(point.histogram for point in selected)
        if direction == "LONG":
            valid = _strictly_decreasing(prices) and _strictly_increasing(histograms)
            valid = valid and all(value < 0 for value in histograms)
        else:
            valid = _strictly_increasing(prices) and _strictly_decreasing(histograms)
            valid = valid and all(value > 0 for value in histograms)
        if not valid:
            continue
        last_atr = atr[selected[-1].index]
        if last_atr is None or last_atr <= 0 or histograms[0] == 0:
            continue
        price_move = abs(prices[-1] - prices[0]) / last_atr
        momentum_recovery = abs(histograms[-1] - histograms[0]) / abs(histograms[0])
        indices = tuple(point.index for point in selected)
        structures.append(
            DivergenceStructure(
                id=f"{direction.lower()}-{'-'.join(map(str, indices))}",
                direction=direction,
                known_at=max(point.known_at for point in selected),
                point_indices=indices,
                prices=prices,
                histograms=histograms,
                score=price_move * momentum_recovery,
            )
        )
    return tuple(structures)


def entry_signals(
    structures: tuple[DivergenceStructure, ...],
    histogram: tuple[float | None, ...],
) -> tuple[EntrySignal, ...]:
    """Trigger once per structure after formation, never on the formation bar itself."""
    by_known: dict[int, list[DivergenceStructure]] = {}
    for structure in structures:
        by_known.setdefault(structure.known_at, []).append(structure)
    active: dict[Direction, DivergenceStructure | None] = {"LONG": None, "SHORT": None}
    signals: list[EntrySignal] = []
    for index in range(0, len(histogram) - 1):
        for structure in by_known.get(index, ()):
            active[structure.direction] = structure
        if index == 0:
            continue
        current = histogram[index]
        previous = histogram[index - 1]
        if current is None or previous is None:
            continue
        if current >= 0:
            active["LONG"] = None
        if current <= 0:
            active["SHORT"] = None
        long_structure = active["LONG"]
        if (
            long_structure is not None
            and index > long_structure.known_at
            and previous < 0
            and current > previous
        ):
            signals.append(EntrySignal(long_structure, index, index + 1))
            active["LONG"] = None
        short_structure = active["SHORT"]
        if (
            short_structure is not None
            and index > short_structure.known_at
            and previous > 0
            and current < previous
        ):
            signals.append(EntrySignal(short_structure, index, index + 1))
            active["SHORT"] = None
    return tuple(sorted(signals, key=lambda item: (item.entry_index, item.structure.id)))


def replay_signals(
    bars: list[ResearchBar],
    indicators: IndicatorSeries,
    signals: tuple[EntrySignal, ...],
    execution: ExecutionConfig,
    *,
    symbol: str,
    timeframe_minutes: int,
    funding: list[FundingRate] | None = None,
    start_index: int = 0,
    end_index: int | None = None,
) -> ReplaySummary:
    _validate_execution(execution)
    end_index = len(bars) if end_index is None else end_index
    if not 0 <= start_index < end_index <= len(bars):
        raise ValueError("replay range must select at least one bar")
    equity = Decimal(str(execution.initial_equity))
    peak = equity
    equity_curve: list[tuple[int, float]] = []
    drawdown_curve: list[tuple[int, float]] = []
    trades: list[DivergenceTrade] = []
    signal_cursor = 0
    open_trade: dict | None = None
    exposure_bars = 0
    ambiguous_bars = 0
    capped_trades = 0
    funding_paid = 0.0
    funding_by_index = _funding_by_bar(bars, funding or [])
    fee_rate = Decimal(str(execution.fee_bps)) / Decimal("10000")
    slippage_rate = Decimal(str(execution.slippage_bps)) / Decimal("10000")
    risk_fraction = Decimal(str(execution.risk_fraction))
    stop_atr = Decimal(str(execution.stop_atr))
    reward_risk = Decimal(str(execution.reward_risk))
    max_leverage = (
        Decimal(str(execution.max_leverage)) if execution.max_leverage is not None else None
    )

    while signal_cursor < len(signals) and signals[signal_cursor].entry_index < start_index:
        signal_cursor += 1

    for index in range(start_index, end_index):
        bar = bars[index]
        was_open = open_trade is not None
        if open_trade is not None:
            for event in funding_by_index[index]:
                amount = -(
                    open_trade["direction_sign"]
                    * open_trade["quantity"]
                    * Decimal(str(event.mark_price))
                    * event.rate
                )
                equity += amount
                open_trade["funding"] += amount
                funding_paid += float(amount)
            exposure_bars += 1
            outcome = _exit_outcome(bar, open_trade)
            if outcome is not None:
                trade, equity_delta = _complete_open_trade(
                    open_trade,
                    bars,
                    bar,
                    index,
                    outcome,
                    fee_rate,
                    slippage_rate,
                    symbol,
                    timeframe_minutes,
                )
                ambiguous_bars += int(trade.ambiguous_exit)
                equity += equity_delta
                trades.append(trade)
                open_trade = None

        while signal_cursor < len(signals) and signals[signal_cursor].entry_index < index:
            signal_cursor += 1
        if (
            not was_open
            and open_trade is None
            and signal_cursor < len(signals)
            and signals[signal_cursor].entry_index == index
        ):
            signal = signals[signal_cursor]
            signal_cursor += 1
            trigger = bars[signal.trigger_index]
            atr = indicators.atr[signal.trigger_index]
            macd = indicators.macd[signal.trigger_index]
            macd_signal = indicators.signal[signal.trigger_index]
            histogram = indicators.histogram[signal.trigger_index]
            if (
                atr is not None
                and macd is not None
                and macd_signal is not None
                and histogram is not None
            ):
                direction_sign = (
                    Decimal("1") if signal.structure.direction == "LONG" else Decimal("-1")
                )
                raw_entry = bar.open
                entry_price = raw_entry * (
                    1 + slippage_rate if direction_sign > 0 else 1 - slippage_rate
                )
                stop = (
                    trigger.low - Decimal(str(atr)) * stop_atr
                    if direction_sign > 0
                    else trigger.high + Decimal(str(atr)) * stop_atr
                )
                stop_distance = direction_sign * (entry_price - stop)
                if stop_distance > 0 and equity > 0:
                    requested_risk = equity * risk_fraction
                    quantity = requested_risk / stop_distance
                    leverage_capped = False
                    if max_leverage is not None:
                        max_quantity = equity * max_leverage / entry_price
                        if quantity > max_quantity:
                            quantity = max_quantity
                            leverage_capped = True
                    quantity = _floor_quantity(quantity)
                    if quantity > 0:
                        take_profit = entry_price + direction_sign * reward_risk * stop_distance
                        entry_fee = abs(entry_price * quantity) * fee_rate
                        actual_risk = quantity * stop_distance
                        equity -= entry_fee
                        capped_trades += int(leverage_capped)
                        open_trade = {
                            "signal": signal,
                            "direction": signal.structure.direction,
                            "direction_sign": direction_sign,
                            "entry_price": entry_price,
                            "entry_equity": equity + entry_fee,
                            "entry_fee": entry_fee,
                            "entry_slippage": abs(entry_price - raw_entry) * quantity,
                            "quantity": quantity,
                            "stop": stop,
                            "take_profit": take_profit,
                            "risk_amount": actual_risk,
                            "risk_fraction": actual_risk / (equity + entry_fee),
                            "atr": atr,
                            "macd": macd,
                            "macd_signal": macd_signal,
                            "histogram": histogram,
                            "leverage_capped": leverage_capped,
                            "funding": Decimal("0"),
                        }

        if not was_open and open_trade is not None:
            for event in funding_by_index[index]:
                amount = -(
                    open_trade["direction_sign"]
                    * open_trade["quantity"]
                    * Decimal(str(event.mark_price))
                    * event.rate
                )
                equity += amount
                open_trade["funding"] += amount
                funding_paid += float(amount)
            exposure_bars += 1
            outcome = _exit_outcome(bar, open_trade)
            if outcome is not None:
                trade, equity_delta = _complete_open_trade(
                    open_trade,
                    bars,
                    bar,
                    index,
                    outcome,
                    fee_rate,
                    slippage_rate,
                    symbol,
                    timeframe_minutes,
                )
                ambiguous_bars += int(trade.ambiguous_exit)
                equity += equity_delta
                trades.append(trade)
                open_trade = None

        marked_equity = equity
        if open_trade is not None:
            direction_sign = Decimal("1") if open_trade["direction"] == "LONG" else Decimal("-1")
            marked_equity += (
                direction_sign * open_trade["quantity"] * (bar.close - open_trade["entry_price"])
            )
        peak = max(peak, marked_equity)
        drawdown = marked_equity / peak - 1 if peak > 0 else Decimal("-1")
        equity_curve.append((bar.end_ms, float(marked_equity)))
        drawdown_curve.append((bar.end_ms, float(drawdown)))

    if open_trade is not None:
        last = bars[end_index - 1]
        raw_exit = last.close
        trade, equity_delta = _complete_open_trade(
            open_trade,
            bars,
            last,
            end_index - 1,
            ("END_OF_DATA", raw_exit, False),
            fee_rate,
            slippage_rate,
            symbol,
            timeframe_minutes,
        )
        equity += equity_delta
        trades.append(trade)
        equity_curve[-1] = (last.end_ms, float(equity))
        peak = max(value for _, value in equity_curve)
        drawdown_curve[-1] = (
            last.end_ms,
            float(equity) / peak - 1 if peak > 0 else -1.0,
        )

    return _summary(
        bars[start_index:end_index],
        execution.initial_equity,
        float(equity),
        trades,
        equity_curve,
        drawdown_curve,
        exposure_bars,
        ambiguous_bars,
        capped_trades,
        funding_paid,
    )


def monte_carlo(
    trades: tuple[DivergenceTrade, ...],
    *,
    simulations: int = 10_000,
    seed: int = 20260828,
    ruin_threshold: float = 0.1,
) -> dict[str, float | int | None]:
    if simulations < 1:
        raise ValueError("simulations must be positive")
    if not trades:
        return {"simulations": simulations, "trade_count": 0, "median_max_drawdown": None}
    rng = random.Random(seed)
    returns = [trade.pnl_percent for trade in trades]
    drawdowns: list[float] = []
    ruined = 0
    for _ in range(simulations):
        shuffled = returns.copy()
        rng.shuffle(shuffled)
        equity = peak = 1.0
        maximum = 0.0
        path_ruined = False
        for value in shuffled:
            equity *= max(0.0, 1.0 + value)
            peak = max(peak, equity)
            maximum = max(maximum, 1.0 - equity / peak if peak else 1.0)
            path_ruined = path_ruined or equity <= ruin_threshold
        drawdowns.append(maximum)
        ruined += path_ruined
    drawdowns.sort()
    return {
        "simulations": simulations,
        "trade_count": len(trades),
        "median_max_drawdown": _percentile(drawdowns, 0.50),
        "p95_max_drawdown": _percentile(drawdowns, 0.95),
        "p99_max_drawdown": _percentile(drawdowns, 0.99),
        "probability_20pct_drawdown": sum(value >= 0.20 for value in drawdowns) / simulations,
        "probability_30pct_drawdown": sum(value >= 0.30 for value in drawdowns) / simulations,
        "probability_50pct_drawdown": sum(value >= 0.50 for value in drawdowns) / simulations,
        "probability_of_ruin": ruined / simulations,
    }


def bootstrap_expectancy(
    trades: tuple[DivergenceTrade, ...],
    *,
    simulations: int = 10_000,
    seed: int = 20260828,
) -> dict[str, float | int | None]:
    """Bootstrap the mean trade R to quantify expectancy uncertainty."""
    if simulations < 1:
        raise ValueError("simulations must be positive")
    if not trades:
        return {"simulations": simulations, "trade_count": 0, "mean_r": None}
    rng = random.Random(seed)
    values = [trade.r_multiple for trade in trades]
    means = []
    for _ in range(simulations):
        means.append(sum(rng.choice(values) for _ in values) / len(values))
    means.sort()
    return {
        "simulations": simulations,
        "trade_count": len(values),
        "mean_r": sum(values) / len(values),
        "p025_mean_r": _percentile(means, 0.025),
        "p975_mean_r": _percentile(means, 0.975),
        "probability_mean_r_positive": sum(value > 0 for value in means) / simulations,
    }


def score_quintiles(trades: tuple[DivergenceTrade, ...]) -> list[dict]:
    ordered = sorted(trades, key=lambda trade: trade.divergence_score)
    rows = []
    for bucket in range(5):
        start = len(ordered) * bucket // 5
        end = len(ordered) * (bucket + 1) // 5
        sample = ordered[start:end]
        if not sample:
            continue
        wins = [trade for trade in sample if trade.r_multiple > 0]
        rows.append(
            {
                "quintile": f"Q{bucket + 1}",
                "trades": len(sample),
                "score_min": sample[0].divergence_score,
                "score_max": sample[-1].divergence_score,
                "win_rate": len(wins) / len(sample),
                "average_r": sum(trade.r_multiple for trade in sample) / len(sample),
                "expectancy_r": sum(trade.r_multiple for trade in sample) / len(sample),
            }
        )
    return rows


def rolling_period_metrics(
    equity_curve: tuple[tuple[int, float], ...],
    initial_equity: float,
    *,
    window: int = 30,
) -> list[dict[str, float | int | str | None]]:
    """Return causal rolling Sharpe and win rate from completed daily returns."""
    if window < 2:
        raise ValueError("rolling window must be at least two")
    periods = _period_returns(list(equity_curve), "%Y-%m-%d", initial_equity)
    values = [value for _, value in periods]
    rows: list[dict[str, float | int | str | None]] = []
    for end in range(window, len(values) + 1):
        sample = values[end - window : end]
        average = sum(sample) / window
        variance = sum((value - average) ** 2 for value in sample) / (window - 1)
        deviation = math.sqrt(variance)
        rows.append(
            {
                "period_end": periods[end - 1][0],
                "window_days": window,
                "rolling_sharpe": average / deviation * math.sqrt(365) if deviation else None,
                "rolling_win_rate": sum(value > 0 for value in sample) / window,
                "rolling_average_return": average,
            }
        )
    return rows


def r_distribution(trades: tuple[DivergenceTrade, ...]) -> dict:
    """Return reproducible R-multiple quantiles and fixed audit bins."""
    values = sorted(trade.r_multiple for trade in trades)
    if not values:
        return {"trade_count": 0, "quantiles": {}, "bins": []}
    edges = (-float("inf"), -2.0, -1.0, -0.5, 0.0, 1.0, 2.0, float("inf"))
    labels = ("<-2", "-2..-1", "-1..-0.5", "-0.5..0", "0..1", "1..2", ">=2")
    bins = []
    for label, left, right in zip(labels, edges[:-1], edges[1:], strict=True):
        count = sum(left <= value < right for value in values)
        bins.append({"label": label, "count": count, "fraction": count / len(values)})
    return {
        "trade_count": len(values),
        "quantiles": {
            "p01": _percentile(values, 0.01),
            "p05": _percentile(values, 0.05),
            "p25": _percentile(values, 0.25),
            "p50": _percentile(values, 0.50),
            "p75": _percentile(values, 0.75),
            "p95": _percentile(values, 0.95),
            "p99": _percentile(values, 0.99),
        },
        "bins": bins,
    }


def trade_dict(trade: DivergenceTrade) -> dict:
    return asdict(trade)


def _exit_outcome(bar: ResearchBar, trade: dict) -> tuple[str, Decimal, bool] | None:
    low = bar.low
    high = bar.high
    open_price = bar.open
    is_long = trade["direction"] == "LONG"
    stop = trade["stop"]
    take_profit = trade["take_profit"]
    if is_long:
        if open_price <= stop:
            return "STOP_GAP", open_price, False
        if open_price >= take_profit:
            return "TAKE_PROFIT_GAP", open_price, False
        hit_stop = low <= stop
        hit_take_profit = high >= take_profit
    else:
        if open_price >= stop:
            return "STOP_GAP", open_price, False
        if open_price <= take_profit:
            return "TAKE_PROFIT_GAP", open_price, False
        hit_stop = high >= stop
        hit_take_profit = low <= take_profit
    if hit_stop and hit_take_profit:
        return "STOP_AMBIGUOUS", stop, True
    if hit_stop:
        return "STOP", stop, False
    if hit_take_profit:
        return "TAKE_PROFIT", take_profit, False
    return None


def _complete_open_trade(
    open_trade: dict,
    bars: list[ResearchBar],
    bar: ResearchBar,
    index: int,
    outcome: tuple[str, Decimal, bool],
    fee_rate: Decimal,
    slippage_rate: Decimal,
    symbol: str,
    timeframe_minutes: int,
) -> tuple[DivergenceTrade, Decimal]:
    reason, raw_exit, ambiguous = outcome
    direction_sign = Decimal("1") if open_trade["direction"] == "LONG" else Decimal("-1")
    exit_price = raw_exit * (1 - slippage_rate if direction_sign > 0 else 1 + slippage_rate)
    quantity = open_trade["quantity"]
    exit_fee = abs(exit_price * quantity) * fee_rate
    gross = direction_sign * quantity * (exit_price - open_trade["entry_price"])
    pnl = gross - open_trade["entry_fee"] - exit_fee + open_trade["funding"]
    risk_amount = open_trade["risk_amount"]
    signal = open_trade["signal"]
    trade = DivergenceTrade(
        symbol=symbol,
        timeframe_minutes=timeframe_minutes,
        direction=open_trade["direction"],
        signal_at_ms=bars[signal.trigger_index].end_ms,
        entry_at_ms=bars[signal.entry_index].start_ms,
        exit_at_ms=bar.end_ms,
        entry_price=float(open_trade["entry_price"]),
        stop_price=float(open_trade["stop"]),
        take_profit=float(open_trade["take_profit"]),
        exit_price=float(exit_price),
        exit_reason=reason,
        atr=open_trade["atr"],
        macd=open_trade["macd"],
        macd_signal=open_trade["macd_signal"],
        histogram=open_trade["histogram"],
        point_indices=signal.structure.point_indices,
        prices=signal.structure.prices,
        histograms=signal.structure.histograms,
        divergence_score=signal.structure.score,
        quantity=float(quantity),
        risk_fraction=float(open_trade["risk_fraction"]),
        pnl=float(pnl),
        pnl_percent=float(pnl / open_trade["entry_equity"]),
        r_multiple=float(pnl / risk_amount) if risk_amount > 0 else 0.0,
        fees=float(open_trade["entry_fee"] + exit_fee),
        funding=float(open_trade["funding"]),
        slippage_cost=float(open_trade["entry_slippage"] + abs(exit_price - raw_exit) * quantity),
        holding_bars=index - signal.entry_index + 1,
        ambiguous_exit=ambiguous,
        leverage_capped=open_trade["leverage_capped"],
    )
    return trade, gross - exit_fee


def _summary(
    bars: list[ResearchBar],
    initial_equity: float,
    final_equity: float,
    trades: list[DivergenceTrade],
    equity_curve: list[tuple[int, float]],
    drawdown_curve: list[tuple[int, float]],
    exposure_bars: int,
    ambiguous_bars: int,
    capped_trades: int,
    funding_paid: float,
) -> ReplaySummary:
    wins = [trade for trade in trades if trade.pnl > 0]
    losses = [trade for trade in trades if trade.pnl < 0]
    gross_profit = sum(trade.pnl for trade in wins)
    gross_loss = -sum(trade.pnl for trade in losses)
    rs = [trade.r_multiple for trade in trades]
    daily = _period_returns(equity_curve, "%Y-%m-%d", initial_equity)
    monthly = _period_returns(equity_curve, "%Y-%m", initial_equity)
    yearly = _period_returns(equity_curve, "%Y", initial_equity)
    sharpe = _sharpe(tuple(value for _, value in daily), downside=False)
    sortino = _sharpe(tuple(value for _, value in daily), downside=True)
    years = (bars[-1].end_ms - bars[0].start_ms) / (365.2425 * 86_400_000) if bars else 0
    cagr = (
        (final_equity / initial_equity) ** (1 / years) - 1
        if years > 0 and final_equity > 0
        else None
    )
    maximum_drawdown = min((value for _, value in drawdown_curve), default=0.0)
    return ReplaySummary(
        initial_equity=initial_equity,
        final_equity=final_equity,
        net_return=final_equity / initial_equity - 1,
        total_trades=len(trades),
        win_rate=len(wins) / len(trades) if trades else None,
        average_win_r=sum(trade.r_multiple for trade in wins) / len(wins) if wins else None,
        average_loss_r=sum(trade.r_multiple for trade in losses) / len(losses) if losses else None,
        average_r=sum(rs) / len(rs) if rs else None,
        expectancy_r=sum(rs) / len(rs) if rs else None,
        profit_factor=gross_profit / gross_loss if gross_loss else None,
        sharpe=sharpe,
        sortino=sortino,
        cagr=cagr,
        max_drawdown=maximum_drawdown,
        calmar=cagr / abs(maximum_drawdown) if cagr is not None and maximum_drawdown < 0 else None,
        longest_losing_streak=_longest_streak(trades, winning=False),
        longest_winning_streak=_longest_streak(trades, winning=True),
        average_holding_bars=(
            sum(trade.holding_bars for trade in trades) / len(trades) if trades else None
        ),
        median_holding_bars=median(trade.holding_bars for trade in trades) if trades else None,
        exposure=exposure_bars / len(bars) if bars else 0.0,
        fees_paid=sum(trade.fees for trade in trades),
        funding_paid=funding_paid,
        slippage_cost=sum(trade.slippage_cost for trade in trades),
        ambiguous_bars=ambiguous_bars,
        leverage_capped_trades=capped_trades,
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
        drawdown_curve=tuple(drawdown_curve),
        monthly_returns=monthly,
        yearly_returns=yearly,
    )


def _period_returns(
    equity_curve: list[tuple[int, float]], format_string: str, initial_equity: float
) -> tuple[tuple[str, float], ...]:
    closes: dict[str, float] = {}
    for timestamp_ms, equity in equity_curve:
        key = datetime.fromtimestamp(timestamp_ms / 1000, UTC).strftime(format_string)
        closes[key] = equity
    result = []
    previous = initial_equity
    for key, equity in closes.items():
        if previous != 0:
            result.append((key, equity / previous - 1))
        previous = equity
    return tuple(result)


def _funding_by_bar(bars: list[ResearchBar], funding: list[FundingRate]) -> list[list[FundingRate]]:
    """Assign each funding event to its containing bar without reordering time."""
    grouped: list[list[FundingRate]] = [[] for _ in bars]
    if not bars or not funding:
        return grouped
    bar_ends = [bar.end_ms for bar in bars]
    for event in funding:
        index = bisect_left(bar_ends, event.timestamp_ms)
        if index < len(bars) and bars[index].start_ms <= event.timestamp_ms <= bars[index].end_ms:
            grouped[index].append(event)
    return grouped


def _sharpe(values: tuple[float, ...], *, downside: bool) -> float | None:
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    deviations = [min(value, 0.0) for value in values] if downside else list(values)
    variance = sum((value - (0.0 if downside else average)) ** 2 for value in deviations) / (
        len(deviations) - 1
    )
    deviation = math.sqrt(variance)
    return average / deviation * math.sqrt(365) if deviation > 0 else None


def _longest_streak(trades: list[DivergenceTrade], *, winning: bool) -> int:
    longest = current = 0
    for trade in trades:
        matches = trade.pnl > 0 if winning else trade.pnl < 0
        current = current + 1 if matches else 0
        longest = max(longest, current)
    return longest


def _strictly_increasing(values: tuple[float, ...]) -> bool:
    return all(left < right for left, right in zip(values, values[1:], strict=False))


def _strictly_decreasing(values: tuple[float, ...]) -> bool:
    return all(left > right for left, right in zip(values, values[1:], strict=False))


def _floor_quantity(value: Decimal) -> Decimal:
    return (value / Decimal("0.000001")).to_integral_value(rounding=ROUND_DOWN) * Decimal(
        "0.000001"
    )


def _validate_execution(config: ExecutionConfig) -> None:
    if (
        config.stop_atr <= 0
        or config.reward_risk <= 0
        or not 0 < config.risk_fraction <= 1
        or config.fee_bps < 0
        or config.slippage_bps < 0
        or config.initial_equity <= 0
        or config.max_leverage is not None
        and config.max_leverage <= 0
    ):
        raise ValueError("invalid divergence execution configuration")


def _validate_filter(config: SignalFilterConfig) -> None:
    if (
        config.trend_period < 1
        or config.rsi_period < 1
        or config.rsi_long_max is not None
        and not 0 < config.rsi_long_max < 100
        or config.rsi_short_min is not None
        and not 0 < config.rsi_short_min < 100
        or config.atr_percentile is not None
        and not 0 <= config.atr_percentile <= 1
        or config.atr_percentile_window < 2
        or config.volume_mean_window is not None
        and config.volume_mean_window < 1
        or config.minimum_histogram_atr is not None
        and config.minimum_histogram_atr < 0
    ):
        raise ValueError("invalid divergence signal filter configuration")


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate percentile of empty values")
    index = min(len(values) - 1, max(0, math.ceil(probability * len(values)) - 1))
    return values[index]
