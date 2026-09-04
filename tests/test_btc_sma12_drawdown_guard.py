import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_sma12_drawdown_guard import build_dense_targets  # noqa: E402

from mastermind_tick.bar_research import ResearchBar  # noqa: E402


def daily_bar(index: int, close: str) -> ResearchBar:
    start = index * 86_400_000
    price = Decimal(close)
    return ResearchBar(
        start_ms=start,
        end_ms=start + 86_400_000 - 1,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
    )


def test_drawdown_guard_does_not_rewrite_past_targets() -> None:
    bars = [daily_bar(index, str(100 + index)) for index in range(60)]
    original = build_dense_targets(bars, 30, Decimal("0.15"), Decimal("0.75"))

    extended = build_dense_targets(
        bars + [daily_bar(60, "50")],
        30,
        Decimal("0.15"),
        Decimal("0.75"),
    )

    assert extended[: len(original)] == original
