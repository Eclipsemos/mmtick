"""Causal exposure overlays for research factor portfolios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from mastermind_tick.factor_portfolio import DailyReturns, PortfolioResult, monthly_returns


@dataclass(frozen=True)
class FactorOverlayConfig:
    lookback_periods: int
    threshold: Decimal
    low_exposure: Decimal
    high_exposure: Decimal
    mode: str = "momentum"
    rebalance_frequency: str = "daily"
    turnover_bps: Decimal = Decimal("7")

    def __post_init__(self) -> None:
        if self.lookback_periods < 1:
            raise ValueError("factor overlay lookback must be positive")
        if self.mode not in {"momentum", "contrarian"}:
            raise ValueError("factor overlay mode is unsupported")
        if self.rebalance_frequency not in {"daily", "monthly"}:
            raise ValueError("factor overlay rebalance frequency is unsupported")
        if self.low_exposure < 0 or self.high_exposure <= self.low_exposure:
            raise ValueError("factor overlay exposures are invalid")
        if self.high_exposure > Decimal("10") or self.turnover_bps < 0:
            raise ValueError("factor overlay exposure or cost is invalid")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            **payload,
            "threshold": float(self.threshold),
            "low_exposure": float(self.low_exposure),
            "high_exposure": float(self.high_exposure),
            "turnover_bps": float(self.turnover_bps),
        }


@dataclass(frozen=True)
class MonthlyRiskConfig:
    leverage: Decimal
    loss_limit: Decimal
    profit_target: Decimal | None
    turnover_bps: Decimal = Decimal("7")

    def __post_init__(self) -> None:
        if self.leverage <= 0 or self.leverage > Decimal("10"):
            raise ValueError("monthly risk leverage is invalid")
        if not Decimal("0") < self.loss_limit < Decimal("1"):
            raise ValueError("monthly risk loss limit is invalid")
        if self.profit_target is not None and not Decimal("0") < self.profit_target:
            raise ValueError("monthly risk profit target is invalid")
        if self.turnover_bps < 0:
            raise ValueError("monthly risk turnover cost is invalid")

    def as_dict(self) -> dict[str, Any]:
        return {
            "leverage": float(self.leverage),
            "loss_limit": float(self.loss_limit),
            "profit_target": float(self.profit_target) if self.profit_target is not None else None,
            "turnover_bps": float(self.turnover_bps),
        }


def causal_overlay_exposures(
    returns: DailyReturns,
    config: FactorOverlayConfig,
) -> tuple[tuple[str, Decimal, Decimal | None], ...]:
    """Choose each day's exposure from portfolio returns closed before that day."""
    if not returns:
        raise ValueError("factor overlay requires daily returns")
    scores = (
        _daily_scores(returns, config.lookback_periods)
        if config.rebalance_frequency == "daily"
        else _monthly_scores(returns, config.lookback_periods)
    )
    result = []
    for (label, _value), score in zip(returns, scores, strict=True):
        if score is None:
            result.append((label, Decimal("1"), None))
            continue
        strong = score >= config.threshold
        use_high = strong if config.mode == "momentum" else not strong
        exposure = config.high_exposure if use_high else config.low_exposure
        result.append((label, exposure, score))
    return tuple(result)


