"""Causal extreme-funding event factors."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class FundingEventCandidate:
    lookback_events: int
    threshold: Decimal
    hold_bars: int
    mode: str
    direction: str

    def __post_init__(self) -> None:
        if self.lookback_events < 5 or self.threshold <= 0 or self.hold_bars < 1:
            raise ValueError("funding event window, threshold, and hold are invalid")
        if self.mode not in {"reversal", "continuation"}:
            raise ValueError("funding event mode is unsupported")
        if self.direction not in {"long_only", "long_short"}:
            raise ValueError("funding event direction is unsupported")

    @property
    def id(self) -> str:
        threshold = f"{self.threshold:g}".replace(".", "p")
        return (
            f"funding-event-{self.mode}-{self.direction}-lookback-{self.lookback_events}-"
            f"threshold-{threshold}-hold-{self.hold_bars}x4h"
        )

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "id": self.id, "threshold": float(self.threshold)}


def funding_event_scores(
    funding: list[list[FundingRate]], lookback_events: int
) -> tuple[Decimal | None, ...]:
    """Z-score each funding event against only events observed before it."""
    if lookback_events < 5:
        raise ValueError("funding event lookback must be at least five")
    history: list[Decimal] = []
    scores: list[Decimal | None] = []
    for events in funding:
        if not events:
            scores.append(None)
            continue
        current = events[-1].rate
        if len(history) < lookback_events:
            score = None
        else:
            window = history[-lookback_events:]
            count = Decimal(lookback_events)
            mean = sum(window, Decimal("0")) / count
            variance = sum(((value - mean) ** 2 for value in window), Decimal("0")) / count
            deviation = variance.sqrt()
            score = (current - mean) / deviation if deviation else Decimal("0")
        history.extend(event.rate for event in events)
        scores.append(score)
    return tuple(scores)


def funding_event_targets(
    scores: tuple[Decimal | None, ...], candidate: FundingEventCandidate
) -> tuple[Decimal, ...]:
    """Turn an extreme event into a fixed closed-bar holding window."""
    state = 0
    remaining = 0
    targets = []
    multiplier = -1 if candidate.mode == "reversal" else 1
    for score in scores:
        if score is not None and abs(score) >= candidate.threshold:
            state = (1 if score > 0 else -1) * multiplier
            if candidate.direction == "long_only":
                state = max(0, state)
            remaining = candidate.hold_bars if state else 0
        targets.append(Decimal(state) if remaining else Decimal("0"))
        if remaining:
            remaining -= 1
            if not remaining:
                state = 0
    return tuple(targets)
