import math
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from mastermind_tick.bar_research import ResearchBar

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_block_bootstrap import (  # noqa: E402
    paired_daily_log_returns,
    run_bootstrap,
)


def _bar(day: int, close: str) -> ResearchBar:
    start_ms = day * 86_400_000
    value = Decimal(close)
    return ResearchBar(
        start_ms=start_ms,
        end_ms=start_ms + 86_400_000 - 1,
        open=value,
        high=value,
        low=value,
        close=value,
    )


def test_paired_daily_returns_reconstruct_strategy_and_benchmark() -> None:
    bars = [_bar(0, "100"), _bar(1, "110")]
    curve = ((bars[0].end_ms, 105_000.0), (bars[1].end_ms, 115_500.0))

    strategy, benchmark = paired_daily_log_returns(bars, curve, 100_000.0)

    assert math.exp(sum(strategy)) - 1 == pytest.approx(0.155)
    assert math.exp(sum(benchmark)) - 1 == pytest.approx(0.1)


def test_bootstrap_is_deterministic_and_preserves_clear_edge() -> None:
    strategy = tuple(math.log(1.01) for _ in range(30))
    benchmark = tuple(0.0 for _ in range(30))

    first = run_bootstrap(strategy, benchmark, block_days=7, samples=100, seed=42)
    second = run_bootstrap(strategy, benchmark, block_days=7, samples=100, seed=42)

    assert first == second
    assert first["probability_beats_bh_return"] == 1.0
    assert first["probability_drawdown_no_worse_than_bh"] == 1.0
    assert first["annualized_excess_vs_bh"]["p05"] > 0


def test_bootstrap_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError, match="lengths"):
        run_bootstrap((0.0,), (), block_days=1, samples=1, seed=1)
    with pytest.raises(ValueError, match="positive"):
        run_bootstrap((0.0,), (0.0,), block_days=0, samples=1, seed=1)
