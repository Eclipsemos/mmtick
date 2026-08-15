from decimal import Decimal

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.factor_mining import (
    BASE_FEATURES,
    Formula,
    causal_features,
    evaluate_formula,
    factor_targets,
    formula_library,
)
from mastermind_tick.models import FundingRate


def _bars(count: int) -> list[ResearchBar]:
    bars = []
    for index in range(count):
        close = Decimal("100") + Decimal(index) / Decimal("10")
        start_ms = index * 15 * 60_000
        bars.append(
            ResearchBar(
                start_ms=start_ms,
                end_ms=start_ms + 15 * 60_000 - 1,
                open=close - Decimal("0.05"),
                high=close + Decimal("0.2"),
                low=close - Decimal("0.2"),
                close=close,
                volume=Decimal("1000") + Decimal(index * 3),
            )
        )
    return bars


def _funding(count: int) -> list[list[FundingRate]]:
    return [
        [
            FundingRate(
                timestamp_ms=index * 15 * 60_000,
                rate=Decimal("0.0001") if index % 32 == 0 else Decimal("0"),
                mark_price=Decimal("100"),
            )
        ]
        if index % 32 == 0
        else []
        for index in range(count)
    ]


def test_causal_features_do_not_change_when_future_bars_are_appended() -> None:
    original_bars = _bars(96)
    extended_bars = [*_bars(96), *_bars(8)]
    for index, bar in enumerate(extended_bars[96:], start=96):
        extended_bars[index] = ResearchBar(
            start_ms=bar.start_ms,
            end_ms=bar.end_ms,
            open=Decimal("1000000"),
            high=Decimal("2000000"),
            low=Decimal("1"),
            close=Decimal("1500000"),
            volume=Decimal("999999999"),
        )

    original = causal_features(original_bars, _funding(96))
    extended = causal_features(extended_bars, _funding(104))

    for name in BASE_FEATURES:
        assert extended[name][:96] == original[name]


def test_formula_validation_rejects_invalid_stack_programs() -> None:
    with pytest.raises(ValueError, match="requires two operands"):
        Formula(("ADD",))
    with pytest.raises(ValueError, match="exactly one"):
        Formula(("ret_1_z", "ret_4_z"))


def test_formula_delay_and_binary_operations_preserve_missing_values() -> None:
    features = {name: (None, Decimal("1"), Decimal("2"), Decimal("3")) for name in BASE_FEATURES}
    formula = Formula(("ret_1_z", "DELAY1", "ret_4_z", "ADD"))

    values = evaluate_formula(formula, features)

    assert values == (None, None, Decimal("3"), Decimal("5"))


def test_zero_threshold_keeps_zero_factor_flat() -> None:
    values = (None, Decimal("0"), Decimal("1"), Decimal("-1"))

    targets = factor_targets(values, Decimal("0"), "long_short")

    assert targets == (None, 0, 1, -1)


def test_formula_library_is_deterministic_and_valid() -> None:
    first = formula_library()
    second = formula_library()

    assert first == second
    assert len(first) == len(set(first))
    assert all(formula.display for formula in first)
