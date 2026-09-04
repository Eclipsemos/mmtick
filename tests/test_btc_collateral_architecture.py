import sys
from decimal import Decimal
from pathlib import Path

import pytest

from mastermind_tick.bar_research import ResearchBar

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from research_btc_collateral_architecture import (  # noqa: E402
    rebalance_wallets,
    replay_segregated,
)


def _bar(index, open_price="100", low="100", close="100"):
    start = index * 900_000
    values = tuple(Decimal(value) for value in (open_price, low, close))
    return ResearchBar(start, start + 899_999, values[0], max(values), values[1], values[2])


def test_rebalance_75pct_spot_creates_three_x_futures_sleeve() -> None:
    spot, futures, collateral, cost, fee = rebalance_wallets(
        Decimal("100000"),
        Decimal("100"),
        Decimal("1.5"),
        Decimal("0.75"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )

    assert spot == Decimal("750")
    assert futures == Decimal("750")
    assert collateral == Decimal("25000")
    assert futures * Decimal("100") / collateral == Decimal("3")
    assert cost == fee == 0


def test_segregated_wallet_liquidates_futures_without_using_spot_collateral() -> None:
    bars = [_bar(0), _bar(1, open_price="100", low="60", close="60")]
    result = replay_segregated(
        bars,
        (Decimal("1.5"), None),
        [[], []],
        bars[0].start_ms,
        bars[-1].end_ms,
        spot_cap=Decimal("0.75"),
        maintenance_rate=Decimal("0.004"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.liquidated is True


def test_open_gap_liquidates_before_target_can_reduce_exposure() -> None:
    bars = [
        _bar(0),
        _bar(1),
        _bar(2, open_price="60", low="60", close="60"),
    ]
    result = replay_segregated(
        bars,
        (Decimal("1.5"), Decimal("0"), None),
        [[], [], []],
        bars[0].start_ms,
        bars[-1].end_ms,
        spot_cap=Decimal("0.75"),
        maintenance_rate=Decimal("0.004"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.liquidated is True


def test_rebalance_rejects_target_above_three_x_futures_sleeve() -> None:
    with pytest.raises(ValueError, match="maximum futures leverage"):
        rebalance_wallets(
            Decimal("100000"),
            Decimal("100"),
            Decimal("1.75"),
            Decimal("0.75"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        )


def test_short_target_has_positive_collateral_and_three_x_bound() -> None:
    spot, futures, collateral, cost, fee = rebalance_wallets(
        Decimal("100000"),
        Decimal("100"),
        Decimal("-0.5"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )

    assert spot == Decimal("0")
    assert futures == Decimal("-500")
    assert collateral == Decimal("100000")
    assert abs(futures) * Decimal("100") / collateral == Decimal("0.5")
    assert cost == fee == 0


def test_short_replay_has_long_short_pnl_symmetry() -> None:
    bars = [_bar(0), _bar(1, open_price="100", low="90", close="90")]
    long_result = replay_segregated(
        bars,
        (Decimal("1"), None),
        [[], []],
        bars[0].start_ms,
        bars[-1].end_ms,
        spot_cap=Decimal("0"),
        maintenance_rate=Decimal("0.004"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    short_result = replay_segregated(
        bars,
        (Decimal("-1"), None),
        [[], []],
        bars[0].start_ms,
        bars[-1].end_ms,
        spot_cap=Decimal("0"),
        maintenance_rate=Decimal("0.004"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert short_result.net_return == pytest.approx(-long_result.net_return)


def test_segregated_wallet_rejects_one_x_spot_cap() -> None:
    with pytest.raises(ValueError, match="one exclusive"):
        replay_segregated(
            [_bar(0)],
            (Decimal("1"),),
            [[]],
            0,
            899_999,
            spot_cap=Decimal("1"),
            maintenance_rate=Decimal("0.004"),
        )


def test_recorded_equity_curve_ends_at_reported_net_return() -> None:
    bars = [_bar(0), _bar(1, open_price="100", low="100", close="110")]
    result = replay_segregated(
        bars,
        (Decimal("1"), None),
        [[], []],
        bars[0].start_ms,
        bars[-1].end_ms,
        spot_cap=Decimal("0"),
        maintenance_rate=Decimal("0.004"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        record_equity=True,
    )

    assert len(result.equity_curve) == 2
    assert result.equity_curve[-1][1] == pytest.approx(100000 * (1 + result.net_return))


def test_effective_leverage_cap_deleverages_after_collateral_loss() -> None:
    bars = [_bar(0), _bar(1, open_price="100", low="80", close="80"), _bar(2)]
    uncapped = replay_segregated(
        bars,
        (Decimal("1.5"), None, None),
        [[], [], []],
        bars[0].start_ms,
        bars[-1].end_ms,
        spot_cap=Decimal("0.75"),
        maintenance_rate=Decimal("0.004"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )
    capped = replay_segregated(
        bars,
        (Decimal("1.5"), None, None),
        [[], [], []],
        bars[0].start_ms,
        bars[-1].end_ms,
        spot_cap=Decimal("0.75"),
        maintenance_rate=Decimal("0.004"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        enforce_effective_leverage_cap=True,
    )

    assert uncapped.maximum_observed_futures_leverage > 3
    assert capped.maximum_controlled_open_futures_leverage <= 3


def test_custom_effective_leverage_cap_is_respected() -> None:
    bars = [_bar(0), _bar(1, open_price="80", low="80", close="80"), _bar(2)]
    result = replay_segregated(
        bars,
        (Decimal("1.5"), None, None),
        [[], [], []],
        bars[0].start_ms,
        bars[-1].end_ms,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.004"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("2.5"),
    )

    assert result.maximum_controlled_open_futures_leverage <= 2.5
