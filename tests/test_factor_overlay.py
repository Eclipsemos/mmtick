from decimal import Decimal

import pytest

from mastermind_tick.factor_overlay import (
    FactorOverlayConfig,
    MonthlyRiskConfig,
    causal_overlay_exposures,
    evaluate_factor_overlay,
    evaluate_monthly_risk_overlay,
)


def test_overlay_uses_only_returns_before_the_exposure_day() -> None:
    rows = (
        ("2026-01-01", Decimal("0.10")),
        ("2026-01-02", Decimal("0.10")),
        ("2026-01-03", Decimal("-0.90")),
    )
    config = FactorOverlayConfig(2, Decimal("0.20"), Decimal("0.5"), Decimal("2"))

    exposures = causal_overlay_exposures(rows, config)

    assert exposures[0][1] == Decimal("1")
    assert exposures[1][1] == Decimal("1")
    assert exposures[2][1] == Decimal("2")
    assert exposures[2][2] == Decimal("0.21")


def test_overlay_compounds_exposure_and_charges_turnover() -> None:
    rows = (
        ("2026-01-01", Decimal("0.10")),
        ("2026-01-02", Decimal("0.10")),
        ("2026-01-03", Decimal("0.10")),
    )
    config = FactorOverlayConfig(
        2,
        Decimal("0"),
        Decimal("0.5"),
        Decimal("2"),
        turnover_bps=Decimal("10"),
    )

    result = evaluate_factor_overlay(rows, config)

    assert result.daily_returns[-1] == ("2026-01-03", Decimal("0.199"))
    assert result.final_equity == Decimal("145079.0")


def test_overlay_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="exposures"):
        FactorOverlayConfig(20, Decimal("0"), Decimal("2"), Decimal("1"))


def test_overlay_can_use_an_aligned_external_factor_signal() -> None:
    returns = (
        ("2026-01-01", Decimal("0.10")),
        ("2026-01-02", Decimal("0.10")),
        ("2026-01-03", Decimal("0.10")),
    )
    signal = (
        ("2026-01-01", Decimal("-0.10")),
        ("2026-01-02", Decimal("-0.10")),
        ("2026-01-03", Decimal("0")),
    )
    config = FactorOverlayConfig(2, Decimal("0"), Decimal("0.5"), Decimal("2"))

    result = evaluate_factor_overlay(returns, config, signal_returns=signal)

    assert result.daily_returns[-1][1] < Decimal("0.10")


def test_monthly_overlay_holds_exposure_using_only_completed_months() -> None:
    rows = (
        ("2026-01-30", Decimal("0.10")),
        ("2026-01-31", Decimal("0.10")),
        ("2026-02-01", Decimal("-0.50")),
        ("2026-02-02", Decimal("-0.50")),
        ("2026-03-01", Decimal("0")),
    )
    config = FactorOverlayConfig(
        1,
        Decimal("0.20"),
        Decimal("0.5"),
        Decimal("2"),
        rebalance_frequency="monthly",
    )

    exposures = causal_overlay_exposures(rows, config)

    assert tuple(value for _label, value, _score in exposures[:2]) == (Decimal("1"),) * 2
    assert tuple(value for _label, value, _score in exposures[2:4]) == (Decimal("2"),) * 2
    assert exposures[4][1] == Decimal("0.5")


def test_monthly_risk_overlay_locks_profit_on_the_next_day_and_resets() -> None:
    rows = (
        ("2026-01-01", Decimal("0.20")),
        ("2026-01-02", Decimal("0.20")),
        ("2026-01-03", Decimal("-0.50")),
        ("2026-02-01", Decimal("0.10")),
    )
    config = MonthlyRiskConfig(
        Decimal("2"), Decimal("0.20"), Decimal("0.25"), turnover_bps=Decimal("0")
    )

    result = evaluate_monthly_risk_overlay(rows, config)

    assert result.daily_returns == (
        ("2026-01-01", Decimal("0.40")),
        ("2026-01-02", Decimal("0")),
        ("2026-01-03", Decimal("0")),
        ("2026-02-01", Decimal("0.20")),
    )
    assert result.monthly_returns == (
        ("2026-01", Decimal("0.40")),
        ("2026-02", Decimal("0.20")),
    )


def test_monthly_risk_overlay_stops_after_loss_limit_breach() -> None:
    rows = (
        ("2026-01-01", Decimal("-0.11")),
        ("2026-01-02", Decimal("0.50")),
    )

    result = evaluate_monthly_risk_overlay(
        rows,
        MonthlyRiskConfig(Decimal("1"), Decimal("0.10"), None, turnover_bps=Decimal("0")),
    )

    assert result.daily_returns[-1] == ("2026-01-02", Decimal("0"))
    assert result.net_return == Decimal("-0.11")
