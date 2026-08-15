"""Shared-equity multi-asset factor book replay."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.factor_portfolio import DailyReturns, PortfolioResult, monthly_returns
from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class FactorBookResult:
    portfolio: PortfolioResult
    total_fees: Decimal
    total_funding: Decimal
    rebalance_count: int

    def as_dict(self, *, include_daily: bool = False) -> dict[str, Any]:
        return {
            **self.portfolio.as_dict(include_daily=include_daily),
            "total_fees": float(self.total_fees),
            "total_funding": float(self.total_funding),
            "rebalance_count": self.rebalance_count,
        }


def evaluate_factor_book(
    bars: dict[str, list[ResearchBar]],
    targets: dict[str, tuple[Decimal, ...]],
    *,
    start_ms: int,
    end_ms: int,
    funding: dict[str, list[list[FundingRate]]] | None = None,
    initial_equity: Decimal = Decimal("100000"),
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
) -> FactorBookResult:
    """Replay aligned futures targets in one mark-to-market equity account."""
    names = tuple(bars)
    if not names or set(targets) != set(names):
        raise ValueError("factor book bars and targets are empty or inconsistent")
    if initial_equity <= 0 or fee_bps < 0 or slippage_bps < 0:
        raise ValueError("factor book equity and costs are invalid")
    length = len(bars[names[0]])
    if any(len(bars[name]) != length or len(targets[name]) != length for name in names):
        raise ValueError("factor book inputs have inconsistent lengths")
    if any(
        bars[name][index].start_ms != bars[names[0]][index].start_ms
        for name in names[1:]
        for index in range(length)
    ):
        raise ValueError("factor book bars are not aligned")
    if funding is None:
        funding = {name: [[] for _bar in bars[name]] for name in names}
    if set(funding) != set(names) or any(len(funding[name]) != length for name in names):
        raise ValueError("factor book funding is inconsistent")
    selected = [
        index for index, bar in enumerate(bars[names[0]]) if start_ms <= bar.start_ms <= end_ms
    ]
    if not selected:
        raise ValueError("factor book evaluation period is empty")

    cost_rate = (fee_bps + slippage_bps) / Decimal("10000")
    cash = initial_equity
    positions = {name: Decimal("0") for name in names}
    current_targets = {name: Decimal("0") for name in names}
    previous_index = selected[0] - 1
    pending_targets = {
        name: targets[name][previous_index] if previous_index >= 0 else Decimal("0")
        for name in names
    }
    previous_closes = {
        name: bars[name][previous_index].close
        if previous_index >= 0
        else bars[name][selected[0]].open
        for name in names
    }
    peak = initial_equity
    max_drawdown = Decimal("0")
    daily_equity: dict[str, Decimal] = {}
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    rebalance_count = 0
    bankrupt = False

    for index in selected:
        for name in names:
            cash += positions[name] * (bars[name][index].open - previous_closes[name])
        if pending_targets != current_targets:
            rebalance_equity = cash
            for name in names:
                price = bars[name][index].open
                desired = rebalance_equity * pending_targets[name] / price
                turnover = abs(desired - positions[name]) * price
                cost = turnover * cost_rate
                cash -= cost
                total_fees += cost
                positions[name] = desired
                current_targets[name] = pending_targets[name]
            rebalance_count += 1
        for name in names:
            for event in funding[name][index]:
                amount = -(positions[name] * event.mark_price * event.rate)
                cash += amount
                total_funding += amount
            cash += positions[name] * (bars[name][index].close - bars[name][index].open)
            previous_closes[name] = bars[name][index].close
        peak = max(peak, cash)
        if peak > 0:
            max_drawdown = min(max_drawdown, cash / peak - Decimal("1"))
        label = _date_label(bars[names[0]][index].end_ms)
        daily_equity[label] = cash
        if cash <= 0:
            bankrupt = True
            break
        pending_targets = {name: targets[name][index] for name in names}

    if not bankrupt:
        close_cost = sum(
            (abs(positions[name]) * bars[name][selected[-1]].close * cost_rate for name in names),
            Decimal("0"),
        )
        cash -= close_cost
        total_fees += close_cost
        daily_equity[_date_label(bars[names[0]][selected[-1]].end_ms)] = cash
        peak = max(peak, cash)
        if peak > 0:
            max_drawdown = min(max_drawdown, cash / peak - Decimal("1"))
    daily = _equity_returns(daily_equity, initial_equity)
    portfolio = PortfolioResult(
        initial_equity=initial_equity,
        final_equity=cash,
        net_return=cash / initial_equity - Decimal("1"),
        max_drawdown=max_drawdown,
        bankrupt=bankrupt,
        daily_returns=daily,
        monthly_returns=monthly_returns(daily),
    )
    return FactorBookResult(portfolio, total_fees, total_funding, rebalance_count)


def _equity_returns(values: dict[str, Decimal], initial: Decimal) -> DailyReturns:
    previous = initial
    result = []
    for label, equity in values.items():
        result.append((label, equity / previous - Decimal("1")))
        previous = equity
    return tuple(result)


def _date_label(timestamp_ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).date().isoformat()
