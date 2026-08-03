from decimal import Decimal
from math import sqrt
from statistics import stdev

import pytest

from mastermind_tick.config import load_settings
from mastermind_tick.reporting import _live_account_valuation, _sharpe_ratio, _trade_stats


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


def test_trade_stats_include_funding_during_round_trip() -> None:
    fills = [
        {"side": "SELL", "timestamp_ms": 3, "quantity": "1", "notional": "101", "fee": "0"},
        {"side": "BUY", "timestamp_ms": 1, "quantity": "1", "notional": "100", "fee": "0"},
    ]
    funding = [{"timestamp_ms": 2, "amount": "-2"}]

    assert _trade_stats(fills, funding)["win_rate"] == 0


def test_trade_stats_support_short_and_long_reversal_legs() -> None:
    fills = [
        {
            "side": "SELL",
            "timestamp_ms": 3,
            "quantity": "1",
            "notional": "90",
            "fee": "1",
            "position_effect": "CLOSE",
            "realized_pnl": "-11",
        },
        {
            "side": "BUY",
            "timestamp_ms": 2,
            "quantity": "1",
            "notional": "100",
            "fee": "1",
            "position_effect": "OPEN",
            "realized_pnl": "-1",
        },
        {
            "side": "BUY",
            "timestamp_ms": 2,
            "quantity": "1",
            "notional": "100",
            "fee": "1",
            "position_effect": "CLOSE",
            "realized_pnl": "9",
        },
        {
            "side": "SELL",
            "timestamp_ms": 1,
            "quantity": "1",
            "notional": "110",
            "fee": "1",
            "position_effect": "OPEN",
            "realized_pnl": "-1",
        },
    ]

    assert _trade_stats(fills) == {
        "round_trips": 2,
        "winning_trades": 1,
        "losing_trades": 1,
        "win_rate": 0.5,
    }


def test_live_futures_valuation_uses_tick_and_rejects_stale_mark_price() -> None:
    settings = load_settings("config/settings.toml")
    instrument = next(item for item in settings.instruments if item.id == "soxl_perp")
    account = {
        "cash": "100000",
        "quantity": "-10",
        "average_price": "100",
        "paper_model": instrument.paper_model,
        "leverage": instrument.leverage,
    }
    latest = {
        "timestamp_ms": 50_000,
        "price": "100",
        "equity": "100000",
        "unrealized_pnl": "0",
        "market_value": "1000",
        "mark_price": "100",
        "index_price": "100",
        "funding_rate": "0",
        "initial_margin": "500",
        "available_balance": "99500",
    }
    runtime = {
        "market_state": {"updated_at_ms": 60_000},
        "last_tick": {
            "timestamp_ms": 100_000,
            "price": "90",
            "mark_price": "95",
            "index_price": "94.9",
            "funding_rate": "0.001",
        },
    }

    stale = _live_account_valuation(account, latest, runtime)

    assert Decimal(stale["equity"]) == Decimal("100100")
    assert stale["mark_price"] == "90"
    assert stale["index_price"] is None
    assert stale["timestamp_ms"] == 100_000

    runtime["market_state"] = {"updated_at_ms": 99_000}
    fresh = _live_account_valuation(account, latest, runtime)

    assert Decimal(fresh["equity"]) == Decimal("100050")
    assert fresh["mark_price"] == "95"
    assert fresh["index_price"] == "94.9"
