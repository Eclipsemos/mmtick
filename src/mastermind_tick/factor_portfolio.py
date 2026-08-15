"""Research-only analytics for combining independently replayed factor sleeves."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

DailyReturns = tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class PortfolioResult:
    initial_equity: Decimal
    final_equity: Decimal
    net_return: Decimal
    max_drawdown: Decimal
    bankrupt: bool
    daily_returns: DailyReturns
    monthly_returns: DailyReturns

    @property
    def positive_month_rate(self) -> Decimal:
        if not self.monthly_returns:
            return Decimal("0")
        positive = sum(value > 0 for _label, value in self.monthly_returns)
        return Decimal(positive) / Decimal(len(self.monthly_returns))

    @property
    def target_month_rate(self) -> Decimal:
        if not self.monthly_returns:
            return Decimal("0")
        reached = sum(value >= Decimal("0.25") for _label, value in self.monthly_returns)
        return Decimal(reached) / Decimal(len(self.monthly_returns))

    @property
    def worst_month(self) -> Decimal:
        return min((value for _label, value in self.monthly_returns), default=Decimal("0"))

    def as_dict(self, *, include_daily: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "initial_equity": float(self.initial_equity),
                "final_equity": float(self.final_equity),
                "net_return": float(self.net_return),
                "max_drawdown": float(self.max_drawdown),
                "positive_month_rate": float(self.positive_month_rate),
                "target_25pct_month_rate": float(self.target_month_rate),
                "worst_month": float(self.worst_month),
                "monthly_returns": [
                    {"label": label, "return": float(value)}
                    for label, value in self.monthly_returns
                ],
                "daily_returns": (
                    [
                        {"label": label, "return": float(value)}
                        for label, value in self.daily_returns
                    ]
                    if include_daily
                    else []
                ),
            }
        )
        return payload


def decimal_returns(rows: tuple[tuple[str, float], ...]) -> DailyReturns:
    """Convert existing research result rows without carrying binary float artifacts."""
    return tuple((label, Decimal(str(value))) for label, value in rows)


def slice_returns(rows: DailyReturns, start: str, end: str) -> DailyReturns:
    if start > end:
        raise ValueError("daily return slice start must not exceed end")
    result = tuple((label, value) for label, value in rows if start <= label <= end)
    if not result:
        raise ValueError("daily return slice is empty")
    return result


def evaluate_static_portfolio(
    sleeves: dict[str, DailyReturns],
    allocations: dict[str, Decimal],
    *,
    leverage: Decimal = Decimal("1"),
    initial_equity: Decimal = Decimal("100000"),
) -> PortfolioResult:
    """Combine fixed-capital sleeves without implicit daily rebalancing.

    Sleeve equity compounds independently from its initial allocation. Portfolio leverage is a
    fixed initial borrowing amount, represented by a constant negative cash reserve.
    """
    if not sleeves:
        raise ValueError("portfolio requires at least one sleeve")
    if set(sleeves) != set(allocations):
        raise ValueError("portfolio sleeve and allocation names differ")
    if leverage <= 0 or initial_equity <= 0:
        raise ValueError("portfolio leverage and initial equity must be positive")
    if any(value < 0 for value in allocations.values()):
        raise ValueError("portfolio allocations must be non-negative")
    if sum(allocations.values(), Decimal("0")) != Decimal("1"):
        raise ValueError("portfolio allocations must sum to one")

    labels = _aligned_labels(sleeves)
    sleeve_returns = {name: dict(rows) for name, rows in sleeves.items()}
    equity = {name: initial_equity * leverage * allocations[name] for name in sleeves}
    reserve = initial_equity * (Decimal("1") - leverage)
    peak_equity = initial_equity
    max_drawdown = Decimal("0")
    daily_equity: list[tuple[str, Decimal]] = []
    bankrupt = False

    for label in labels:
        for name in sleeves:
            equity[name] *= Decimal("1") + sleeve_returns[name][label]
        total = reserve + sum(equity.values(), Decimal("0"))
        daily_equity.append((label, total))
        peak_equity = max(peak_equity, total)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, total / peak_equity - Decimal("1"))
        if total <= 0:
            bankrupt = True
            break
    final_equity = daily_equity[-1][1]
    return PortfolioResult(
        initial_equity=initial_equity,
        final_equity=final_equity,
        net_return=final_equity / initial_equity - Decimal("1"),
        max_drawdown=max_drawdown,
        bankrupt=bankrupt,
        daily_returns=_equity_returns(daily_equity, initial_equity, 10),
        monthly_returns=_equity_returns(daily_equity, initial_equity, 7),
    )


def return_correlation(left: DailyReturns, right: DailyReturns) -> Decimal:
    """Return Pearson correlation for exactly aligned return observations."""
    if tuple(label for label, _value in left) != tuple(label for label, _value in right):
        raise ValueError("correlation return labels are not aligned")
    if len(left) < 2:
        return Decimal("0")
    left_values = tuple(value for _label, value in left)
    right_values = tuple(value for _label, value in right)
    count = Decimal(len(left_values))
    left_mean = sum(left_values, Decimal("0")) / count
    right_mean = sum(right_values, Decimal("0")) / count
    covariance = sum(
        (first - left_mean) * (second - right_mean)
        for first, second in zip(left_values, right_values, strict=True)
    )
    left_variance = sum((value - left_mean) ** 2 for value in left_values)
    right_variance = sum((value - right_mean) ** 2 for value in right_values)
    denominator = (left_variance * right_variance).sqrt()
    return covariance / denominator if denominator else Decimal("0")


def monthly_returns(rows: DailyReturns) -> DailyReturns:
    """Compound labeled daily returns into calendar-month returns."""
    equity = Decimal("1")
    daily_equity = []
    for label, value in rows:
        equity *= Decimal("1") + value
        daily_equity.append((label, equity))
    return _equity_returns(daily_equity, Decimal("1"), 7)


def _aligned_labels(sleeves: dict[str, DailyReturns]) -> tuple[str, ...]:
    label_sets = [tuple(label for label, _value in rows) for rows in sleeves.values()]
    labels = label_sets[0]
    if not labels or any(candidate != labels for candidate in label_sets[1:]):
        raise ValueError("portfolio sleeve return labels are not aligned")
    return labels


def _equity_returns(
    equity_rows: list[tuple[str, Decimal]], initial_equity: Decimal, label_length: int
) -> DailyReturns:
    period_ends: dict[str, Decimal] = {}
    for label, equity in equity_rows:
        period_ends[label[:label_length]] = equity
    previous = initial_equity
    result = []
    for label, equity in period_ends.items():
        result.append((label, equity / previous - Decimal("1")))
        previous = equity
    return tuple(result)