def evaluate_factor_overlay(
    returns: DailyReturns,
    config: FactorOverlayConfig,
    *,
    signal_returns: DailyReturns | None = None,
    initial_equity: Decimal = Decimal("100000"),
) -> PortfolioResult:
    """Apply a next-day exposure overlay with explicit exposure-turnover costs."""
    if initial_equity <= 0:
        raise ValueError("factor overlay initial equity must be positive")
    signals = signal_returns or returns
    if tuple(label for label, _value in signals) != tuple(label for label, _value in returns):
        raise ValueError("factor overlay signal labels are not aligned")
    exposures = causal_overlay_exposures(signals, config)
    equity = initial_equity
    peak = initial_equity
    max_drawdown = Decimal("0")
    previous_exposure = Decimal("1")
    daily: list[tuple[str, Decimal]] = []
    bankrupt = False
    cost_rate = config.turnover_bps / Decimal("10000")
    for (label, base_return), (exposure_label, exposure, _score) in zip(
        returns, exposures, strict=True
    ):
        if label != exposure_label:
            raise ValueError("factor overlay labels are not aligned")
        turnover_cost = abs(exposure - previous_exposure) * cost_rate
        strategy_return = exposure * base_return - turnover_cost
        equity *= Decimal("1") + strategy_return
        daily.append((label, strategy_return))
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - Decimal("1"))
        previous_exposure = exposure
        if equity <= 0:
            bankrupt = True
            break
    used_daily = tuple(daily)
    return PortfolioResult(
        initial_equity=initial_equity,
        final_equity=equity,
        net_return=equity / initial_equity - Decimal("1"),
        max_drawdown=max_drawdown,
        bankrupt=bankrupt,
        daily_returns=used_daily,
        monthly_returns=monthly_returns(used_daily),
    )


def evaluate_monthly_risk_overlay(
    returns: DailyReturns,
    config: MonthlyRiskConfig,
    *,
    initial_equity: Decimal = Decimal("100000"),
) -> PortfolioResult:
    """Apply fixed leverage with causal month-to-date loss and profit locks."""
    if not returns or initial_equity <= 0:
        raise ValueError("monthly risk overlay requires returns and positive equity")
    equity = initial_equity
    peak = initial_equity
    max_drawdown = Decimal("0")
    month = ""
    month_start_equity = initial_equity
    paused = False
    previous_exposure = Decimal("0")
    cost_rate = config.turnover_bps / Decimal("10000")
    daily: list[tuple[str, Decimal]] = []
    bankrupt = False
    for label, base_return in returns:
        current_month = label[:7]
        if current_month != month:
            month = current_month
            month_start_equity = equity
            paused = False
        exposure = Decimal("0") if paused else config.leverage
        turnover_cost = abs(exposure - previous_exposure) * cost_rate
        strategy_return = exposure * base_return - turnover_cost
        equity *= Decimal("1") + strategy_return
        daily.append((label, strategy_return))
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - Decimal("1"))
        month_return = equity / month_start_equity - Decimal("1")
        if month_return <= -config.loss_limit or (
            config.profit_target is not None and month_return >= config.profit_target
        ):
            paused = True
        previous_exposure = exposure
        if equity <= 0:
            bankrupt = True
            break
    used_daily = tuple(daily)
    return PortfolioResult(
        initial_equity=initial_equity,
        final_equity=equity,
        net_return=equity / initial_equity - Decimal("1"),
        max_drawdown=max_drawdown,
        bankrupt=bankrupt,
        daily_returns=used_daily,
        monthly_returns=monthly_returns(used_daily),
    )


def _compound(values: Any) -> Decimal:
    equity = Decimal("1")
    for value in values:
        equity *= Decimal("1") + value
    return equity - Decimal("1")


def _daily_scores(returns: DailyReturns, lookback: int) -> tuple[Decimal | None, ...]:
    return tuple(
        None
        if index < lookback
        else _compound(value for _label, value in returns[index - lookback : index])
        for index in range(len(returns))
    )


def _monthly_scores(returns: DailyReturns, lookback: int) -> tuple[Decimal | None, ...]:
    completed = monthly_returns(returns)
    month_values = {label: value for label, value in completed}
    months = tuple(month_values)
    month_index = {label: index for index, label in enumerate(months)}
    scores: dict[str, Decimal | None] = {}
    for month in months:
        index = month_index[month]
        scores[month] = (
            None
            if index < lookback
            else _compound(month_values[value] for value in months[index - lookback : index])
        )
    return tuple(scores[label[:7]] for label, _value in returns)
