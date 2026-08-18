"""Minute-level trade-flow research primitives.

The functions in this module operate on closed bars. A signal observed at bar ``i`` can only fill
at bar ``i + 1`` or later.
"""

from __future__ import annotations

import math
from array import array
from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class HighFrequencyCandidate:
    feature: str
    interval_minutes: int
    normalization_window: int
    threshold: float
    hold_bars: int

    @property
    def id(self) -> str:
        threshold = f"{self.threshold:g}".replace(".", "p")
        return (
            f"{self.feature}-{self.interval_minutes}m-window{self.normalization_window}-"
            f"threshold{threshold}-hold{self.hold_bars}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, **asdict(self)}


@dataclass(frozen=True)
class ExecutionCost:
    name: str
    fee_bps: Decimal
    slippage_bps: Decimal

    @property
    def all_in_bps_per_fill(self) -> Decimal:
        return self.fee_bps + self.slippage_bps


@dataclass(frozen=True)
class HighFrequencyReplay:
    net_return: float
    max_drawdown: float
    completed_trades: int
    win_rate: float | None
    profit_factor: float | None
    average_gross_bps: float | None
    approximate_break_even_bps_per_fill: float | None
    positive_month_rate: float
    monthly_returns: tuple[tuple[str, float], ...]
    bankrupt: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "monthly_returns": [
                {"label": label, "return": value} for label, value in self.monthly_returns
            ],
        }


