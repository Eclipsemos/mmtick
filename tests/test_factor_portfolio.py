from decimal import Decimal

import pytest

from mastermind_tick.factor_portfolio import (
    evaluate_static_portfolio,
    monthly_returns,
    return_correlation,
    slice_returns,
)


def test_static_portfolio_compounds_sleeves_without_daily_rebalancing() -> None:
    result = evaluate_static_portfolio(
        {
            "first": (("2026-01-01", Decimal("0.10")), ("2026-01-02", Decimal("0.10"))),
            "second": (("2026-01-01", Decimal("0")), ("2026-01-02", Decimal("0"))),
        },
        {"first": Decimal("0.5"), "second": Decimal("0.5")},
    )

    assert result.final_equity == Decimal("110500")
    assert result.net_return == Decimal("0.105")


def test_portfolio_leverage_uses_fixed_initial_reserve() -> None:
    result = evaluate_static_portfolio(
        {"factor": (("2026-01-01", Decimal("0.10")),)},
        {"factor": Decimal("1")},
        leverage=Decimal("2"),
    )

    assert result.final_equity == Decimal("120000")
    assert result.net_return == Decimal("0.20")


def test_portfolio_reports_calendar_month_returns_and_drawdown() -> None:
    result = evaluate_static_portfolio(
        {
            "factor": (
                ("2026-01-31", Decimal("0.30")),
                ("2026-02-01", Decimal("-0.20")),
            )
        },
        {"factor": Decimal("1")},
    )

    assert result.monthly_returns == (
        ("2026-01", Decimal("0.30")),
        ("2026-02", Decimal("-0.20")),
    )
    assert result.target_month_rate == Decimal("0.5")
    assert result.max_drawdown == Decimal("-0.20")


def test_portfolio_rejects_unaligned_daily_labels() -> None:
    with pytest.raises(ValueError, match="not aligned"):
        evaluate_static_portfolio(
            {
                "first": (("2026-01-01", Decimal("0")),),
                "second": (("2026-01-02", Decimal("0")),),
            },
            {"first": Decimal("0.5"), "second": Decimal("0.5")},
        )


def test_monthly_correlation_and_slice_are_deterministic() -> None:
    left = (
        ("2026-01-01", Decimal("0.10")),
        ("2026-01-02", Decimal("0")),
        ("2026-02-01", Decimal("-0.10")),
    )
    right = (
        ("2026-01-01", Decimal("-0.10")),
        ("2026-01-02", Decimal("0")),
        ("2026-02-01", Decimal("0.10")),
    )

    assert slice_returns(left, "2026-01-02", "2026-02-01") == left[1:]
    assert return_correlation(monthly_returns(left), monthly_returns(right)) < Decimal("-0.99")
