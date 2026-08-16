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


@dataclass(frozen=True)
class VolatilityTargetConfig:
    lookback_days: int
    target_daily_volatility: Decimal
    minimum_exposure: Decimal
    maximum_exposure: Decimal
    rebalance_frequency: str = "monthly"
    turnover_bps: Decimal = Decimal("7")

    def __post_init__(self) -> None:
        if self.lookback_days < 2:
            raise ValueError("volatility target lookback must be at least two days")
        if self.target_daily_volatility <= 0:
            raise ValueError("volatility target must be positive")
        if not Decimal("0") <= self.minimum_exposure < self.maximum_exposure:
            raise ValueError("volatility target exposures are invalid")
        if self.maximum_exposure > Decimal("10") or self.turnover_bps < 0:
            raise ValueError("volatility target exposure or cost is invalid")
        if self.rebalance_frequency not in {"daily", "monthly"}:
            raise ValueError("volatility target rebalance frequency is unsupported")

    def as_dict(self) -> dict[str, Any]:
        return {
            "lookback_days": self.lookback_days,
            "target_daily_volatility": float(self.target_daily_volatility),
            "minimum_exposure": float(self.minimum_exposure),
            "maximum_exposure": float(self.maximum_exposure),
            "rebalance_frequency": self.rebalance_frequency,
            "turnover_bps": float(self.turnover_bps),
        }


