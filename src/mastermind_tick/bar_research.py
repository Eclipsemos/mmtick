"""Research-only closed-bar strategy signals and execution replay."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class ResearchBar:
    start_ms: int
    end_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")


@dataclass(frozen=True)
class ResearchTrade:
    direction: str
    entry_at_ms: int
    exit_at_ms: int
    entry_price: Decimal
    exit_price: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal


@dataclass(frozen=True)
class ResearchResult:
    exposure: float
    initial_equity: float
    final_equity: float
    net_return: float
    max_drawdown: float
    completed_trades: int
    win_rate: float | None
    profit_factor: float | None
    total_fees: float
    total_funding: float
    bankrupt: bool
    daily_returns: tuple[tuple[str, float], ...]
    monthly_returns: tuple[tuple[str, float], ...]
    trades: tuple[ResearchTrade, ...]


def aggregate_bars(bars: list[ResearchBar], interval_minutes: int) -> list[ResearchBar]:
    if interval_minutes < 15 or interval_minutes % 15:
        raise ValueError("research interval must be a multiple of 15 minutes")
    interval_ms = interval_minutes * 60_000
    expected_count = interval_minutes // 15
    result: list[ResearchBar] = []
    group: list[ResearchBar] = []
    current_bucket: int | None = None

    def finish() -> None:
        if (
            len(group) == expected_count
            and group[0].start_ms == current_bucket
            and all(
                right.start_ms - left.start_ms == 15 * 60_000
                for left, right in zip(group, group[1:], strict=False)
            )
        ):
            result.append(
                ResearchBar(
                    start_ms=group[0].start_ms,
                    end_ms=group[-1].end_ms,
                    open=group[0].open,
                    high=max(item.high for item in group),
                    low=min(item.low for item in group),
                    close=group[-1].close,
                    volume=sum((item.volume for item in group), Decimal("0")),
                )
            )

    for bar in bars:
        bucket = bar.start_ms // interval_ms * interval_ms
        if current_bucket is None or bucket != current_bucket:
            if group:
                finish()
            group = [bar]
            current_bucket = bucket
        else:
            group.append(bar)
    if group:
        finish()
    return result


def funding_by_bar(
    bars: list[ResearchBar], funding_rates: list[FundingRate]
) -> list[list[FundingRate]]:
    bar_ends = [bar.end_ms for bar in bars]
    grouped: list[list[FundingRate]] = [[] for _ in bars]
    for event in funding_rates:
        index = bisect.bisect_left(bar_ends, event.timestamp_ms)
        if index < len(bars) and bars[index].start_ms <= event.timestamp_ms:
            grouped[index].append(event)
    return grouped


def buy_and_hold_targets(bars: list[ResearchBar]) -> tuple[int, ...]:
    return tuple(1 for _ in bars)


def ema_targets(
    bars: list[ResearchBar],
    fast_period: int,
    slow_period: int,
    direction: str,
    minimum_separation: float = 0.0,
) -> tuple[int | None, ...]:
    _validate_direction(direction)
    if fast_period < 1 or fast_period >= slow_period:
        raise ValueError("EMA periods must satisfy 1 <= fast < slow")
    if minimum_separation < 0:
        raise ValueError("EMA minimum separation must be non-negative")
    fast_alpha = Decimal("2") / Decimal(fast_period + 1)
    slow_alpha = Decimal("2") / Decimal(slow_period + 1)
    separation = Decimal(str(minimum_separation))
    fast = slow = None
    targets: list[int | None] = []
    for index, bar in enumerate(bars):
        fast = bar.close if fast is None else fast + fast_alpha * (bar.close - fast)
        slow = bar.close if slow is None else slow + slow_alpha * (bar.close - slow)
        if index + 1 < slow_period:
            targets.append(None)
        elif fast > slow * (Decimal("1") + separation):
            targets.append(1)
        elif direction == "long_short" and fast < slow * (Decimal("1") - separation):
            targets.append(-1)
        else:
            targets.append(0)
    return tuple(targets)


def momentum_targets(
    bars: list[ResearchBar], lookback: int, threshold: float, direction: str
) -> tuple[int | None, ...]:
    _validate_direction(direction)
    if lookback < 1 or threshold < 0:
        raise ValueError("momentum lookback must be positive and threshold non-negative")
    boundary = Decimal(str(threshold))
    targets: list[int | None] = []
    for index, bar in enumerate(bars):
        if index < lookback:
            targets.append(None)
            continue
        change = bar.close / bars[index - lookback].close - Decimal("1")
        if change > boundary:
            targets.append(1)
        elif direction == "long_short" and change < -boundary:
            targets.append(-1)
        else:
            targets.append(0)
    return tuple(targets)


def donchian_targets(
    bars: list[ResearchBar], entry_window: int, exit_window: int, direction: str
) -> tuple[int | None, ...]:
    _validate_direction(direction)
    if exit_window < 1 or entry_window <= exit_window:
        raise ValueError("Donchian windows must satisfy 1 <= exit < entry")
    target = 0
    targets: list[int | None] = []
    for index, bar in enumerate(bars):
        if index < entry_window:
            targets.append(None)
            continue
        entry_bars = bars[index - entry_window : index]
        exit_bars = bars[index - exit_window : index]
        prior_high = max(item.high for item in entry_bars)
        prior_low = min(item.low for item in entry_bars)
        exit_high = max(item.high for item in exit_bars)
        exit_low = min(item.low for item in exit_bars)
        if target > 0 and bar.close < exit_low:
            target = 0
        elif target < 0 and bar.close > exit_high:
            target = 0
        if bar.close > prior_high:
            target = 1
        elif direction == "long_short" and bar.close < prior_low:
            target = -1
        targets.append(target)
    return tuple(targets)


def rsi_reversion_targets(
    bars: list[ResearchBar], period: int, lower: float, upper: float, direction: str
) -> tuple[int | None, ...]:
    _validate_direction(direction)
    if period < 2 or not 0 < lower < 50 < upper < 100:
        raise ValueError("invalid RSI parameters")
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    average_gain: Decimal | None = None
    average_loss: Decimal | None = None
    target = 0
    targets: list[int | None] = [None]
    for index in range(1, len(bars)):
        change = bars[index].close - bars[index - 1].close
        gain = max(change, Decimal("0"))
        loss = max(-change, Decimal("0"))
        if index <= period:
            gains.append(gain)
            losses.append(loss)
            if index < period:
                targets.append(None)
                continue
            average_gain = sum(gains, Decimal("0")) / Decimal(period)
            average_loss = sum(losses, Decimal("0")) / Decimal(period)
        else:
            assert average_gain is not None and average_loss is not None
            average_gain = (average_gain * Decimal(period - 1) + gain) / Decimal(period)
            average_loss = (average_loss * Decimal(period - 1) + loss) / Decimal(period)
        rsi = _rsi(average_gain, average_loss)
        if target > 0 and rsi >= Decimal("50"):
            target = 0
        elif target < 0 and rsi <= Decimal("50"):
            target = 0
        if target == 0 and rsi <= Decimal(str(lower)):
            target = 1
        elif target == 0 and direction == "long_short" and rsi >= Decimal(str(upper)):
            target = -1
        targets.append(target)
    return tuple(targets)


def evaluate_targets(
    bars: list[ResearchBar],
    targets: tuple[int | None, ...],
    *,
    start_ms: int,
    end_ms: int,
    funding: list[list[FundingRate]] | None = None,
    exposure: float = 1.0,
    initial_equity: Decimal = Decimal("100000"),
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
    quantity_step: Decimal = Decimal("0.001"),
) -> ResearchResult:
    if len(targets) != len(bars):
        raise ValueError("target and bar lengths differ")
    if exposure <= 0:
        raise ValueError("exposure must be positive")
    if any(value not in {-1, 0, 1, None} for value in targets):
        raise ValueError("targets must be -1, 0, 1, or None")
    if funding is None:
        funding = [[] for _ in bars]
    if len(funding) != len(bars):
        raise ValueError("funding and bar lengths differ")
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        raise ValueError("no bars in requested range")

    fee_rate = fee_bps / Decimal("10000")
    slippage_rate = slippage_bps / Decimal("10000")
    exposure_value = Decimal(str(exposure))
    cash = initial_equity
    position = Decimal("0")
    entry_price = Decimal("0")
    entry_at_ms = 0
    entry_fee = Decimal("0")
    trade_funding = Decimal("0")
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    peak_equity = initial_equity
    max_drawdown = Decimal("0")
    trades: list[ResearchTrade] = []
    daily_equity: dict[str, Decimal] = {}
    bankrupt = False
    previous_index = selected[0] - 1
    pending_target = targets[previous_index] if previous_index >= 0 else 0
    pending_target = pending_target if pending_target is not None else 0

    def close(market_price: Decimal, timestamp_ms: int) -> None:
        nonlocal cash, position, entry_price, entry_fee, trade_funding, total_fees
        if position == 0:
            return
        fill = market_price * (
            Decimal("1") - slippage_rate if position > 0 else Decimal("1") + slippage_rate
        )
        exit_fee = abs(position) * fill * fee_rate
        gross = position * (fill - entry_price)
        cash += gross - exit_fee
        total_fees += exit_fee
        trades.append(
            ResearchTrade(
                direction="LONG" if position > 0 else "SHORT",
                entry_at_ms=entry_at_ms,
                exit_at_ms=timestamp_ms,
                entry_price=entry_price,
                exit_price=fill,
                fees=entry_fee + exit_fee,
                funding=trade_funding,
                net_pnl=gross - entry_fee - exit_fee + trade_funding,
            )
        )
        position = Decimal("0")
        entry_price = Decimal("0")
        entry_fee = Decimal("0")
        trade_funding = Decimal("0")

    def open_position(target: int, market_price: Decimal, timestamp_ms: int) -> None:
        nonlocal cash, position, entry_price, entry_at_ms, entry_fee, total_fees
        if target == 0 or cash <= 0:
            return
        fill = market_price * (
            Decimal("1") + slippage_rate if target > 0 else Decimal("1") - slippage_rate
        )
        quantity = _floor_step(cash * exposure_value / fill, quantity_step)
        fee = quantity * fill * fee_rate
        if quantity <= 0 or fee >= cash:
            return
        cash -= fee
        total_fees += fee
        position = quantity if target > 0 else -quantity
        entry_price = fill
        entry_at_ms = timestamp_ms
        entry_fee = fee

    last_index = selected[-1]
    for index in selected:
        bar = bars[index]
        current_target = 1 if position > 0 else -1 if position < 0 else 0
        if pending_target != current_target:
            close(bar.open, bar.start_ms)
            open_position(pending_target, bar.open, bar.start_ms)
        for event in funding[index]:
            if position:
                amount = -(position * event.mark_price * event.rate)
                cash += amount
                total_funding += amount
                trade_funding += amount
        equity = cash + position * (bar.close - entry_price)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, equity / peak_equity - Decimal("1"))
        daily_equity[_utc_date(bar.end_ms)] = equity
        if equity <= 0:
            bankrupt = True
            last_index = index
            break
        signal = targets[index]
        if signal is not None:
            pending_target = signal

    final_bar = bars[last_index]
    if position and not bankrupt:
        close(final_bar.close, final_bar.end_ms)
        daily_equity[_utc_date(final_bar.end_ms)] = cash
        peak_equity = max(peak_equity, cash)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, cash / peak_equity - Decimal("1"))
    final_equity = cash if not bankrupt else daily_equity[_utc_date(final_bar.end_ms)]
    daily_returns = _period_returns(daily_equity, initial_equity, 10)
    monthly_returns = _period_returns(daily_equity, initial_equity, 7)
    wins = sum(item.net_pnl > 0 for item in trades)
    gross_profit = sum((item.net_pnl for item in trades if item.net_pnl > 0), Decimal("0"))
    gross_loss = -sum((item.net_pnl for item in trades if item.net_pnl < 0), Decimal("0"))
    return ResearchResult(
        exposure=exposure,
        initial_equity=float(initial_equity),
        final_equity=float(final_equity),
        net_return=float(final_equity / initial_equity - Decimal("1")),
        max_drawdown=float(max_drawdown),
        completed_trades=len(trades),
        win_rate=wins / len(trades) if trades else None,
        profit_factor=float(gross_profit / gross_loss) if gross_loss else None,
        total_fees=float(total_fees),
        total_funding=float(total_funding),
        bankrupt=bankrupt,
        daily_returns=daily_returns,
        monthly_returns=monthly_returns,
        trades=tuple(trades),
    )


def _rsi(average_gain: Decimal, average_loss: Decimal) -> Decimal:
    if average_loss == 0:
        return Decimal("100") if average_gain > 0 else Decimal("50")
    relative_strength = average_gain / average_loss
    return Decimal("100") - Decimal("100") / (Decimal("1") + relative_strength)


def _validate_direction(direction: str) -> None:
    if direction not in {"long_only", "long_short"}:
        raise ValueError(f"unsupported direction: {direction}")


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _utc_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).date().isoformat()


def _period_returns(
    daily_equity: dict[str, Decimal], initial_equity: Decimal, label_length: int
) -> tuple[tuple[str, float], ...]:
    period_ends: dict[str, Decimal] = {}
    for day, equity in daily_equity.items():
        period_ends[day[:label_length]] = equity
    previous = initial_equity
    result = []
    for label, equity in period_ends.items():
        value = float(equity / previous - Decimal("1")) if previous else math.nan
        result.append((label, value))
        previous = equity
    return tuple(result)
