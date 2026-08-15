"""Causal BTC shock factors for delayed ETH response research."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from mastermind_tick.bar_research import ResearchBar, ResearchResult, ResearchTrade
from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class LeadLagCandidate:
    normalization_days: int
    threshold: Decimal
    hold_bars: int
    direction: str
    response_gate: str

    @property
    def id(self) -> str:
        threshold = f"{self.threshold:g}".replace(".", "p")
        return (
            f"btc-shock-eth-{self.direction}-window-{self.normalization_days}d"
            f"-threshold-{threshold}-hold-{self.hold_bars}x4h-gate-{self.response_gate}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, **asdict(self), "threshold": float(self.threshold)}


@dataclass(frozen=True)
class ShockSizing:
    moderate_exposure: Decimal
    strong_exposure: Decimal
    extreme_exposure: Decimal

    @property
    def id(self) -> str:
        values = (
            f"{value:g}".replace(".", "p")
            for value in (
                self.moderate_exposure,
                self.strong_exposure,
                self.extreme_exposure,
            )
        )
        moderate, strong, extreme = values
        return f"moderate-{moderate}x-strong-{strong}x-extreme-{extreme}x"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "moderate_exposure": float(self.moderate_exposure),
            "strong_exposure": float(self.strong_exposure),
            "extreme_exposure": float(self.extreme_exposure),
        }


def candidate_library() -> tuple[LeadLagCandidate, ...]:
    return tuple(
        LeadLagCandidate(window, threshold, hold, direction, gate)
        for window in (15, 30, 60, 90)
        for threshold in (Decimal("1.5"), Decimal("2"), Decimal("2.5"), Decimal("3"))
        for hold in (2, 4, 6, 8, 12)
        for direction in ("long_only", "short_only", "long_short")
        for gate in ("none", "underreaction", "opposing", "lag_gap")
    )


def sizing_library() -> tuple[ShockSizing, ...]:
    tiered = tuple(
        ShockSizing(moderate, strong, extreme)
        for moderate in (Decimal("0.5"), Decimal("0.75"), Decimal("1"), Decimal("1.25"))
        for strong in (Decimal("0.75"), Decimal("1"), Decimal("1.5"), Decimal("2"))
        for extreme in (Decimal("0.5"), Decimal("1"), Decimal("1.5"), Decimal("2"), Decimal("3"))
    )
    constant = tuple(
        ShockSizing(exposure, exposure, exposure)
        for exposure in (Decimal("1.5"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5"))
    )
    return tiered + tuple(item for item in constant if item not in tiered)


def causal_shock_scores(
    btc_bars: list[ResearchBar],
    eth_bars: list[ResearchBar],
    normalization_bars: int,
) -> tuple[tuple[Decimal | None, ...], tuple[Decimal | None, ...]]:
    """Normalize current close-to-close returns against prior observations only."""
    if len(btc_bars) != len(eth_bars):
        raise ValueError("BTC and ETH shock bars have different lengths")
    if any(
        btc.start_ms != eth.start_ms or btc.end_ms != eth.end_ms
        for btc, eth in zip(btc_bars, eth_bars, strict=True)
    ):
        raise ValueError("BTC and ETH shock bars are not aligned")
    if normalization_bars < 12:
        raise ValueError("shock normalization requires at least twelve bars")
    return (
        _causal_zscore(_returns(btc_bars), normalization_bars),
        _causal_zscore(_returns(eth_bars), normalization_bars),
    )


def shock_targets(
    btc_scores: tuple[Decimal | None, ...],
    eth_scores: tuple[Decimal | None, ...],
    candidate: LeadLagCandidate,
) -> tuple[int | None, ...]:
    if len(btc_scores) != len(eth_scores):
        raise ValueError("BTC and ETH shock score lengths differ")
    if candidate.direction not in {"long_only", "short_only", "long_short"}:
        raise ValueError("unsupported shock direction")
    if candidate.response_gate not in {"none", "underreaction", "opposing", "lag_gap"}:
        raise ValueError("unsupported ETH response gate")
    if candidate.threshold <= 0 or candidate.hold_bars < 1:
        raise ValueError("invalid shock threshold or holding period")

    state = 0
    exit_index = -1
    targets: list[int | None] = []
    for index, (btc, eth) in enumerate(zip(btc_scores, eth_scores, strict=True)):
        if state and index >= exit_index:
            state = 0
            targets.append(0)
            continue
        if state:
            targets.append(state)
            continue
        if btc is None or eth is None:
            targets.append(None)
            continue
        side = 1 if btc >= candidate.threshold else -1 if btc <= -candidate.threshold else 0
        if side > 0 and candidate.direction == "short_only":
            side = 0
        elif side < 0 and candidate.direction == "long_only":
            side = 0
        if side and _response_passes(btc, eth, candidate):
            state = side
            exit_index = index + candidate.hold_bars
        targets.append(state)
    return tuple(targets)


def shock_weight_targets(
    targets: tuple[int | None, ...],
    btc_scores: tuple[Decimal | None, ...],
    sizing: ShockSizing,
) -> tuple[Decimal | None, ...]:
    if len(targets) != len(btc_scores):
        raise ValueError("shock target and score lengths differ")
    active = Decimal("0")
    result: list[Decimal | None] = []
    for target, score in zip(targets, btc_scores, strict=True):
        if target is None:
            result.append(None)
            continue
        if target == 0:
            active = Decimal("0")
        elif not active or (active > 0) != (target > 0):
            if score is None:
                result.append(None)
                continue
            magnitude = abs(score)
            exposure = (
                sizing.extreme_exposure
                if magnitude >= Decimal("3")
                else sizing.strong_exposure
                if magnitude >= Decimal("2.5")
                else sizing.moderate_exposure
            )
            active = exposure if target > 0 else -exposure
        result.append(active)
    return tuple(result)


def evaluate_weighted_targets(
    bars: list[ResearchBar],
    targets: tuple[Decimal | None, ...],
    *,
    start_ms: int,
    end_ms: int,
    funding: list[list[FundingRate]] | None = None,
    initial_equity: Decimal = Decimal("100000"),
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
    quantity_step: Decimal = Decimal("0.001"),
    monthly_loss_limit: Decimal | None = None,
) -> ResearchResult:
    """Replay signed target exposure with next-open fills and close-bar risk marking."""
    if len(targets) != len(bars):
        raise ValueError("weighted shock target and bar lengths differ")
    if any(value is not None and abs(value) > Decimal("10") for value in targets):
        raise ValueError("weighted shock exposure exceeds research limit")
    if monthly_loss_limit is not None and not Decimal("0") < monthly_loss_limit < Decimal("1"):
        raise ValueError("monthly loss limit must be between zero and one")
    if funding is None:
        funding = [[] for _bar in bars]
    if len(funding) != len(bars):
        raise ValueError("weighted shock funding and bar lengths differ")
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        raise ValueError("no weighted shock bars in requested range")

    fee_rate = fee_bps / Decimal("10000")
    slippage_rate = slippage_bps / Decimal("10000")
    cash = initial_equity
    position = Decimal("0")
    entry_price = Decimal("0")
    entry_at_ms = 0
    entry_fee = Decimal("0")
    trade_funding = Decimal("0")
    current_target = Decimal("0")
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    peak_equity = initial_equity
    max_drawdown = Decimal("0")
    trades: list[ResearchTrade] = []
    daily_equity: dict[str, Decimal] = {}
    bankrupt = False
    paused_for_month = False
    current_month: str | None = None
    month_start_equity = initial_equity
    previous_index = selected[0] - 1
    pending_target = targets[previous_index] if previous_index >= 0 else None
    pending_target = pending_target or Decimal("0")

    def close(market_price: Decimal, timestamp_ms: int) -> None:
        nonlocal cash, position, entry_price, entry_fee, trade_funding, total_fees
        nonlocal current_target
        if not position:
            current_target = Decimal("0")
            return
        fill = market_price * (
            Decimal("1") - slippage_rate if position > 0 else Decimal("1") + slippage_rate
        )
        exit_fee = abs(position) * fill * fee_rate
        gross = position * (fill - entry_price)
        cash += gross - exit_fee
        total_fees += exit_fee
        trades.append(
            ResearchTrade(
                direction="LONG" if position > 0 else "SHORT",
                entry_at_ms=entry_at_ms,
                exit_at_ms=timestamp_ms,
                entry_price=entry_price,
                exit_price=fill,
                fees=entry_fee + exit_fee,
                funding=trade_funding,
                net_pnl=gross - entry_fee - exit_fee + trade_funding,
            )
        )
        position = Decimal("0")
        entry_price = Decimal("0")
        entry_fee = Decimal("0")
        trade_funding = Decimal("0")
        current_target = Decimal("0")

    def open_position(target: Decimal, market_price: Decimal, timestamp_ms: int) -> None:
        nonlocal cash, position, entry_price, entry_at_ms, entry_fee, total_fees
        nonlocal current_target
        if not target or cash <= 0:
            return
        fill = market_price * (
            Decimal("1") + slippage_rate if target > 0 else Decimal("1") - slippage_rate
        )
        quantity = _floor_step(cash * abs(target) / fill, quantity_step)
        fee = quantity * fill * fee_rate
        if quantity <= 0 or fee >= cash:
            return
        cash -= fee
        total_fees += fee
        position = quantity if target > 0 else -quantity
        entry_price = fill
        entry_at_ms = timestamp_ms
        entry_fee = fee
        current_target = target

    last_index = selected[-1]
    for index in selected:
        bar = bars[index]
        month = _utc_date(bar.start_ms)[:7]
        if month != current_month:
            current_month = month
            paused_for_month = False
            month_start_equity = cash + position * (bar.open - entry_price)
            previous_signal = targets[index - 1] if index > 0 else None
            pending_target = previous_signal or Decimal("0")
        if paused_for_month:
            pending_target = Decimal("0")
        if pending_target != current_target:
            close(bar.open, bar.start_ms)
            open_position(pending_target, bar.open, bar.start_ms)
        for event in funding[index]:
            if position:
                amount = -(position * event.mark_price * event.rate)
                cash += amount
                total_funding += amount
                trade_funding += amount
        equity = cash + position * (bar.close - entry_price)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, equity / peak_equity - Decimal("1"))
        daily_equity[_utc_date(bar.end_ms)] = equity
        if equity <= 0:
            bankrupt = True
            last_index = index
            break
        if (
            monthly_loss_limit is not None
            and month_start_equity > 0
            and equity / month_start_equity - Decimal("1") <= -monthly_loss_limit
        ):
            paused_for_month = True
            pending_target = Decimal("0")
        elif not paused_for_month:
            signal = targets[index]
            if signal is not None:
                pending_target = signal

    final_bar = bars[last_index]
    if position and not bankrupt:
        close(final_bar.close, final_bar.end_ms)
        daily_equity[_utc_date(final_bar.end_ms)] = cash
        peak_equity = max(peak_equity, cash)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, cash / peak_equity - Decimal("1"))
    final_equity = cash if not bankrupt else daily_equity[_utc_date(final_bar.end_ms)]
    wins = sum(trade.net_pnl > 0 for trade in trades)
    gross_profit = sum((trade.net_pnl for trade in trades if trade.net_pnl > 0), Decimal("0"))
    gross_loss = -sum((trade.net_pnl for trade in trades if trade.net_pnl < 0), Decimal("0"))
    maximum_exposure = max(
        (abs(value) for value in targets if value is not None),
        default=Decimal("0"),
    )
    return ResearchResult(
        exposure=float(maximum_exposure),
        initial_equity=float(initial_equity),
        final_equity=float(final_equity),
        net_return=float(final_equity / initial_equity - Decimal("1")),
        max_drawdown=float(max_drawdown),
        completed_trades=len(trades),
        win_rate=wins / len(trades) if trades else None,
        profit_factor=float(gross_profit / gross_loss) if gross_loss else None,
        total_fees=float(total_fees),
        total_funding=float(total_funding),
        bankrupt=bankrupt,
        ending_position="LONG" if position > 0 else "SHORT" if position < 0 else "FLAT",
        daily_returns=_period_returns(daily_equity, initial_equity, 10),
        monthly_returns=_period_returns(daily_equity, initial_equity, 7),
        trades=tuple(trades),
    )


def _response_passes(btc: Decimal, eth: Decimal, candidate: LeadLagCandidate) -> bool:
    if candidate.response_gate == "none":
        return True
    if candidate.response_gate == "underreaction":
        return abs(eth) <= abs(btc) * Decimal("0.75")
    if candidate.response_gate == "opposing":
        return btc * eth <= 0
    if candidate.response_gate == "lag_gap":
        return abs(btc - eth) >= candidate.threshold
    raise ValueError(f"unsupported ETH response gate: {candidate.response_gate}")


def _returns(bars: list[ResearchBar]) -> tuple[Decimal | None, ...]:
    return tuple(
        None if index == 0 else bar.close / bars[index - 1].close - Decimal("1")
        for index, bar in enumerate(bars)
    )


def _causal_zscore(values: tuple[Decimal | None, ...], window: int) -> tuple[Decimal | None, ...]:
    history: deque[Decimal | None] = deque()
    total = Decimal("0")
    total_square = Decimal("0")
    valid = 0
    result: list[Decimal | None] = []
    for value in values:
        if value is None or len(history) < window or valid < window:
            result.append(None)
        else:
            mean = total / Decimal(valid)
            variance = total_square / Decimal(valid) - mean * mean
            result.append((value - mean) / variance.sqrt() if variance > 0 else Decimal("0"))
        history.append(value)
        if value is not None:
            total += value
            total_square += value * value
            valid += 1
        if len(history) > window:
            removed = history.popleft()
            if removed is not None:
                total -= removed
                total_square -= removed * removed
                valid -= 1
    return tuple(result)


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _utc_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).date().isoformat()


def _period_returns(
    daily_equity: dict[str, Decimal], initial_equity: Decimal, label_length: int
) -> tuple[tuple[str, float], ...]:
    period_ends: dict[str, Decimal] = {}
    for day, equity in daily_equity.items():
        period_ends[day[:label_length]] = equity
    previous = initial_equity
    result = []
    for label, equity in period_ends.items():
        result.append((label, float(equity / previous - Decimal("1")) if previous else -1.0))
        previous = equity
    return tuple(result)
