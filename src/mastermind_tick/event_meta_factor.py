"""Causal features and target filtering for sparse shock-event meta labels."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.models import FundingRate

FEATURE_NAMES = (
    "btc_shock_z",
    "eth_shock_z",
    "shock_gap",
    "btc_return_1",
    "btc_return_3",
    "btc_return_6",
    "btc_return_18",
    "btc_return_42",
    "eth_return_1",
    "eth_return_3",
    "eth_return_6",
    "eth_return_18",
    "eth_return_42",
    "relative_return_6",
    "relative_return_18",
    "btc_volatility_18",
    "eth_volatility_18",
    "btc_efficiency_18",
    "eth_efficiency_18",
    "btc_range_ratio",
    "eth_range_ratio",
    "btc_close_location",
    "eth_close_location",
    "btc_volume_ratio",
    "eth_volume_ratio",
    "return_correlation_30",
)


@dataclass(frozen=True)
class EventSample:
    index: int
    timestamp_ms: int
    exit_timestamp_ms: int
    direction: int
    features: tuple[Decimal, ...]
    net_return: Decimal

    @property
    def profitable(self) -> bool:
        return self.net_return > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "features": [float(value) for value in self.features],
            "net_return": float(self.net_return),
            "profitable": self.profitable,
        }


def build_event_samples(
    btc_bars: list[ResearchBar],
    eth_bars: list[ResearchBar],
    btc_scores: tuple[Decimal | None, ...],
    eth_scores: tuple[Decimal | None, ...],
    base_targets: tuple[int | None, ...],
    eth_funding: list[list[FundingRate]],
    *,
    hold_bars: int,
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
) -> tuple[EventSample, ...]:
    """Build one causal feature row and realized net label for each base-factor entry."""
    lengths = {
        len(btc_bars),
        len(eth_bars),
        len(btc_scores),
        len(eth_scores),
        len(base_targets),
        len(eth_funding),
    }
    if len(lengths) != 1:
        raise ValueError("event meta-label inputs have different lengths")
    if hold_bars < 1:
        raise ValueError("event meta-label holding period must be positive")
    if any(btc.start_ms != eth.start_ms for btc, eth in zip(btc_bars, eth_bars, strict=True)):
        raise ValueError("event meta-label BTC and ETH bars are not aligned")

    result = []
    for index, target in enumerate(base_targets):
        previous = base_targets[index - 1] if index else None
        if target not in {-1, 1} or previous not in {None, 0}:
            continue
        exit_index = index + hold_bars + 1
        if index < 42 or exit_index >= len(eth_bars):
            continue
        btc_score = btc_scores[index]
        eth_score = eth_scores[index]
        if btc_score is None or eth_score is None:
            continue
        features = _features(btc_bars, eth_bars, btc_score, eth_score, index)
        net_return = _event_net_return(
            eth_bars,
            eth_funding,
            index,
            exit_index,
            target,
            fee_bps,
            slippage_bps,
        )
        result.append(
            EventSample(
                index=index,
                timestamp_ms=btc_bars[index].end_ms,
                exit_timestamp_ms=eth_bars[exit_index].start_ms,
                direction=target,
                features=features,
                net_return=net_return,
            )
        )
    return tuple(result)


def filtered_event_targets(
    base_targets: tuple[int | None, ...],
    samples: tuple[EventSample, ...],
    probabilities: tuple[Decimal, ...],
    *,
    probability_threshold: Decimal,
    exposure: Decimal,
) -> tuple[Decimal | None, ...]:
    """Keep or reject each complete base event using its entry-time probability."""
    if len(samples) != len(probabilities):
        raise ValueError("event sample and probability lengths differ")
    if not Decimal("0") <= probability_threshold <= Decimal("1"):
        raise ValueError("event probability threshold must be between zero and one")
    if exposure <= 0:
        raise ValueError("event exposure must be positive")
    decisions = {
        sample.index: probability >= probability_threshold
        for sample, probability in zip(samples, probabilities, strict=True)
    }
    active = Decimal("0")
    blocked = False
    result: list[Decimal | None] = []
    for index, target in enumerate(base_targets):
        if target is None:
            result.append(None)
            continue
        if target == 0:
            active = Decimal("0")
            blocked = False
        elif not active and not blocked:
            if decisions.get(index, False):
                active = exposure if target > 0 else -exposure
            else:
                blocked = True
        result.append(active)
    return tuple(result)


def _features(
    btc_bars: list[ResearchBar],
    eth_bars: list[ResearchBar],
    btc_score: Decimal,
    eth_score: Decimal,
    index: int,
) -> tuple[Decimal, ...]:
    btc_returns = tuple(_return(btc_bars, index, window) for window in (1, 3, 6, 18, 42))
    eth_returns = tuple(_return(eth_bars, index, window) for window in (1, 3, 6, 18, 42))
    btc = btc_bars[index]
    eth = eth_bars[index]
    features = (
        btc_score,
        eth_score,
        btc_score - eth_score,
        *btc_returns,
        *eth_returns,
        btc_returns[2] - eth_returns[2],
        btc_returns[3] - eth_returns[3],
        _volatility(btc_bars, index, 18),
        _volatility(eth_bars, index, 18),
        _efficiency(btc_bars, index, 18),
        _efficiency(eth_bars, index, 18),
        (btc.high - btc.low) / btc.close,
        (eth.high - eth.low) / eth.close,
        _close_location(btc),
        _close_location(eth),
        _volume_ratio(btc_bars, index, 30),
        _volume_ratio(eth_bars, index, 30),
        _return_correlation(btc_bars, eth_bars, index, 30),
    )
    if len(features) != len(FEATURE_NAMES):
        raise RuntimeError("event meta-label feature definition is inconsistent")
    return features


def _event_net_return(
    bars: list[ResearchBar],
    funding: list[list[FundingRate]],
    signal_index: int,
    exit_index: int,
    direction: int,
    fee_bps: Decimal,
    slippage_bps: Decimal,
) -> Decimal:
    fee_rate = fee_bps / Decimal("10000")
    slippage_rate = slippage_bps / Decimal("10000")
    entry = bars[signal_index + 1].open * (
        Decimal("1") + slippage_rate if direction > 0 else Decimal("1") - slippage_rate
    )
    exit_price = bars[exit_index].open * (
        Decimal("1") - slippage_rate if direction > 0 else Decimal("1") + slippage_rate
    )
    gross = Decimal(direction) * (exit_price - entry) / entry
    fees = fee_rate * (Decimal("1") + exit_price / entry)
    funding_return = sum(
        (
            -Decimal(direction) * event.mark_price / entry * event.rate
            for events in funding[signal_index + 1 : exit_index + 1]
            for event in events
        ),
        Decimal("0"),
    )
    return gross - fees + funding_return


def _return(bars: list[ResearchBar], index: int, lookback: int) -> Decimal:
    return bars[index].close / bars[index - lookback].close - Decimal("1")


def _volatility(bars: list[ResearchBar], index: int, window: int) -> Decimal:
    values = tuple(
        bars[offset].close / bars[offset - 1].close - Decimal("1")
        for offset in range(index - window + 1, index + 1)
    )
    mean = sum(values, Decimal("0")) / Decimal(window)
    variance = sum((value - mean) ** 2 for value in values) / Decimal(window)
    return variance.sqrt()


def _efficiency(bars: list[ResearchBar], index: int, window: int) -> Decimal:
    displacement = abs(bars[index].close - bars[index - window].close)
    path = sum(
        (
            abs(bars[offset].close - bars[offset - 1].close)
            for offset in range(index - window + 1, index + 1)
        ),
        Decimal("0"),
    )
    return displacement / path if path else Decimal("0")


def _close_location(bar: ResearchBar) -> Decimal:
    return (
        (bar.close - bar.low) / (bar.high - bar.low) - Decimal("0.5")
        if bar.high > bar.low
        else Decimal("0")
    )


def _volume_ratio(bars: list[ResearchBar], index: int, window: int) -> Decimal:
    average = sum((bar.volume for bar in bars[index - window : index]), Decimal("0")) / Decimal(
        window
    )
    return bars[index].volume / average - Decimal("1") if average else Decimal("0")


def _return_correlation(
    left: list[ResearchBar], right: list[ResearchBar], index: int, window: int
) -> Decimal:
    first = tuple(
        left[offset].close / left[offset - 1].close - Decimal("1")
        for offset in range(index - window + 1, index + 1)
    )
    second = tuple(
        right[offset].close / right[offset - 1].close - Decimal("1")
        for offset in range(index - window + 1, index + 1)
    )
    count = Decimal(window)
    first_mean = sum(first, Decimal("0")) / count
    second_mean = sum(second, Decimal("0")) / count
    covariance = sum(
        (a - first_mean) * (b - second_mean) for a, b in zip(first, second, strict=True)
    )
    denominator = (
        sum((value - first_mean) ** 2 for value in first)
        * sum((value - second_mean) ** 2 for value in second)
    ).sqrt()
    return covariance / denominator if denominator else Decimal("0")
