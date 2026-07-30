from math import sqrt
from statistics import stdev

import pytest

from mastermind_tick.reporting import _sharpe_ratio, _trade_stats


def test_sharpe_uses_last_equity_in_each_strategy_bar() -> None:
    minute = 60_000
    points = [
        {"timestamp_ms": 0, "equity": "100"},
        {"timestamp_ms": minute, "equity": "101"},
        {"timestamp_ms": 15 * minute, "equity": "102"},
        {"timestamp_ms": 30 * minute, "equity": "105"},
    ]
    returns = [102 / 101 - 1, 105 / 102 - 1]
    expected = sum(returns) / len(returns) / stdev(returns) * sqrt(365 * 24 * 4)

    assert _sharpe_ratio(points, 15) == pytest.approx(expected)


def test_sharpe_requires_two_nonconstant_returns() -> None:
    assert _sharpe_ratio([], 15) is None
    assert _sharpe_ratio(
        [
            {"timestamp_ms": 0, "equity": "100"},
            {"timestamp_ms": 900_000, "equity": "100"},
            {"timestamp_ms": 1_800_000, "equity": "100"},
        ],
        15,
    ) is None


def test_trade_stats_pair_round_trips_after_fees() -> None:
    fills = [
        {"side": "BUY", "timestamp_ms": 5, "quantity": "1", "notional": "100", "fee": "1"},
        {"side": "SELL", "timestamp_ms": 4, "quantity": "1", "notional": "90", "fee": "0.9"},
        {"side": "BUY", "timestamp_ms": 3, "quantity": "1", "notional": "100", "fee": "1"},
        {"side": "SELL", "timestamp_ms": 2, "quantity": "1", "notional": "110", "fee": "1.1"},
        {"side": "BUY", "timestamp_ms": 1, "quantity": "1", "notional": "100", "fee": "1"},
    ]

    assert _trade_stats(fills) == {
        "round_trips": 2,
        "winning_trades": 1,
        "losing_trades": 1,
        "win_rate": 0.5,
    }
