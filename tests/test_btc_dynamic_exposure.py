import sys
from decimal import Decimal
from pathlib import Path

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.models import FundingRate

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_dynamic_exposure import (  # noqa: E402
    benchmark,
    replay_dynamic,
    replay_dynamic_constant_exposure_legacy,
    replay_dynamic_incremental,
)
from research_btc_funding_aware_exposure import funding_aware_targets  # noqa: E402


def _bar(index: int, *, open_price: str = "100", close_price: str = "100") -> ResearchBar:
    start_ms = index * 15 * 60_000
    open_value = Decimal(open_price)
    close_value = Decimal(close_price)
    return ResearchBar(
        start_ms=start_ms,
        end_ms=start_ms + 15 * 60_000 - 1,
        open=open_value,
        high=max(open_value, close_value),
        low=min(open_value, close_value),
        close=close_value,
    )


def test_replay_inherits_last_target_before_period_start() -> None:
    bars = [_bar(0), _bar(1), _bar(2), _bar(3, close_price="110")]
    targets = (Decimal("2"), None, None, None)

    result = replay_dynamic(
        bars,
        targets,
        None,
        bars[2].start_ms,
        bars[3].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.net_return == 0.2


def test_funding_can_apply_only_to_exposure_above_one_x() -> None:
    bars = [_bar(0), _bar(1)]
    targets = (Decimal("2"), None)
    funding = [
        [],
        [FundingRate(bars[1].start_ms, Decimal("0.01"), Decimal("100"))],
    ]

    result = replay_dynamic(
        bars,
        targets,
        funding,
        bars[1].start_ms,
        bars[1].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        funding_on_excess_only=True,
    )

    assert result.total_funding == -1000.0
    assert result.net_return == -0.01


def test_total_fees_are_not_double_counted() -> None:
    bars = [_bar(0)]
    result = replay_dynamic(
        bars,
        (Decimal("1"),),
        None,
        bars[0].start_ms,
        bars[0].end_ms,
        slippage_bps=Decimal("0"),
    )

    assert result.total_fees == 100.0


def test_incremental_replay_inherits_target_and_compounds_return() -> None:
    bars = [_bar(0), _bar(1), _bar(2), _bar(3, close_price="110")]
    result = replay_dynamic_incremental(
        bars,
        (Decimal("2"), None, None, None),
        None,
        bars[2].start_ms,
        bars[3].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.net_return == 0.2


def test_incremental_rebalance_charges_only_changed_notional() -> None:
    bars = [_bar(0), _bar(1)]
    result = replay_dynamic_incremental(
        bars,
        (Decimal("2"), None),
        None,
        bars[0].start_ms,
        bars[1].end_ms,
        slippage_bps=Decimal("0"),
    )

    assert 199.0 < result.total_fees < 201.0


def test_incremental_funding_applies_to_futures_overlay() -> None:
    bars = [_bar(0), _bar(1)]
    funding = [
        [],
        [FundingRate(bars[1].start_ms, Decimal("0.01"), Decimal("100"))],
    ]
    result = replay_dynamic_incremental(
        bars,
        (Decimal("2"), None),
        funding,
        bars[1].start_ms,
        bars[1].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        funding_on_excess_only=True,
    )

    assert result.total_funding == -1000.0
    assert result.net_return == -0.01


def test_incremental_equity_curve_matches_final_result() -> None:
    bars = [_bar(0), _bar(1, close_price="110")]

    result = replay_dynamic_incremental(
        bars,
        (Decimal("1"), None),
        None,
        bars[0].start_ms,
        bars[-1].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        record_equity=True,
    )

    assert result.equity_curve == ((bars[0].end_ms, 100000.0), (bars[1].end_ms, 110000.0))
    assert result.equity_curve[-1][1] / 100000 - 1 == pytest.approx(result.net_return)


def test_incremental_equity_curve_includes_final_close_cost() -> None:
    bars = [_bar(0)]

    result = replay_dynamic_incremental(
        bars,
        (Decimal("1"),),
        None,
        bars[0].start_ms,
        bars[0].end_ms,
        slippage_bps=Decimal("0"),
        record_equity=True,
    )

    assert result.equity_curve[-1][1] / 100000 - 1 == pytest.approx(result.net_return)


def test_incremental_exposure_curve_records_active_target() -> None:
    bars = [_bar(0), _bar(1)]
    result = replay_dynamic_incremental(
        bars,
        (Decimal("1.5"), None),
        None,
        bars[0].start_ms,
        bars[-1].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        record_exposure=True,
    )

    assert result.exposure_curve == ((bars[0].end_ms, 1.0), (bars[1].end_ms, 1.5))


def test_incremental_risk_curve_records_intrabar_equity_and_futures_notional() -> None:
    bars = [_bar(0), _bar(1, open_price="100", close_price="110")]
    result = replay_dynamic_incremental(
        bars,
        (Decimal("1.5"), None),
        None,
        bars[0].start_ms,
        bars[-1].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        record_risk=True,
    )

    assert result.risk_curve[-1][1] == pytest.approx(100000.0)
    assert result.risk_curve[-1][2] == pytest.approx(50000.0)
    assert result.risk_curve[-1][3] == pytest.approx(150000.0)
    assert result.risk_curve[-1][4] == pytest.approx(1.5)


def test_spot_cap_moves_neutral_exposure_into_futures_sleeve() -> None:
    bars = [_bar(0), _bar(1)]
    funding = [[], [FundingRate(bars[1].start_ms, Decimal("0.01"), Decimal("100"))]]
    result = replay_dynamic_incremental(
        bars,
        (Decimal("1"), None),
        funding,
        bars[0].start_ms,
        bars[-1].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        funding_on_excess_only=True,
        spot_exposure_cap=Decimal("0.5"),
        record_risk=True,
    )

    assert result.total_funding == pytest.approx(-500.0)
    assert result.risk_curve[-1][2] == pytest.approx(50000.0)


def test_spot_cap_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="spot exposure cap"):
        replay_dynamic_incremental(
            [_bar(0)],
            (Decimal("1"),),
            None,
            0,
            899_999,
            spot_exposure_cap=Decimal("1.1"),
        )


def test_incremental_replay_keeps_quantity_fixed_between_target_changes() -> None:
    bars = [
        _bar(0),
        _bar(1, open_price="100", close_price="110"),
        _bar(2, open_price="110", close_price="121"),
    ]
    targets = (Decimal("2"), None, None)

    fixed = replay_dynamic_incremental(
        bars,
        targets,
        None,
        bars[1].start_ms,
        bars[2].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    legacy = replay_dynamic_constant_exposure_legacy(
        bars,
        targets,
        None,
        bars[1].start_ms,
        bars[2].end_ms,
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert fixed.net_return == pytest.approx(0.42)
    assert legacy.net_return == pytest.approx(0.44)


def test_benchmark_uses_first_open_and_intrabar_low_for_drawdown() -> None:
    bars = [
        ResearchBar(
            start_ms=0,
            end_ms=899_999,
            open=Decimal("100"),
            high=Decimal("115"),
            low=Decimal("90"),
            close=Decimal("110"),
        ),
        ResearchBar(
            start_ms=900_000,
            end_ms=1_799_999,
            open=Decimal("110"),
            high=Decimal("112"),
            low=Decimal("80"),
            close=Decimal("100"),
        ),
    ]

    result = benchmark(bars, bars[0].start_ms, bars[-1].end_ms)

    assert result["net_return"] == 0.0
    assert result["max_drawdown"] == pytest.approx(0.8 / 1.1 - 1)


def test_funding_filter_uses_only_rate_known_on_current_bar() -> None:
    bars = [_bar(0), _bar(1), _bar(2)]
    funding = [
        [],
        [FundingRate(bars[1].start_ms, Decimal("0.0002"), Decimal("100"))],
        [],
    ]

    targets = funding_aware_targets(
        (Decimal("1.5"), None, None),
        funding,
        Decimal("1.5"),
        Decimal("0.0001"),
    )

    assert targets == (Decimal("1.5"), Decimal("1"), None)
