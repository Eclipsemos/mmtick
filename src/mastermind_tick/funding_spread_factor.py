"""Causal BTC/ETH funding-spread signals for research portfolios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class FundingSpreadCandidate:
    lookback_bars: int
    threshold_bps: Decimal
    mode: str
    minimum_hold_bars: int
    confirmation_bars: int

    def __post_init__(self) -> None:
        if self.lookback_bars < 1 or self.minimum_hold_bars < 1:
            raise ValueError("funding spread lookback and hold must be positive")
        if self.threshold_bps < 0 or self.confirmation_bars < 1:
            raise ValueError("funding spread threshold and confirmation are invalid")
        if self.mode not in {"carry", "crowding_follow"}:
            raise ValueError("funding spread mode is unsupported")

    @property
    def id(self) -> str:
        threshold = f"{self.threshold_bps:g}".replace(".", "p")
        return (
            f"funding-spread-{self.mode}-lookback-{self.lookback_bars}x4h-"
            f"threshold-{threshold}bps-hold-{self.minimum_hold_bars}-"
            f"confirm-{self.confirmation_bars}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "id": self.id, "threshold_bps": float(self.threshold_bps)}


def funding_spread_scores(
    btc_funding: list[list[FundingRate]],
    eth_funding: list[list[FundingRate]],
    lookback_bars: int,
) -> tuple[Decimal, ...]:
    """Return trailing BTC-minus-ETH funding in bps through each closed bar."""
    if len(btc_funding) != len(eth_funding):
        raise ValueError("funding spread inputs must be aligned")
    if lookback_bars < 1:
        raise ValueError("funding spread lookback must be positive")
    raw = tuple(
        sum((event.rate for event in btc_events), Decimal("0"))
        - sum((event.rate for event in eth_events), Decimal("0"))
        for btc_events, eth_events in zip(btc_funding, eth_funding, strict=True)
    )
    result = []
    rolling = Decimal("0")
    for index, value in enumerate(raw):
        rolling += value
        if index >= lookback_bars:
            rolling -= raw[index - lookback_bars]
        result.append(rolling * Decimal("10000"))
    return tuple(result)


def funding_spread_targets(
    scores: tuple[Decimal, ...],
    candidate: FundingSpreadCandidate,
) -> tuple[tuple[Decimal, ...], tuple[Decimal, ...]]:
    """Return opposite BTC/ETH targets; a closed-bar score acts at the next open."""
    state = 0
    hold_count = 0
    pending = 0
    pending_count = 0
    btc_targets = []
    eth_targets = []
    multiplier = -1 if candidate.mode == "carry" else 1
    for score in scores:
        desired = (
            multiplier
            if score >= candidate.threshold_bps and score > 0
            else -multiplier
            if score <= -candidate.threshold_bps and score < 0
            else 0
        )
        if state:
            hold_count += 1
            if desired == state:
                pending = 0
                pending_count = 0
            elif hold_count >= candidate.minimum_hold_bars:
                state = 0
                hold_count = 0
                pending = 0
                pending_count = 0
        if not state and desired:
            pending_count = pending_count + 1 if pending == desired else 1
            pending = desired
            if pending_count >= candidate.confirmation_bars:
                state = desired
                hold_count = 0
                pending = 0
                pending_count = 0
        elif not state:
            pending = 0
            pending_count = 0
        btc_targets.append(Decimal(state))
        eth_targets.append(Decimal(-state))
    return tuple(btc_targets), tuple(eth_targets)
