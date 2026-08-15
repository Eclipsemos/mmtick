from decimal import Decimal

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.factor_book import evaluate_factor_book


def _bar(start: int, open_price: str, close_price: str) -> ResearchBar:
    return ResearchBar(
        start_ms=start,
        end_ms=start + 1,
        open=Decimal(open_price),
        high=max(Decimal(open_price), Decimal(close_price)),
        low=min(Decimal(open_price), Decimal(close_price)),
        close=Decimal(close_price),
    )


def test_factor_book_applies_closed_bar_target_at_next_open() -> None:
    bars = {"btc": [_bar(0, "100", "100"), _bar(2, "100", "110")]}

    result = evaluate_factor_book(
        bars,
        {"btc": (Decimal("1"), Decimal("1"))},
        start_ms=0,
        end_ms=2,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.portfolio.net_return == Decimal("0.10")


def test_factor_book_offsets_equal_long_and_short_returns() -> None:
    bars = {
        "btc": [_bar(0, "100", "100"), _bar(2, "100", "110")],
        "eth": [_bar(0, "100", "100"), _bar(2, "100", "110")],
    }

    result = evaluate_factor_book(
        bars,
        {
            "btc": (Decimal("0.5"), Decimal("0.5")),
            "eth": (Decimal("-0.5"), Decimal("-0.5")),
        },
        start_ms=0,
        end_ms=2,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.portfolio.net_return == Decimal("0")


def test_factor_book_rejects_unaligned_bars() -> None:
    with pytest.raises(ValueError, match="not aligned"):
        evaluate_factor_book(
            {
                "btc": [_bar(0, "100", "100")],
                "eth": [_bar(1, "100", "100")],
            },
            {"btc": (Decimal("0"),), "eth": (Decimal("0"),)},
            start_ms=0,
            end_ms=2,
        )