@dataclass(frozen=True)
class SignalOverlayConfig:
    threshold: Decimal
    low_exposure: Decimal
    high_exposure: Decimal
    mode: str
    turnover_bps: Decimal = Decimal("7")

    def __post_init__(self) -> None:
        if self.threshold < 0:
            raise ValueError("signal overlay threshold must be non-negative")
        if not Decimal("0") <= self.low_exposure < self.high_exposure:
            raise ValueError("signal overlay exposures are invalid")
        if self.high_exposure > Decimal("10") or self.turnover_bps < 0:
            raise ValueError("signal overlay exposure or cost is invalid")
        if self.mode not in {"above", "below", "extreme", "calm"}:
            raise ValueError("signal overlay mode is unsupported")

    def as_dict(self) -> dict[str, Any]:
        return {
            "threshold": float(self.threshold),
            "low_exposure": float(self.low_exposure),
            "high_exposure": float(self.high_exposure),
            "mode": self.mode,
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


def causal_volatility_exposures(
    returns: DailyReturns,
    config: VolatilityTargetConfig,
) -> tuple[tuple[str, Decimal, Decimal | None], ...]:
    """Set exposure from trailing returns that closed before each exposure day."""
    if not returns:
        raise ValueError("volatility target requires daily returns")
    exposures: list[tuple[str, Decimal, Decimal | None]] = []
    current_month = ""
    current_exposure = Decimal("1")
    current_volatility: Decimal | None = None
    for index, (label, _value) in enumerate(returns):
        should_rebalance = config.rebalance_frequency == "daily" or label[:7] != current_month
        if should_rebalance:
            current_month = label[:7]
            history = returns[max(0, index - config.lookback_days) : index]
            if len(history) < config.lookback_days:
                current_exposure = Decimal("1")
                current_volatility = None
            else:
                count = Decimal(len(history))
                current_volatility = (
                    sum((value * value for _history_label, value in history), Decimal("0")) / count
                ).sqrt()
                unconstrained = (
                    config.maximum_exposure
                    if current_volatility == 0
                    else config.target_daily_volatility / current_volatility
                )
                current_exposure = min(
                    config.maximum_exposure,
                    max(config.minimum_exposure, unconstrained),
                )
        exposures.append((label, current_exposure, current_volatility))
    return tuple(exposures)


def evaluate_volatility_target(
    returns: DailyReturns,
    config: VolatilityTargetConfig,
    *,
    signal_returns: DailyReturns | None = None,
    start: str | None = None,
    end: str | None = None,
    initial_equity: Decimal = Decimal("100000"),
) -> PortfolioResult:
    """Apply causal volatility-scaled exposure with explicit turnover costs."""
    if initial_equity <= 0:
        raise ValueError("volatility target initial equity must be positive")
    signals = signal_returns or returns
    labels = tuple(label for label, _value in returns)
    if tuple(label for label, _value in signals) != labels:
        raise ValueError("volatility target signal labels are not aligned")
    if start is not None and end is not None and start > end:
        raise ValueError("volatility target period is invalid")
    exposures = causal_volatility_exposures(signals, config)
    selected = [
        ((label, value), exposure)
        for (label, value), exposure in zip(returns, exposures, strict=True)
        if (start is None or label >= start) and (end is None or label <= end)
    ]
    if not selected:
        raise ValueError("volatility target evaluation period is empty")

    equity = initial_equity
    peak = initial_equity
    max_drawdown = Decimal("0")
    previous_exposure = Decimal("1")
    daily: list[tuple[str, Decimal]] = []
    bankrupt = False
    cost_rate = config.turnover_bps / Decimal("10000")
    for (label, base_return), (exposure_label, exposure, _volatility) in selected:
        if label != exposure_label:
            raise ValueError("volatility target labels are not aligned")
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


def evaluate_signal_overlay(
    returns: DailyReturns,
    signals: tuple[tuple[str, Decimal | None], ...],
    config: SignalOverlayConfig,
    *,
    initial_equity: Decimal = Decimal("100000"),
) -> PortfolioResult:
    """Apply exposure selected from already-causal, date-aligned scalar signals."""
    if not returns or initial_equity <= 0:
        raise ValueError("signal overlay requires returns and positive equity")
    if tuple(label for label, _value in signals) != tuple(label for label, _value in returns):
        raise ValueError("signal overlay labels are not aligned")
    equity = initial_equity
    peak = initial_equity
    max_drawdown = Decimal("0")
    previous_exposure = Decimal("1")
    daily: list[tuple[str, Decimal]] = []
    bankrupt = False
    cost_rate = config.turnover_bps / Decimal("10000")
    for (label, base_return), (_signal_label, signal) in zip(returns, signals, strict=True):
        use_high = _signal_high_state(signal, config)
        exposure = (
            Decimal("1")
            if use_high is None
            else config.high_exposure
            if use_high
            else config.low_exposure
        )
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


def evaluate_signal_volatility_overlay(
    returns: DailyReturns,
    signals: tuple[tuple[str, Decimal | None], ...],
    signal_config: SignalOverlayConfig,
    volatility_config: VolatilityTargetConfig,
    *,
    volatility_signal_returns: DailyReturns | None = None,
    start: str | None = None,
    end: str | None = None,
    initial_equity: Decimal = Decimal("100000"),
) -> PortfolioResult:
    """Apply state and volatility exposure with turnover on their combined notional."""
    if not returns or initial_equity <= 0:
        raise ValueError("combined overlay requires returns and positive equity")
    labels = tuple(label for label, _value in returns)
    if tuple(label for label, _value in signals) != labels:
        raise ValueError("combined overlay signal labels are not aligned")
    if start is not None and end is not None and start > end:
        raise ValueError("combined overlay period is invalid")
    if signal_config.turnover_bps != volatility_config.turnover_bps:
        raise ValueError("combined overlay turnover costs must match")

    volatility_signals = volatility_signal_returns
    if volatility_signals is None:
        volatility_signals = evaluate_signal_overlay(
            returns,
            signals,
            signal_config,
            initial_equity=initial_equity,
        ).daily_returns
    if tuple(label for label, _value in volatility_signals) != labels:
        raise ValueError("combined volatility signal labels are not aligned")
    volatility_exposures = causal_volatility_exposures(volatility_signals, volatility_config)
    selected = [
        (row, signal, volatility)
        for row, signal, volatility in zip(
            returns,
            signals,
            volatility_exposures,
            strict=True,
        )
        if (start is None or row[0] >= start) and (end is None or row[0] <= end)
    ]
    if not selected:
        raise ValueError("combined overlay evaluation period is empty")

    equity = initial_equity
    peak = initial_equity
    max_drawdown = Decimal("0")
    previous_exposure = Decimal("1")
    daily: list[tuple[str, Decimal]] = []
    bankrupt = False
    cost_rate = volatility_config.turnover_bps / Decimal("10000")
    for (label, base_return), (signal_label, signal), volatility in selected:
        volatility_label, volatility_exposure, _realized_volatility = volatility
        if label != signal_label or label != volatility_label:
            raise ValueError("combined overlay labels are not aligned")
        use_high = _signal_high_state(signal, signal_config)
        signal_exposure = (
            Decimal("1")
            if use_high is None
            else signal_config.high_exposure
            if use_high
            else signal_config.low_exposure
        )
        exposure = signal_exposure * volatility_exposure
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


def _signal_high_state(signal: Decimal | None, config: SignalOverlayConfig) -> bool | None:
    if signal is None:
        return None
    if config.mode == "above":
        return signal >= config.threshold
    if config.mode == "below":
        return signal <= -config.threshold
    if config.mode == "extreme":
        return abs(signal) >= config.threshold
    return abs(signal) < config.threshold


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
