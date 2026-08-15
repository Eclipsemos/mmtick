"""Causal vote aggregation for de-duplicated sparse event factors."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ConsensusConfig:
    minimum_active: int
    minimum_agreement: Decimal
    mode: str
    exposure: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "minimum_agreement": float(self.minimum_agreement),
            "exposure": float(self.exposure),
        }


def consensus_targets(
    member_targets: tuple[tuple[int | None, ...], ...],
    config: ConsensusConfig,
) -> tuple[Decimal | None, ...]:
    """Aggregate simultaneous signed event votes without using future target values."""
    if not member_targets:
        raise ValueError("event consensus requires at least one member")
    lengths = {len(values) for values in member_targets}
    if len(lengths) != 1:
        raise ValueError("event consensus member lengths differ")
    if config.minimum_active < 1:
        raise ValueError("event consensus minimum active votes must be positive")
    if not Decimal("0") < config.minimum_agreement <= Decimal("1"):
        raise ValueError("event consensus agreement must be between zero and one")
    if config.mode not in {"follow", "fade"}:
        raise ValueError("event consensus mode is unsupported")
    if config.exposure <= 0:
        raise ValueError("event consensus exposure must be positive")

    result: list[Decimal | None] = []
    for values in zip(*member_targets, strict=True):
        if all(value is None for value in values):
            result.append(None)
            continue
        active = tuple(value for value in values if value not in {None, 0})
        if len(active) < config.minimum_active:
            result.append(Decimal("0"))
            continue
        net = sum(active)
        agreement = Decimal(abs(net)) / Decimal(len(active))
        if not net or agreement < config.minimum_agreement:
            result.append(Decimal("0"))
            continue
        side = Decimal("1") if net > 0 else Decimal("-1")
        if config.mode == "fade":
            side = -side
        result.append(side * config.exposure)
    return tuple(result)
