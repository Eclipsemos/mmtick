"""Research-only configuration and ranking helpers for static factor portfolios."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mastermind_tick.factor_portfolio import (
    DailyReturns,
    PortfolioResult,
    evaluate_static_portfolio,
)


@dataclass(frozen=True)
class StaticPortfolioConfig:
    allocations: tuple[tuple[str, Decimal], ...]
    leverage: Decimal

    def __post_init__(self) -> None:
        names = tuple(name for name, _weight in self.allocations)
        weights = tuple(weight for _name, weight in self.allocations)
        if not names or len(set(names)) != len(names):
            raise ValueError("static portfolio sleeve names must be non-empty and unique")
        if any(weight <= 0 for weight in weights):
            raise ValueError("static portfolio weights must be positive")
        if sum(weights, Decimal("0")) != Decimal("1"):
            raise ValueError("static portfolio weights must sum to one")
        if self.leverage <= 0:
            raise ValueError("static portfolio leverage must be positive")

    @property
    def allocation_map(self) -> dict[str, Decimal]:
        return dict(self.allocations)

    @property
    def id(self) -> str:
        sleeves = "__".join(f"{name}-{_decimal_id(weight)}" for name, weight in self.allocations)
        return f"static-{sleeves}__leverage-{_decimal_id(self.leverage)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "allocations": {name: float(weight) for name, weight in self.allocations},
            "leverage": float(self.leverage),
        }


def static_weight_grid(
    lead_name: str,
    secondary_names: tuple[str, ...],
    *,
    lead_weights: Iterable[Decimal],
    secondary_patterns: Iterable[tuple[Decimal, ...]],
    leverages: Iterable[Decimal],
) -> tuple[StaticPortfolioConfig, ...]:
    """Build a deterministic, de-duplicated allocation grid.

    Secondary patterns describe relative weights and are normalized into the capital left after
    assigning the lead sleeve. Permutations are intentionally supplied by the caller so the
    research protocol remains explicit.
    """
    if not secondary_names or lead_name in secondary_names:
        raise ValueError("static portfolio requires distinct lead and secondary sleeves")
    rows: dict[tuple[tuple[tuple[str, Decimal], ...], Decimal], StaticPortfolioConfig] = {}
    for lead_weight in lead_weights:
        if not Decimal("0") < lead_weight < Decimal("1"):
            raise ValueError("lead weight must be between zero and one")
        available = Decimal("1") - lead_weight
        for pattern in secondary_patterns:
            if len(pattern) != len(secondary_names) or any(value <= 0 for value in pattern):
                raise ValueError("secondary pattern does not match the sleeve set")
            total = sum(pattern, Decimal("0"))
            secondary_weights = [available * value / total for value in pattern[:-1]]
            secondary_weights.append(available - sum(secondary_weights, Decimal("0")))
            allocations = (
                (lead_name, lead_weight),
                *tuple(
                    (name, weight)
                    for name, weight in zip(secondary_names, secondary_weights, strict=True)
                ),
            )
            for leverage in leverages:
                config = StaticPortfolioConfig(allocations, leverage)
                rows[(config.allocations, config.leverage)] = config
    return tuple(rows.values())


def evaluate_static_config(
    sleeves: dict[str, DailyReturns], config: StaticPortfolioConfig
) -> PortfolioResult:
    selected = {name: sleeves[name] for name, _weight in config.allocations}
    return evaluate_static_portfolio(
        selected,
        config.allocation_map,
        leverage=config.leverage,
    )


def development_eligible(
    results: dict[str, PortfolioResult],
    *,
    drawdown_floor: Decimal = Decimal("-0.35"),
) -> bool:
    if set(results) != {"discovery", "validation"}:
        raise ValueError("development results require discovery and validation splits")
    return all(
        not result.bankrupt and result.net_return > 0 and result.max_drawdown >= drawdown_floor
        for result in results.values()
    )


def development_score(results: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
    """Rank only with development data, prioritizing repeatable 25% month coverage."""
    if set(results) != {"discovery", "validation"}:
        raise ValueError("development results require discovery and validation splits")
    discovery = results["discovery"]
    validation = results["validation"]
    return (
        min(discovery.target_month_rate, validation.target_month_rate),
        discovery.target_month_rate + validation.target_month_rate,
        min(discovery.positive_month_rate, validation.positive_month_rate),
        min(discovery.worst_month, validation.worst_month),
        min(discovery.net_return, validation.net_return),
        min(discovery.max_drawdown, validation.max_drawdown),
    )


def _decimal_id(value: Decimal) -> str:
    return f"{value:g}".replace(".", "p")
