"""Deterministic block-bootstrap diagnostics for strategy return paths."""

from __future__ import annotations

import random
from typing import Any


def circular_block_bootstrap(
    daily_returns: list[float],
    *,
    horizon_days: int,
    block_size: int,
    simulations: int,
    seed: int,
    target_geometric_daily_return: float = 0.05,
) -> dict[str, Any]:
    """Resample adjacent daily returns in circular blocks without changing the source series."""
    if not daily_returns:
        raise ValueError("daily_returns must not be empty")
    if any(value <= -1 for value in daily_returns):
        raise ValueError("source daily returns must be greater than -100%")
    if min(horizon_days, block_size, simulations) < 1:
        raise ValueError("horizon_days, block_size, and simulations must be positive")

    generator = random.Random(seed)
    geometric_returns: list[float] = []
    max_drawdowns: list[float] = []
    terminal_returns: list[float] = []
    target_hits = terminal_losses = 0
    drawdown_30_hits = drawdown_50_hits = drawdown_80_hits = 0
    ruins = 0
    source_days = len(daily_returns)

    for _ in range(simulations):
        equity = peak = 1.0
        max_drawdown = 0.0
        sampled_days = 0
        ruined = False
        while sampled_days < horizon_days:
            block_start = generator.randrange(source_days)
            for offset in range(block_size):
                value = daily_returns[(block_start + offset) % source_days]
                equity *= 1 + value
                sampled_days += 1
                if equity <= 0:
                    ruined = True
                    equity = 0.0
                    max_drawdown = -1.0
                    break
                peak = max(peak, equity)
                max_drawdown = min(max_drawdown, equity / peak - 1)
                if sampled_days == horizon_days:
                    break
            if ruined:
                break

        geometric = equity ** (1 / horizon_days) - 1 if equity > 0 else -1.0
        terminal_return = equity - 1
        geometric_returns.append(geometric)
        max_drawdowns.append(max_drawdown)
        terminal_returns.append(terminal_return)
        target_hits += geometric >= target_geometric_daily_return
        terminal_losses += terminal_return < 0
        drawdown_30_hits += max_drawdown <= -0.30
        drawdown_50_hits += max_drawdown <= -0.50
        drawdown_80_hits += max_drawdown <= -0.80
        ruins += ruined

    return {
        "method": "circular_moving_block_bootstrap",
        "source_days": source_days,
        "horizon_days": horizon_days,
        "block_size": block_size,
        "simulations": simulations,
        "seed": seed,
        "target_geometric_daily_return": target_geometric_daily_return,
        "probability_target_reached": target_hits / simulations,
        "probability_terminal_loss": terminal_losses / simulations,
        "probability_daily_close_drawdown_30": drawdown_30_hits / simulations,
        "probability_daily_close_drawdown_50": drawdown_50_hits / simulations,
        "probability_daily_close_drawdown_80": drawdown_80_hits / simulations,
        "probability_daily_close_ruin": ruins / simulations,
        "geometric_daily_return": _distribution(geometric_returns),
        "terminal_return": _distribution(terminal_returns),
        "max_daily_close_drawdown": _distribution(max_drawdowns),
    }


def _distribution(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p05": _percentile(ordered, 0.05),
        "p25": _percentile(ordered, 0.25),
        "median": _percentile(ordered, 0.50),
        "p75": _percentile(ordered, 0.75),
        "p95": _percentile(ordered, 0.95),
    }


def _percentile(ordered: list[float], probability: float) -> float:
    if not ordered:
        raise ValueError("ordered values must not be empty")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight
