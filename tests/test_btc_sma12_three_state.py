import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_sma12_three_state import build_dense_targets  # noqa: E402

from mastermind_tick.bar_research import ResearchBar  # noqa: E402


def bars_from_closes(closes: list[int]) -> list[ResearchBar]:
    bars = []
    for index, close in enumerate(closes):
        price = Decimal(close)
        start = index * 86_400_000
        bars.append(
            ResearchBar(
                start_ms=start,
                end_ms=start + 86_400_000 - 1,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=Decimal("1"),
            )
        )
    return bars


def test_three_state_classifies_bull_neutral_and_bear() -> None:
    bull = build_dense_targets(
        bars_from_closes(list(range(100, 150))), Decimal("1"), Decimal("1.5")
    )
    neutral = build_dense_targets(bars_from_closes([100] * 50), Decimal("1"), Decimal("1.5"))
    bear = build_dense_targets(
        bars_from_closes(list(range(150, 100, -1))), Decimal("1"), Decimal("1.5")
    )

    assert bull[-1] == Decimal("1.5")
    assert neutral[-1] == Decimal("1")
    assert bear[-1] == Decimal("0")


def test_three_state_does_not_rewrite_past_targets() -> None:
    bars = bars_from_closes(list(range(100, 150)))
    original = build_dense_targets(bars, Decimal("1"), Decimal("1.5"))
    extended = build_dense_targets(bars + bars_from_closes([50]), Decimal("1"), Decimal("1.5"))

    assert extended[: len(original)] == original


def test_three_state_rejects_invalid_sma_periods() -> None:
    bars = bars_from_closes([100] * 50)

    try:
        build_dense_targets(
            bars,
            Decimal("1"),
            Decimal("1.5"),
            fast_period=40,
            slow_period=12,
        )
    except ValueError as exc:
        assert "fast must be below slow" in str(exc)
    else:
        raise AssertionError("invalid SMA periods must be rejected")
