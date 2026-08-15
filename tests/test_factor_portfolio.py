from decimal import Decimal

import pytest

from mastermind_tick.factor_portfolio import (
    AdaptivePortfolioConfig,
    evaluate_adaptive_portfolio,
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


def test_adaptive_portfolio_uses_only_trailing_returns_for_monthly_weights() -> None:
    warmup = tuple((f"2026-01-{day:02d}", Decimal("0.01")) for day in range(1, 11))
    weak_warmup = tuple((f"2026-01-{day:02d}", Decimal("-0.01")) for day in range(1, 11))
    evaluation = (("2026-02-01", Decimal("0.10")), ("2026-02-02", Decimal("0")))
    result = evaluate_adaptive_portfolio(
        {"strong": (*warmup, *evaluation), "weak": (*weak_warmup, *evaluation)},
        AdaptivePortfolioConfig(10, 1, "return", "equal", Decimal("1")),
        start="2026-02-01",
        end="2026-02-02",
    )

    assert result.net_return == Decimal("0.10")
    assert result.allocation_history[0].weights == (("strong", Decimal("1")),)


def test_adaptive_portfolio_charges_turnover_when_leader_rotates() -> None:
    first = tuple(
        (f"2026-01-{day:02d}", Decimal("0.01") if day <= 5 else Decimal("-0.02"))
        for day in range(1, 11)
    )
    second = tuple(
        (f"2026-01-{day:02d}", Decimal("-0.01") if day <= 5 else Decimal("0.03"))
        for day in range(1, 11)
    )
    february = tuple(
        (f"2026-02-{day:02d}", Decimal("-0.01") if day <= 5 else Decimal("0.04"))
        for day in range(1, 11)
    )
    other_february = tuple(
        (f"2026-02-{day:02d}", Decimal("0.02") if day <= 5 else Decimal("-0.03"))
        for day in range(1, 11)
    )
    march = (("2026-03-01", Decimal("0")),)
    result = evaluate_adaptive_portfolio(
        {"first": (*first, *february, *march), "second": (*second, *other_february, *march)},
        AdaptivePortfolioConfig(
            5,
            1,
            "return",
            "equal",
            Decimal("1"),
            rebalance_bps=Decimal("10"),
        ),
        start="2026-02-01",
        end="2026-03-01",
    )

    assert result.rebalance_count == 2
    assert result.allocation_history[1].turnover == Decimal("2")
    assert result.rebalance_costs > 0


def test_adaptive_monthly_loss_limit_moves_portfolio_to_cash() -> None:
    rows = tuple((f"2026-01-{day:02d}", Decimal("0.01")) for day in range(1, 6)) + (
        ("2026-02-01", Decimal("-0.11")),
        ("2026-02-02", Decimal("-0.50")),
    )
    result = evaluate_adaptive_portfolio(
        {"factor": rows},
        AdaptivePortfolioConfig(
            5,
            1,
            "return",
            "equal",
            Decimal("1"),
            monthly_loss_limit=Decimal("0.10"),
        ),
        start="2026-02-01",
        end="2026-02-02",
    )

    assert result.daily_returns[-1][1] == 0
    assert result.net_return > Decimal("-0.12")
