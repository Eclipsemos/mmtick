"""Causal BTC/ETH regime and relative-strength portfolio research."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from mastermind_tick.models import FundingRate
from mastermind_tick.pair_research import PairBar

WeightTarget = tuple[Decimal, Decimal]


@dataclass(frozen=True)
class CrossAssetCandidate:
    interval_minutes: int
    lookback_days: int
    feature_set: str
    family: str
    direction: str
    threshold: Decimal
    minimum_hold_days: int
    adaptation_days: int = 0

    @property
    def lookback_bars(self) -> int:
        return self.lookback_days * 1440 // self.interval_minutes

    @property
    def minimum_hold_bars(self) -> int:
        return self.minimum_hold_days * 1440 // self.interval_minutes

    @property
    def id(self) -> str:
        threshold = f"{self.threshold:g}".replace(".", "p")
        return (
            f"{self.interval_minutes}m-{self.family}-{self.direction}-{self.feature_set}"
            f"-lookback-{self.lookback_days}d-threshold-{threshold}"
            f"-hold-{self.minimum_hold_days}d-adapt-{self.adaptation_days}d"
        )

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, **asdict(self), "threshold": float(self.threshold)}


@dataclass(frozen=True)
class PortfolioTrade:
    entry_at_ms: int
    exit_at_ms: int
    target: WeightTarget
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal


@dataclass(frozen=True)
class PortfolioResult:
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
    yearly_returns: tuple[tuple[str, float], ...]
    trades: tuple[PortfolioTrade, ...]


def candidate_library() -> tuple[CrossAssetCandidate, ...]:
    families = (
        ("common_trend", "long_only"),
        ("common_trend", "long_short"),
        ("dual_trend", "long_only"),
        ("dual_trend", "long_short"),
        ("rotation", "long_only"),
        ("rotation", "long_short"),
        ("relative_value", "long_short"),
        ("relative_reversion", "long_short"),
    )
    static = tuple(
        CrossAssetCandidate(interval, lookback, feature_set, family, direction, threshold, hold)
        for interval in (240, 1440)
        for lookback in (3, 7, 21, 63)
        for feature_set in (
            "momentum",
            "momentum_breakout",
            "momentum_carry",
            "momentum_defensive",
            "all_equal",
        )
        for family, direction in families
        for threshold in (Decimal("0"), Decimal("0.5"), Decimal("1"))
        for hold in (0, 1, 3)
    )
    adaptive = tuple(
        CrossAssetCandidate(
            interval,
            lookback,
            feature_set,
            "relative_adaptive",
            "long_short",
            threshold,
            hold,
            adaptation,
        )
        for interval in (240, 1440)
        for lookback in (3, 7, 21, 63)
        for feature_set in (
            "momentum",
            "momentum_breakout",
            "momentum_carry",
            "momentum_defensive",
            "all_equal",
        )
        for threshold in (Decimal("0"), Decimal("0.5"), Decimal("1"))
        for hold in (0, 1, 3)
        for adaptation in (21, 63, 126)
    )
    return static + adaptive


def causal_asset_scores(
    bars: list[PairBar],
    funding_left: list[list[FundingRate]],
    funding_right: list[list[FundingRate]],
    *,
    lookback: int,
    normalization_window: int,
    feature_set: str,
) -> tuple[tuple[Decimal | None, ...], tuple[Decimal | None, ...]]:
    """Build factor scores using data available through each bar close only."""
    if len(bars) != len(funding_left) or len(bars) != len(funding_right):
        raise ValueError("bars and funding lengths must match")
    if lookback < 2 or normalization_window < lookback:
        raise ValueError("invalid cross-asset feature windows")
    if feature_set not in {
        "momentum",
        "momentum_breakout",
        "momentum_carry",
        "momentum_defensive",
        "all_equal",
    }:
        raise ValueError(f"unsupported cross-asset feature set: {feature_set}")

    left_raw = _raw_features(bars, funding_left, lookback, left=True)
    right_raw = _raw_features(bars, funding_right, lookback, left=False)
    left_z = {
        name: _causal_zscore(values, normalization_window) for name, values in left_raw.items()
    }
    right_z = {
        name: _causal_zscore(values, normalization_window) for name, values in right_raw.items()
    }
    return (
        _combine_feature_set(left_z, feature_set),
        _combine_feature_set(right_z, feature_set),
    )


def factor_targets(
    left_scores: tuple[Decimal | None, ...],
    right_scores: tuple[Decimal | None, ...],
    candidate: CrossAssetCandidate,
    bars: list[PairBar] | None = None,
) -> tuple[WeightTarget | None, ...]:
    if len(left_scores) != len(right_scores):
        raise ValueError("cross-asset score lengths differ")
    if candidate.direction not in {"long_only", "long_short"}:
        raise ValueError("unsupported cross-asset direction")
    if candidate.threshold < 0:
        raise ValueError("cross-asset threshold must be non-negative")

    efficacy = (
        _causal_relative_efficacy(left_scores, right_scores, bars, candidate)
        if candidate.family == "relative_adaptive"
        else None
    )
    desired: list[WeightTarget | None] = []
    for index, (left, right) in enumerate(zip(left_scores, right_scores, strict=True)):
        if left is None or right is None:
            desired.append(None)
            continue
        target = _desired_target(left, right, candidate)
        if efficacy is not None:
            regime = efficacy[index]
            if regime is None:
                desired.append(None)
                continue
            target = (target[0] * regime, target[1] * regime)
        desired.append(target)
    return _apply_minimum_hold(tuple(desired), candidate.minimum_hold_bars)


def evaluate_portfolio_targets(
    bars: list[PairBar],
    targets: tuple[WeightTarget | None, ...],
    funding_left: list[list[FundingRate]],
    funding_right: list[list[FundingRate]],
    *,
    start_ms: int,
    end_ms: int,
    exposure: float = 1.0,
    initial_equity: Decimal = Decimal("100000"),
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
) -> PortfolioResult:
    if len(targets) != len(bars):
        raise ValueError("portfolio target and bar lengths differ")
    if len(funding_left) != len(bars) or len(funding_right) != len(bars):
        raise ValueError("portfolio funding and bar lengths differ")
    if exposure <= 0:
        raise ValueError("portfolio exposure must be positive")
    if any(
        target is not None and sum((abs(weight) for weight in target), Decimal("0")) > 1
        for target in targets
    ):
        raise ValueError("portfolio target gross weight cannot exceed one")
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.timestamp_ms <= end_ms]
    if not selected:
        raise ValueError("no portfolio bars in requested range")

    fee_rate = fee_bps / Decimal("10000")
    slippage_rate = slippage_bps / Decimal("10000")
    exposure_value = Decimal(str(exposure))
    cash = initial_equity
    positions = [Decimal("0"), Decimal("0")]
    entries = [Decimal("0"), Decimal("0")]
    active_target: WeightTarget = (Decimal("0"), Decimal("0"))
    entry_at_ms = 0
    entry_fee = Decimal("0")
    trade_funding = Decimal("0")
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    peak_equity = initial_equity
    max_drawdown = Decimal("0")
    trades: list[PortfolioTrade] = []
    daily_equity: dict[str, Decimal] = {}
    bankrupt = False
    previous_index = selected[0] - 1
    pending_target = targets[previous_index] if previous_index >= 0 else None
    pending_target = pending_target or (Decimal("0"), Decimal("0"))

    def close_positions(bar: PairBar, *, at_close: bool = False) -> None:
        nonlocal cash, active_target, entry_fee, trade_funding, total_fees
        if not any(positions):
            active_target = (Decimal("0"), Decimal("0"))
            return
        markets = (bar.left.close, bar.right.close) if at_close else (bar.left.open, bar.right.open)
        gross = Decimal("0")
        exit_fee = Decimal("0")
        for index, (position, entry, market) in enumerate(
            zip(positions, entries, markets, strict=True)
        ):
            if not position:
                continue
            fill = market * (
                Decimal("1") - slippage_rate if position > 0 else Decimal("1") + slippage_rate
            )
            gross += position * (fill - entry)
            exit_fee += abs(position) * fill * fee_rate
            positions[index] = Decimal("0")
            entries[index] = Decimal("0")
        cash += gross - exit_fee
        total_fees += exit_fee
        trades.append(
            PortfolioTrade(
                entry_at_ms=entry_at_ms,
                exit_at_ms=bar.end_ms if at_close else bar.timestamp_ms,
                target=active_target,
                fees=entry_fee + exit_fee,
                funding=trade_funding,
                net_pnl=gross - entry_fee - exit_fee + trade_funding,
            )
        )
        active_target = (Decimal("0"), Decimal("0"))
        entry_fee = Decimal("0")
        trade_funding = Decimal("0")

    def open_positions(target: WeightTarget, bar: PairBar) -> None:
        nonlocal cash, active_target, entry_at_ms, entry_fee, total_fees
        if target == (Decimal("0"), Decimal("0")) or cash <= 0:
            return
        fees = Decimal("0")
        for index, (weight, market) in enumerate(
            zip(target, (bar.left.open, bar.right.open), strict=True)
        ):
            if not weight:
                continue
            fill = market * (
                Decimal("1") + slippage_rate if weight > 0 else Decimal("1") - slippage_rate
            )
            quantity = _floor_step(
                cash * exposure_value * abs(weight) / fill,
                Decimal("0.001"),
            )
            if quantity <= 0:
                continue
            positions[index] = quantity if weight > 0 else -quantity
            entries[index] = fill
            fees += quantity * fill * fee_rate
        if not any(positions) or fees >= cash:
            positions[:] = [Decimal("0"), Decimal("0")]
            entries[:] = [Decimal("0"), Decimal("0")]
            return
        cash -= fees
        total_fees += fees
        entry_fee = fees
        entry_at_ms = bar.timestamp_ms
        active_target = target

    last_index = selected[-1]
    for index in selected:
        bar = bars[index]
        if pending_target != active_target:
            close_positions(bar)
            open_positions(pending_target, bar)
        for events, position in (
            (funding_left[index], positions[0]),
            (funding_right[index], positions[1]),
        ):
            for event in events:
                if position:
                    amount = -(position * event.mark_price * event.rate)
                    cash += amount
                    total_funding += amount
                    trade_funding += amount
        equity = cash
        equity += positions[0] * (bar.left.close - entries[0])
        equity += positions[1] * (bar.right.close - entries[1])
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
    if any(positions) and not bankrupt:
        close_positions(final_bar, at_close=True)
        daily_equity[_utc_date(final_bar.end_ms)] = cash
        peak_equity = max(peak_equity, cash)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, cash / peak_equity - Decimal("1"))
    final_equity = cash if not bankrupt else daily_equity[_utc_date(final_bar.end_ms)]
    wins = sum(trade.net_pnl > 0 for trade in trades)
    gross_profit = sum((trade.net_pnl for trade in trades if trade.net_pnl > 0), Decimal("0"))
    gross_loss = -sum((trade.net_pnl for trade in trades if trade.net_pnl < 0), Decimal("0"))
    return PortfolioResult(
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
        daily_returns=_period_returns(daily_equity, initial_equity, 10),
        monthly_returns=_period_returns(daily_equity, initial_equity, 7),
        yearly_returns=_period_returns(daily_equity, initial_equity, 4),
        trades=tuple(trades),
    )


def _raw_features(
    bars: list[PairBar],
    funding: list[list[FundingRate]],
    lookback: int,
    *,
    left: bool,
) -> dict[str, tuple[Decimal | None, ...]]:
    selected = [bar.left if left else bar.right for bar in bars]
    returns: list[Decimal | None] = [None]
    for index in range(1, len(selected)):
        returns.append(selected[index].close / selected[index - 1].close - Decimal("1"))
    momentum: list[Decimal | None] = []
    breakout: list[Decimal | None] = []
    volatility: list[Decimal | None] = []
    carry: list[Decimal | None] = []
    funding_values = [sum((event.rate for event in events), Decimal("0")) for events in funding]
    for index, bar in enumerate(selected):
        if index < lookback:
            momentum.append(None)
            breakout.append(None)
            volatility.append(None)
            carry.append(None)
            continue
        prior = selected[index - lookback : index]
        prior_returns = [
            value for value in returns[index - lookback + 1 : index + 1] if value is not None
        ]
        momentum.append(bar.close / selected[index - lookback].close - Decimal("1"))
        prior_high = max(item.high for item in prior)
        prior_low = min(item.low for item in prior)
        breakout.append(
            (bar.close - prior_low) / (prior_high - prior_low) * Decimal("2") - Decimal("1")
            if prior_high > prior_low
            else Decimal("0")
        )
        if prior_returns:
            mean = sum(prior_returns, Decimal("0")) / Decimal(len(prior_returns))
            variance = sum((value - mean) ** 2 for value in prior_returns) / Decimal(
                len(prior_returns)
            )
            volatility.append(variance.sqrt())
        else:
            volatility.append(Decimal("0"))
        carry.append(-sum(funding_values[index - lookback + 1 : index + 1], Decimal("0")))
    return {
        "momentum": tuple(momentum),
        "breakout": tuple(breakout),
        "carry": tuple(carry),
        "volatility": tuple(volatility),
    }


def _combine_feature_set(
    features: dict[str, tuple[Decimal | None, ...]], feature_set: str
) -> tuple[Decimal | None, ...]:
    weights = {
        "momentum": {"momentum": Decimal("1")},
        "momentum_breakout": {"momentum": Decimal("1"), "breakout": Decimal("0.5")},
        "momentum_carry": {"momentum": Decimal("1"), "carry": Decimal("0.5")},
        "momentum_defensive": {"momentum": Decimal("1"), "volatility": Decimal("-0.25")},
        "all_equal": {
            "momentum": Decimal("1"),
            "breakout": Decimal("0.5"),
            "carry": Decimal("0.5"),
            "volatility": Decimal("-0.25"),
        },
    }[feature_set]
    length = len(next(iter(features.values())))
    result: list[Decimal | None] = []
    for index in range(length):
        values = [(features[name][index], weight) for name, weight in weights.items()]
        if any(value is None for value, _weight in values):
            result.append(None)
        else:
            result.append(sum((value * weight for value, weight in values), Decimal("0")))
    return tuple(result)


def _desired_target(left: Decimal, right: Decimal, candidate: CrossAssetCandidate) -> WeightTarget:
    zero = Decimal("0")
    half = Decimal("0.5")
    one = Decimal("1")
    threshold = candidate.threshold
    if candidate.family == "common_trend":
        common = (left + right) / Decimal("2")
        if common > threshold:
            return (half, half)
        if candidate.direction == "long_short" and common < -threshold:
            return (-half, -half)
        return (zero, zero)
    if candidate.family == "dual_trend":
        signs = [
            one
            if score > threshold
            else -one
            if candidate.direction == "long_short" and score < -threshold
            else zero
            for score in (left, right)
        ]
        active = sum(value != 0 for value in signs)
        return tuple(value / Decimal(active) if active else zero for value in signs)  # type: ignore[return-value]
    if candidate.family == "rotation":
        if candidate.direction == "long_only":
            if max(left, right) <= threshold:
                return (zero, zero)
            return (one, zero) if left >= right else (zero, one)
        strongest = max(left, right)
        weakest = min(left, right)
        if strongest <= threshold and weakest >= -threshold:
            return (zero, zero)
        if strongest >= -weakest:
            return (one, zero) if left >= right else (zero, one)
        return (-one, zero) if left <= right else (zero, -one)
    if candidate.family == "relative_value":
        difference = left - right
        if abs(difference) <= threshold:
            return (zero, zero)
        return (half, -half) if difference > 0 else (-half, half)
    if candidate.family == "relative_reversion":
        difference = left - right
        if abs(difference) <= threshold:
            return (zero, zero)
        return (-half, half) if difference > 0 else (half, -half)
    if candidate.family == "relative_adaptive":
        difference = left - right
        if abs(difference) <= threshold:
            return (zero, zero)
        return (half, -half) if difference > 0 else (-half, half)
    raise ValueError(f"unsupported cross-asset family: {candidate.family}")


def _apply_minimum_hold(
    desired: tuple[WeightTarget | None, ...], minimum_hold_bars: int
) -> tuple[WeightTarget | None, ...]:
    if minimum_hold_bars <= 0:
        return desired
    current: WeightTarget | None = None
    held = 0
    result: list[WeightTarget | None] = []
    for target in desired:
        if target is None:
            result.append(None)
            continue
        if current is None:
            current = target
            held = 1
        elif target == current:
            held += 1
        elif held >= minimum_hold_bars:
            current = target
            held = 1
        else:
            held += 1
        result.append(current)
    return tuple(result)


def _causal_relative_efficacy(
    left_scores: tuple[Decimal | None, ...],
    right_scores: tuple[Decimal | None, ...],
    bars: list[PairBar] | None,
    candidate: CrossAssetCandidate,
) -> tuple[Decimal | None, ...]:
    if bars is None or len(bars) != len(left_scores):
        raise ValueError("adaptive relative factors require aligned bars")
    window = candidate.adaptation_days * 1440 // candidate.interval_minutes
    if window < 2:
        raise ValueError("adaptive relative factor window is too short")
    outcomes: list[Decimal | None] = [None]
    for index in range(1, len(bars)):
        previous_left = left_scores[index - 1]
        previous_right = right_scores[index - 1]
        if previous_left is None or previous_right is None:
            outcomes.append(None)
            continue
        signal = Decimal("1") if previous_left >= previous_right else Decimal("-1")
        left_return = bars[index].left.close / bars[index - 1].left.close - Decimal("1")
        right_return = bars[index].right.close / bars[index - 1].right.close - Decimal("1")
        outcomes.append(signal * (left_return - right_return))
    result: list[Decimal | None] = []
    history: deque[Decimal] = deque()
    total = Decimal("0")
    for outcome in outcomes:
        if outcome is not None:
            history.append(outcome)
            total += outcome
            if len(history) > window:
                total -= history.popleft()
        if len(history) < window:
            result.append(None)
        else:
            result.append(Decimal("1") if total >= 0 else Decimal("-1"))
    return tuple(result)


def _causal_zscore(values: tuple[Decimal | None, ...], window: int) -> tuple[Decimal | None, ...]:
    history: deque[Decimal | None] = deque()
    total = Decimal("0")
    total_square = Decimal("0")
    valid = 0
    result: list[Decimal | None] = []
    for value in values:
        if value is None or len(history) < window or valid < window:
            result.append(None)
        else:
            mean = total / Decimal(valid)
            variance = total_square / Decimal(valid) - mean * mean
            result.append((value - mean) / variance.sqrt() if variance > 0 else Decimal("0"))
        history.append(value)
        if value is not None:
            total += value
            total_square += value * value
            valid += 1
        if len(history) > window:
            removed = history.popleft()
            if removed is not None:
                total -= removed
                total_square -= removed * removed
                valid -= 1
    return tuple(result)


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