def rolling_prior_zscore(values: Sequence[float], window: int) -> array:
    """Z-score each value against prior values in the trailing index window."""
    if window < 8:
        raise ValueError("normalization window must contain at least eight bars")
    result = array("d")
    observations: deque[tuple[int, float]] = deque()
    total = 0.0
    total_square = 0.0
    minimum_count = max(8, window // 2)
    for index, value in enumerate(values):
        while observations and observations[0][0] < index - window:
            _, expired = observations.popleft()
            total -= expired
            total_square -= expired * expired
        if math.isfinite(value) and len(observations) >= minimum_count:
            mean = total / len(observations)
            variance = max(total_square / len(observations) - mean * mean, 0.0)
            scale = math.sqrt(variance)
            if scale <= 1e-12:
                scale = max(abs(mean), 1.0) * 1e-12
            result.append((value - mean) / scale)
        else:
            result.append(math.nan)
        if math.isfinite(value):
            observations.append((index, value))
            total += value
            total_square += value * value
    return result


def feature_scores(
    closes: Sequence[float],
    reported_imbalance: Sequence[float],
    tick_rule_imbalance: Sequence[float],
    notionals: Sequence[float],
    window: int,
) -> dict[str, array]:
    """Build causal minute-flow features from prior-window normalized inputs."""
    if not (len(closes) == len(reported_imbalance) == len(tick_rule_imbalance) == len(notionals)):
        raise ValueError("high-frequency inputs must have equal lengths")
    returns = array("d", [math.nan])
    returns.extend(
        close / previous - 1 if previous > 0 else math.nan
        for previous, close in zip(closes, closes[1:], strict=False)
    )
    return_z = rolling_prior_zscore(returns, window)
    reported_z = rolling_prior_zscore(reported_imbalance, window)
    tick_z = rolling_prior_zscore(tick_rule_imbalance, window)
    notional_z = rolling_prior_zscore(notionals, window)

    result = {
        "reported_flow_follow": array("d"),
        "reported_flow_revert": array("d"),
        "tick_flow_follow": array("d"),
        "tick_flow_revert": array("d"),
        "tick_price_confirm": array("d"),
        "tick_absorption_revert": array("d"),
        "volume_burst_follow": array("d"),
        "volume_burst_revert": array("d"),
    }
    for price, reported, tick, volume in zip(return_z, reported_z, tick_z, notional_z, strict=True):
        result["reported_flow_follow"].append(reported)
        result["reported_flow_revert"].append(-reported)
        result["tick_flow_follow"].append(tick)
        result["tick_flow_revert"].append(-tick)
        if math.isfinite(price) and math.isfinite(tick):
            confirmation = (price + tick) / 2 if price * tick > 0 else 0.0
            absorption = (
                -math.copysign(min(abs(price), abs(tick)), tick) if price * tick < 0 else 0.0
            )
        else:
            confirmation = absorption = math.nan
        result["tick_price_confirm"].append(confirmation)
        result["tick_absorption_revert"].append(absorption)
        if math.isfinite(price) and math.isfinite(volume):
            multiplier = max(0.0, min(volume, 3.0))
            burst = price * multiplier
        else:
            burst = math.nan
        result["volume_burst_follow"].append(burst)
        result["volume_burst_revert"].append(-burst)
    return result


def threshold_events(scores: Sequence[float], threshold: float) -> tuple[tuple[int, int], ...]:
    if threshold <= 0:
        raise ValueError("signal threshold must be positive")
    return tuple(
        (index, 1 if value >= threshold else -1)
        for index, value in enumerate(scores)
        if math.isfinite(value) and abs(value) >= threshold
    )


def replay_fixed_hold(
    timestamps_ms: Sequence[int],
    closes: Sequence[float],
    events: Sequence[tuple[int, int]],
    *,
    hold_bars: int,
    start_ms: int,
    end_ms: int,
    cost: ExecutionCost,
) -> HighFrequencyReplay:
    """Replay non-overlapping fixed-hold events with next-bar-close execution."""
    if hold_bars < 1:
        raise ValueError("hold bars must be positive")
    if len(timestamps_ms) != len(closes):
        raise ValueError("timestamps and closes must have equal lengths")
    fee_rate = cost.fee_bps / Decimal("10000")
    slippage_rate = cost.slippage_bps / Decimal("10000")
    equity = Decimal("1")
    peak = equity
    max_drawdown = Decimal("0")
    next_signal_index = 0
    wins = 0
    gains = Decimal("0")
    losses = Decimal("0")
    gross_bps = Decimal("0")
    monthly_factors: dict[str, Decimal] = {}
    completed = 0

    for signal_index, direction in events:
        if signal_index < next_signal_index:
            continue
        entry_index = signal_index + 1
        exit_index = entry_index + hold_bars
        if exit_index >= len(closes):
            break
        if timestamps_ms[entry_index] < start_ms:
            continue
        if timestamps_ms[exit_index] > end_ms:
            break
        entry_mid = Decimal(str(closes[entry_index]))
        exit_mid = Decimal(str(closes[exit_index]))
        side = Decimal(direction)
        entry_fill = entry_mid * (Decimal("1") + side * slippage_rate)
        exit_fill = exit_mid * (Decimal("1") - side * slippage_rate)
        gross_rate = side * (exit_mid / entry_mid - Decimal("1"))
        price_rate = side * (exit_fill / entry_fill - Decimal("1"))
        exit_notional = exit_fill / entry_fill
        net_rate = price_rate - fee_rate * (Decimal("1") + exit_notional)
        factor = Decimal("1") + net_rate
        before = equity
        equity *= factor
        completed += 1
        gross_bps += gross_rate * Decimal("10000")
        if net_rate > 0:
            wins += 1
            gains += net_rate
        elif net_rate < 0:
            losses += -net_rate

        for mark_index in range(entry_index, exit_index + 1):
            mark = Decimal(str(closes[mark_index]))
            marked_rate = side * (mark / entry_mid - Decimal("1")) - fee_rate
            marked_equity = before * (Decimal("1") + marked_rate)
            peak = max(peak, marked_equity)
            if peak:
                max_drawdown = min(max_drawdown, marked_equity / peak - Decimal("1"))
        peak = max(peak, equity)
        if peak:
            max_drawdown = min(max_drawdown, equity / peak - Decimal("1"))
        label = _month_label(timestamps_ms[exit_index])
        monthly_factors[label] = monthly_factors.get(label, Decimal("1")) * factor
        next_signal_index = exit_index
        if equity <= 0:
            break

    monthly = tuple((label, float(factor - 1)) for label, factor in sorted(monthly_factors.items()))
    positive_months = sum(value > 0 for _, value in monthly)
    average_gross = gross_bps / completed if completed else None
    return HighFrequencyReplay(
        net_return=float(equity - 1),
        max_drawdown=float(max_drawdown),
        completed_trades=completed,
        win_rate=wins / completed if completed else None,
        profit_factor=float(gains / losses) if losses else (math.inf if gains else None),
        average_gross_bps=float(average_gross) if average_gross is not None else None,
        approximate_break_even_bps_per_fill=(
            float(average_gross / 2) if average_gross is not None else None
        ),
        positive_month_rate=positive_months / len(monthly) if monthly else 0.0,
        monthly_returns=monthly,
        bankrupt=equity <= 0,
    )


def _month_label(timestamp_ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).strftime("%Y-%m")
