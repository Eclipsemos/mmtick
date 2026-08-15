"""Research-only analytics for combining independently replayed factor sleeves."""

from __future__ import annotations

from bisect import bisect_left
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


@dataclass(frozen=True)
class AdaptivePortfolioConfig:
    lookback_days: int
    top_k: int
    scoring: str
    weighting: str
    leverage: Decimal
    rebalance_bps: Decimal = Decimal("7")
    monthly_loss_limit: Decimal | None = None
    anchor_name: str | None = None
    anchor_weight: Decimal = Decimal("0")


@dataclass(frozen=True)
class AllocationRecord:
    month: str
    weights: tuple[tuple[str, Decimal], ...]
    turnover: Decimal
    cost: Decimal


@dataclass(frozen=True)
class AdaptivePortfolioResult(PortfolioResult):
    rebalance_costs: Decimal
    rebalance_count: int
    allocation_history: tuple[AllocationRecord, ...]

    def as_dict(self, *, include_daily: bool = False) -> dict[str, Any]:
        payload = super().as_dict(include_daily=include_daily)
        payload.update(
            {
                "rebalance_costs": float(self.rebalance_costs),
                "rebalance_count": self.rebalance_count,
                "allocation_history": [
                    {
                        "month": record.month,
                        "weights": {name: float(weight) for name, weight in record.weights},
                        "turnover": float(record.turnover),
                        "cost": float(record.cost),
                    }
                    for record in self.allocation_history
                ],
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


def evaluate_adaptive_portfolio(
    sleeves: dict[str, DailyReturns],
    config: AdaptivePortfolioConfig,
    *,
    start: str,
    end: str,
    initial_equity: Decimal = Decimal("100000"),
) -> AdaptivePortfolioResult:
    """Rotate fixed-capital sleeves monthly using trailing data available before allocation."""
    _validate_adaptive_config(sleeves, config, start, end, initial_equity)
    labels = _aligned_labels(sleeves)
    selected_labels = tuple(label for label in labels if start <= label <= end)
    if not selected_labels:
        raise ValueError("adaptive portfolio evaluation period is empty")
    sleeve_returns = {name: dict(rows) for name, rows in sleeves.items()}
    sleeve_equity = {name: Decimal("0") for name in sleeves}
    reserve = initial_equity
    current_weights: dict[str, Decimal] = {}
    current_month: str | None = None
    month_start_equity = initial_equity
    paused_for_month = False
    peak_equity = initial_equity
    max_drawdown = Decimal("0")
    rebalance_costs = Decimal("0")
    allocations: list[AllocationRecord] = []
    daily_equity: list[tuple[str, Decimal]] = []
    bankrupt = False
    rate = config.rebalance_bps / Decimal("10000")

    def total_equity() -> Decimal:
        return reserve + sum(sleeve_equity.values(), Decimal("0"))

    for label in selected_labels:
        month = label[:7]
        if month != current_month:
            current_month = month
            paused_for_month = False
            total = total_equity()
            next_weights = _adaptive_weights(sleeves, config, label)
            old_notional = {
                name: config.leverage * current_weights.get(name, Decimal("0")) for name in sleeves
            }
            new_notional = {
                name: config.leverage * next_weights.get(name, Decimal("0")) for name in sleeves
            }
            turnover = (
                sum(
                    (abs(new_notional[name] - old_notional[name]) for name in sleeves),
                    Decimal("0"),
                )
                if allocations
                else Decimal("0")
            )
            cost = max(total, Decimal("0")) * turnover * rate
            total -= cost
            rebalance_costs += cost
            current_weights = next_weights
            invested_fraction = sum(current_weights.values(), Decimal("0"))
            reserve = total * (Decimal("1") - config.leverage * invested_fraction)
            sleeve_equity = {
                name: total * config.leverage * current_weights.get(name, Decimal("0"))
                for name in sleeves
            }
            month_start_equity = total
            allocations.append(
                AllocationRecord(
                    month=month,
                    weights=tuple(sorted(current_weights.items())),
                    turnover=turnover,
                    cost=cost,
                )
            )

        if not paused_for_month:
            for name in current_weights:
                sleeve_equity[name] *= Decimal("1") + sleeve_returns[name][label]
        total = total_equity()
        if (
            not paused_for_month
            and current_weights
            and config.monthly_loss_limit is not None
            and month_start_equity > 0
            and total / month_start_equity - Decimal("1") <= -config.monthly_loss_limit
        ):
            close_turnover = config.leverage * sum(current_weights.values(), Decimal("0"))
            close_cost = max(total, Decimal("0")) * close_turnover * rate
            total -= close_cost
            rebalance_costs += close_cost
            reserve = total
            sleeve_equity = {name: Decimal("0") for name in sleeves}
            current_weights = {}
            paused_for_month = True
        daily_equity.append((label, total))
        peak_equity = max(peak_equity, total)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, total / peak_equity - Decimal("1"))
        if total <= 0:
            bankrupt = True
            break

    final_equity = daily_equity[-1][1]
    return AdaptivePortfolioResult(
        initial_equity=initial_equity,
        final_equity=final_equity,
        net_return=final_equity / initial_equity - Decimal("1"),
        max_drawdown=max_drawdown,
        bankrupt=bankrupt,
        daily_returns=_equity_returns(daily_equity, initial_equity, 10),
        monthly_returns=_equity_returns(daily_equity, initial_equity, 7),
        rebalance_costs=rebalance_costs,
        rebalance_count=len(allocations),
        allocation_history=tuple(allocations),
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


def _validate_adaptive_config(
    sleeves: dict[str, DailyReturns],
    config: AdaptivePortfolioConfig,
    start: str,
    end: str,
    initial_equity: Decimal,
) -> None:
    if not sleeves:
        raise ValueError("adaptive portfolio requires at least one sleeve")
    if start > end or initial_equity <= 0:
        raise ValueError("adaptive portfolio period or initial equity is invalid")
    if config.lookback_days < 5 or config.top_k < 1:
        raise ValueError("adaptive portfolio lookback and top-k must be positive")
    if config.scoring not in {"return", "calmar"}:
        raise ValueError("adaptive portfolio scoring is unsupported")
    if config.weighting not in {"equal", "score"}:
        raise ValueError("adaptive portfolio weighting is unsupported")
    if config.leverage <= 0 or config.rebalance_bps < 0:
        raise ValueError("adaptive portfolio leverage or cost is invalid")
    if config.monthly_loss_limit is not None and not (
        Decimal("0") < config.monthly_loss_limit < Decimal("1")
    ):
        raise ValueError("adaptive portfolio monthly loss limit is invalid")
    if not Decimal("0") <= config.anchor_weight <= Decimal("1"):
        raise ValueError("adaptive portfolio anchor weight is invalid")
    if config.anchor_weight and config.anchor_name not in sleeves:
        raise ValueError("adaptive portfolio anchor sleeve is missing")


def _adaptive_weights(
    sleeves: dict[str, DailyReturns],
    config: AdaptivePortfolioConfig,
    allocation_day: str,
) -> dict[str, Decimal]:
    anchor_weight = config.anchor_weight if config.anchor_name else Decimal("0")
    reference = next(iter(sleeves.values()))
    history_end = bisect_left(tuple(label for label, _value in reference), allocation_day)
    candidates = []
    for name, rows in sleeves.items():
        if anchor_weight and name == config.anchor_name:
            continue
        history_start = max(0, history_end - config.lookback_days)
        history = tuple(value for _label, value in rows[history_start:history_end])
        if len(history) < config.lookback_days:
            continue
        cumulative, max_drawdown = _trailing_metrics(history)
        if cumulative <= 0:
            continue
        score = (
            cumulative
            if config.scoring == "return"
            else cumulative / max(abs(max_drawdown), Decimal("0.01"))
        )
        candidates.append((name, score))
    ranked = sorted(candidates, key=lambda item: (item[1], item[0]), reverse=True)[: config.top_k]
    available = Decimal("1") - anchor_weight
    weights: dict[str, Decimal] = {}
    if anchor_weight and config.anchor_name:
        weights[config.anchor_name] = anchor_weight
    if not ranked or available <= 0:
        return weights
    if config.weighting == "equal":
        value = available / Decimal(len(ranked))
        weights.update({name: value for name, _score in ranked})
    else:
        total_score = sum((score for _name, score in ranked), Decimal("0"))
        weights.update({name: available * score / total_score for name, score in ranked})
    return weights


def _trailing_metrics(values: tuple[Decimal, ...]) -> tuple[Decimal, Decimal]:
    equity = Decimal("1")
    peak = equity
    max_drawdown = Decimal("0")
    for value in values:
        equity *= Decimal("1") + value
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - Decimal("1"))
    return equity - Decimal("1"), max_drawdown


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
