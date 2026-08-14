"""Research-only equal-notional BTC/ETH pair strategy replay."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class PairBar:
    timestamp_ms: int
    end_ms: int
    left: ResearchBar
    right: ResearchBar


@dataclass(frozen=True)
class PairTrade:
    direction: str
    entry_at_ms: int
    exit_at_ms: int
    left_entry: Decimal
    left_exit: Decimal
    right_entry: Decimal
    right_exit: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal


@dataclass(frozen=True)
class PairResult:
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
    trades: tuple[PairTrade, ...]


def align_pair_bars(
    left: list[ResearchBar], right: list[ResearchBar]
) -> list[PairBar]:
    if len(left) != len(right):
        raise ValueError("pair bars have different lengths")
    result = []
    for left_bar, right_bar in zip(left, right, strict=True):
        if left_bar.start_ms != right_bar.start_ms or left_bar.end_ms != right_bar.end_ms:
            raise ValueError("pair bars are not aligned")
        result.append(
            PairBar(
                timestamp_ms=left_bar.start_ms,
                end_ms=left_bar.end_ms,
                left=left_bar,
                right=right_bar,
            )
        )
    return result


def ratio_ema_targets(
    bars: list[PairBar], fast_period: int, slow_period: int
) -> tuple[int | None, ...]:
    if fast_period < 1 or fast_period >= slow_period:
        raise ValueError("pair EMA periods must satisfy 1 <= fast < slow")
    fast_alpha = Decimal("2") / Decimal(fast_period + 1)
    slow_alpha = Decimal("2") / Decimal(slow_period + 1)
    fast = slow = None
    targets: list[int | None] = []
    for index, bar in enumerate(bars):
        ratio = bar.left.close / bar.right.close
        fast = ratio if fast is None else fast + fast_alpha * (ratio - fast)
        slow = ratio if slow is None else slow + slow_alpha * (ratio - slow)
        if index + 1 < slow_period:
            targets.append(None)
        elif fast > slow:
            targets.append(1)
        elif fast < slow:
            targets.append(-1)
        else:
            targets.append(0)
    return tuple(targets)


def ratio_momentum_targets(
    bars: list[PairBar], lookback: int, threshold: float
) -> tuple[int | None, ...]:
    if lookback < 1 or threshold < 0:
        raise ValueError("pair momentum parameters are invalid")
    boundary = Decimal(str(threshold))
    ratios = [bar.left.close / bar.right.close for bar in bars]
    targets: list[int | None] = []
    for index, ratio in enumerate(ratios):
        if index < lookback:
            targets.append(None)
            continue
        change = ratio / ratios[index - lookback] - Decimal("1")
        targets.append(1 if change > boundary else -1 if change < -boundary else 0)
    return tuple(targets)


def ratio_mean_reversion_targets(
    bars: list[PairBar], window: int, entry_z: float, exit_z: float
) -> tuple[int | None, ...]:
    if window < 2 or entry_z <= exit_z or exit_z < 0:
        raise ValueError("pair mean-reversion parameters are invalid")
    log_ratios = [
        math.log(float(bar.left.close / bar.right.close))
        for bar in bars
    ]
    target = 0
    targets: list[int | None] = []
    for index in range(len(bars)):
        if index < window:
            targets.append(None)
            continue
        sample = log_ratios[index - window : index]
        mean = sum(sample) / window
        variance = sum((value - mean) ** 2 for value in sample) / window
        deviation = math.sqrt(variance)
        z_score = (log_ratios[index] - mean) / deviation if deviation else 0.0
        if target and abs(z_score) <= exit_z:
            target = 0
        if target == 0:
            if z_score <= -entry_z:
                target = 1
            elif z_score >= entry_z:
                target = -1
        targets.append(target)
    return tuple(targets)


def evaluate_pair_targets(
    bars: list[PairBar],
    targets: tuple[int | None, ...],
    funding_left: list[list[FundingRate]],
    funding_right: list[list[FundingRate]],
    *,
    start_ms: int,
    end_ms: int,
    exposure: float = 1.0,
    initial_equity: Decimal = Decimal("100000"),
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
) -> PairResult:
    if len(targets) != len(bars):
        raise ValueError("pair target and bar lengths differ")
    if len(funding_left) != len(bars) or len(funding_right) != len(bars):
        raise ValueError("pair funding and bar lengths differ")
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.timestamp_ms <= end_ms]
    if not selected:
        raise ValueError("no pair bars in requested range")
    fee_rate = fee_bps / Decimal("10000")
    slippage_rate = slippage_bps / Decimal("10000")
    gross_exposure = Decimal(str(exposure))
    cash = initial_equity
    left_position = right_position = Decimal("0")
    left_entry = right_entry = Decimal("0")
    entry_at_ms = 0
    entry_fee = Decimal("0")
    trade_funding = Decimal("0")
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    peak_equity = initial_equity
    max_drawdown = Decimal("0")
    trades: list[PairTrade] = []
    daily_equity: dict[str, Decimal] = {}
    bankrupt = False
    previous_index = selected[0] - 1
    pending_target = targets[previous_index] if previous_index >= 0 else 0
    pending_target = pending_target if pending_target is not None else 0

    def close(bar: PairBar, *, at_close: bool = False) -> None:
        nonlocal cash, left_position, right_position, left_entry, right_entry
        nonlocal entry_fee, trade_funding, total_fees
        if left_position == 0 and right_position == 0:
            return
        left_market = bar.left.close if at_close else bar.left.open
        right_market = bar.right.close if at_close else bar.right.open
        left_fill = left_market * (
            Decimal("1") - slippage_rate
            if left_position > 0
            else Decimal("1") + slippage_rate
        )
        right_fill = right_market * (
            Decimal("1") - slippage_rate
            if right_position > 0
            else Decimal("1") + slippage_rate
        )
        left_fee = abs(left_position) * left_fill * fee_rate
        right_fee = abs(right_position) * right_fill * fee_rate
        exit_fee = left_fee + right_fee
        gross = left_position * (left_fill - left_entry) + right_position * (
            right_fill - right_entry
        )
        cash += gross - exit_fee
        total_fees += exit_fee
        trades.append(
            PairTrade(
                direction="BTC_LONG_ETH_SHORT" if left_position > 0 else "BTC_SHORT_ETH_LONG",
                entry_at_ms=entry_at_ms,
                exit_at_ms=bar.end_ms if at_close else bar.timestamp_ms,
                left_entry=left_entry,
                left_exit=left_fill,
                right_entry=right_entry,
                right_exit=right_fill,
                fees=entry_fee + exit_fee,
                funding=trade_funding,
                net_pnl=gross - entry_fee - exit_fee + trade_funding,
            )
        )
        left_position = right_position = Decimal("0")
        left_entry = right_entry = Decimal("0")
        entry_fee = Decimal("0")
        trade_funding = Decimal("0")

    def open_position(target: int, bar: PairBar) -> None:
        nonlocal cash, left_position, right_position, left_entry, right_entry
        nonlocal entry_at_ms, entry_fee, total_fees
        if target == 0 or cash <= 0:
            return
        notional = cash * gross_exposure / Decimal("2")
        left_long = target > 0
        left_fill = bar.left.open * (
            Decimal("1") + slippage_rate if left_long else Decimal("1") - slippage_rate
        )
        right_fill = bar.right.open * (
            Decimal("1") - slippage_rate if left_long else Decimal("1") + slippage_rate
        )
        left_quantity = _floor_step(notional / left_fill, Decimal("0.001"))
        right_quantity = _floor_step(notional / right_fill, Decimal("0.001"))
        left_fee = left_quantity * left_fill * fee_rate
        right_fee = right_quantity * right_fill * fee_rate
        fee = left_fee + right_fee
        if left_quantity <= 0 or right_quantity <= 0 or fee >= cash:
            return
        cash -= fee
        total_fees += fee
        left_position = left_quantity if left_long else -left_quantity
        right_position = -right_quantity if left_long else right_quantity
        left_entry = left_fill
        right_entry = right_fill
        entry_at_ms = bar.timestamp_ms
        entry_fee = fee

    last_index = selected[-1]
    for index in selected:
        bar = bars[index]
        current_target = 1 if left_position > 0 else -1 if left_position < 0 else 0
        if pending_target != current_target:
            if current_target:
                close(bar)
            open_position(pending_target, bar)
        for event, position in [
            *[(event, left_position) for event in funding_left[index]],
            *[(event, right_position) for event in funding_right[index]],
        ]:
            if position:
                amount = -(position * event.mark_price * event.rate)
                cash += amount
                total_funding += amount
                trade_funding += amount
        equity = cash + left_position * (bar.left.close - left_entry)
        equity += right_position * (bar.right.close - right_entry)
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
    if (left_position or right_position) and not bankrupt:
        close(final_bar, at_close=True)
        daily_equity[_utc_date(final_bar.end_ms)] = cash
        peak_equity = max(peak_equity, cash)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, cash / peak_equity - Decimal("1"))
    final_equity = cash if not bankrupt else daily_equity[_utc_date(final_bar.end_ms)]
    wins = sum(item.net_pnl > 0 for item in trades)
    gross_profit = sum((item.net_pnl for item in trades if item.net_pnl > 0), Decimal("0"))
    gross_loss = -sum((item.net_pnl for item in trades if item.net_pnl < 0), Decimal("0"))
    daily = _period_returns(daily_equity, initial_equity, 10)
    monthly = _period_returns(daily_equity, initial_equity, 7)
    return PairResult(
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
        daily_returns=daily,
        monthly_returns=monthly,
        trades=tuple(trades),
    )


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
        result.append((label, float(equity / previous - Decimal("1")) if previous else -1.0))
        previous = equity
    return tuple(result)
