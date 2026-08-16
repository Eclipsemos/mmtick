"""Independent-sleeve execution model for the frozen BTC/ETH calendar router."""

from __future__ import annotations

import bisect
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from mastermind_tick.models import Bar, FundingRate

INITIAL_EQUITY = Decimal("100000")
QUANTITY_STEP = Decimal("0.001")


@dataclass
class SleeveDay:
    day: str
    timestamp_ms: int
    cash: Decimal
    quantity: Decimal
    equity: Decimal
    target: Decimal
    daily_return: Decimal
    fee_amount: Decimal
    slippage_amount: Decimal
    funding_amount: Decimal
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def fee_return(self) -> Decimal:
        return Decimal("0") if self.daily_return == Decimal("-1") else self._cost_return("fee")

    @property
    def slippage_return(self) -> Decimal:
        return self._cost_return("slippage")

    @property
    def funding_return(self) -> Decimal:
        return self._cost_return("funding")

    def _cost_return(self, kind: str) -> Decimal:
        previous_equity = Decimal(
            next(
                (
                    event["previous_equity"]
                    for event in self.events
                    if event.get("event_type") == "DAY_ACCOUNTING"
                ),
                "0",
            )
        )
        if not previous_equity:
            return Decimal("0")
        if kind == "fee":
            return -self.fee_amount / previous_equity
        if kind == "slippage":
            return -self.slippage_amount / previous_equity
        return self.funding_amount / previous_equity


def replay_independent_sleeve(
    bars: list[Bar],
    targets: tuple[Decimal | int | None, ...],
    funding_rates: list[FundingRate],
    *,
    start_ms: int,
    end_ms: int,
    fee_bps: Decimal,
    slippage_bps: Decimal,
    monthly_loss_limit: Decimal | None = None,
) -> dict[str, SleeveDay]:
    """Replay one derivative sleeve with fixed quantity between target changes."""
    if len(bars) != len(targets):
        raise ValueError("bar and target lengths differ")
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        raise ValueError("independent sleeve replay period is empty")
    grouped_funding = _funding_by_bar(bars, funding_rates)
    fee_rate = fee_bps / Decimal("10000")
    slippage_rate = slippage_bps / Decimal("10000")
    cash = INITIAL_EQUITY
    position = Decimal("0")
    entry_price = Decimal("0")
    current_target = Decimal("0")
    previous_index = selected[0] - 1
    pending_target = _target(targets[previous_index]) if previous_index >= 0 else Decimal("0")
    current_month: str | None = None
    month_start_equity = INITIAL_EQUITY
    paused_for_month = False
    daily: dict[str, dict[str, Any]] = {}

    def bucket(timestamp_ms: int) -> dict[str, Any]:
        day = _day(timestamp_ms)
        return daily.setdefault(
            day,
            {"fee": Decimal("0"), "slippage": Decimal("0"), "funding": Decimal("0"), "events": []},
        )

    def close(market_price: Decimal, timestamp_ms: int) -> None:
        nonlocal cash, position, entry_price, current_target
        if not position:
            current_target = Decimal("0")
            return
        fill = market_price * (
            Decimal("1") - slippage_rate if position > 0 else Decimal("1") + slippage_rate
        )
        quantity = abs(position)
        fee = quantity * fill * fee_rate
        slippage = quantity * abs(fill - market_price)
        gross = position * (fill - entry_price)
        values = bucket(timestamp_ms)
        values["fee"] += fee
        values["slippage"] += slippage
        values["events"].append(
            {
                "timestamp_ms": timestamp_ms,
                "event_type": "COMPONENT_FILL",
                "side": "SELL" if position > 0 else "BUY",
                "position_effect": "CLOSE",
                "quantity": str(quantity),
                "market_price": str(market_price),
                "fill_price": str(fill),
                "fee": str(fee),
                "slippage": str(slippage),
                "target_before": str(current_target),
                "target_after": "0",
            }
        )
        cash += gross - fee
        position = Decimal("0")
        entry_price = Decimal("0")
        current_target = Decimal("0")

    def open_position(target: Decimal, market_price: Decimal, timestamp_ms: int) -> None:
        nonlocal cash, position, entry_price, current_target
        if not target or cash <= 0:
            return
        fill = market_price * (
            Decimal("1") + slippage_rate if target > 0 else Decimal("1") - slippage_rate
        )
        quantity = _floor_step(cash * abs(target) / fill, QUANTITY_STEP)
        fee = quantity * fill * fee_rate
        if quantity <= 0 or fee >= cash:
            return
        slippage = quantity * abs(fill - market_price)
        values = bucket(timestamp_ms)
        values["fee"] += fee
        values["slippage"] += slippage
        values["events"].append(
            {
                "timestamp_ms": timestamp_ms,
                "event_type": "COMPONENT_FILL",
                "side": "BUY" if target > 0 else "SELL",
                "position_effect": "OPEN",
                "quantity": str(quantity),
                "market_price": str(market_price),
                "fill_price": str(fill),
                "fee": str(fee),
                "slippage": str(slippage),
                "target_before": "0",
                "target_after": str(target),
            }
        )
        cash -= fee
        position = quantity if target > 0 else -quantity
        entry_price = fill
        current_target = target

    daily_equity: dict[str, Decimal] = {}
    for index in selected:
        bar = bars[index]
        month = _day(bar.start_ms)[:7]
        if monthly_loss_limit is not None and month != current_month:
            current_month = month
            paused_for_month = False
            month_start_equity = cash + position * (bar.open - entry_price)
            pending_target = _target(targets[index - 1]) if index else Decimal("0")
        if paused_for_month:
            pending_target = Decimal("0")
        if pending_target != current_target:
            close(bar.open, bar.start_ms)
            open_position(pending_target, bar.open, bar.start_ms)
        for funding in grouped_funding[index]:
            if not position:
                continue
            amount = -(position * funding.mark_price * funding.rate)
            cash += amount
            values = bucket(funding.timestamp_ms)
            values["funding"] += amount
            values["events"].append(
                {
                    "timestamp_ms": funding.timestamp_ms,
                    "event_type": "FUNDING",
                    "quantity": str(position),
                    "mark_price": str(funding.mark_price),
                    "rate": str(funding.rate),
                    "amount": str(amount),
                    "target": str(current_target),
                }
            )
        equity = cash + position * (bar.close - entry_price)
        day = _day(bar.end_ms)
        daily_equity[day] = equity
        values = bucket(bar.end_ms)
        values["cash"] = cash
        values["position"] = position
        values["target"] = current_target
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
                pending_target = _target(signal)

    previous_equity = INITIAL_EQUITY
    result: dict[str, SleeveDay] = {}
    for day, equity in daily_equity.items():
        values = daily.get(
            day,
            {
                "fee": Decimal("0"),
                "slippage": Decimal("0"),
                "funding": Decimal("0"),
                "events": [],
                "cash": INITIAL_EQUITY,
                "position": Decimal("0"),
                "target": Decimal("0"),
            },
        )
        daily_return = Decimal(str(float(equity / previous_equity - Decimal("1"))))
        events = list(values["events"])
        events.append(
            {
                "timestamp_ms": _day_end_ms(day),
                "event_type": "DAY_ACCOUNTING",
                "previous_equity": str(previous_equity),
                "equity": str(equity),
                "cash": str(values["cash"]),
            }
        )
        result[day] = SleeveDay(
            day,
            _day_end_ms(day),
            values["cash"],
            values["position"],
            equity,
            values["target"],
            daily_return,
            values["fee"],
            values["slippage"],
            values["funding"],
            events,
        )
        previous_equity = equity
    return result


def combine_static_anchor(
    sleeves: dict[str, dict[str, SleeveDay]],
    allocations: dict[str, Decimal],
    *,
    leverage: Decimal,
) -> dict[str, dict[str, Any]]:
    labels = _aligned_labels(sleeves)
    allocated = {name: INITIAL_EQUITY * leverage * allocations[name] for name in sleeves}
    reserve = INITIAL_EQUITY * (Decimal("1") - leverage)
    previous_total = INITIAL_EQUITY
    result: dict[str, dict[str, Any]] = {}
    for day in labels:
        before = dict(allocated)
        for name in sleeves:
            allocated[name] *= Decimal("1") + sleeves[name][day].daily_return
        total = reserve + sum(allocated.values(), Decimal("0"))
        daily_return = total / previous_total - Decimal("1")
        fee_return = (
            sum((before[name] * sleeves[name][day].fee_return for name in sleeves), Decimal("0"))
            / previous_total
        )
        slippage_return = (
            sum(
                (before[name] * sleeves[name][day].slippage_return for name in sleeves),
                Decimal("0"),
            )
            / previous_total
        )
        funding_return = (
            sum(
                (before[name] * sleeves[name][day].funding_return for name in sleeves), Decimal("0")
            )
            / previous_total
        )
        result[day] = {
            "return": daily_return,
            "equity": total,
            "reserve": reserve,
            "allocated_equity": dict(allocated),
            "fee_return": fee_return,
            "slippage_return": slippage_return,
            "funding_return": funding_return,
        }
        previous_total = total
    return result


def apply_state_volatility_overlay(
    anchor: dict[str, dict[str, Any]],
    metrics: dict[str, tuple[Decimal | None, str | None, int]],
    *,
    route_cost_bps: Decimal,
) -> dict[str, dict[str, Any]]:
    route_rate = route_cost_bps / Decimal("10000")
    state_signal_returns: deque[Decimal] = deque(maxlen=20)
    previous_signal_exposure = Decimal("1")
    previous_combined_exposure = Decimal("1")
    result: dict[str, dict[str, Any]] = {}
    for day, anchor_day in anchor.items():
        score, source, metric_start = metrics.get(day, (None, None, 0))
        signal_exposure = (
            Decimal("1")
            if score is None
            else Decimal("2")
            if score <= Decimal("-1.25")
            else Decimal("0.8")
        )
        signal_cost = abs(signal_exposure - previous_signal_exposure) * route_rate
        signal_return = signal_exposure * anchor_day["return"] - signal_cost
        volatility_exposure, rms = _volatility_exposure(state_signal_returns)
        combined_exposure = signal_exposure * volatility_exposure
        route_turnover = abs(combined_exposure - previous_combined_exposure)
        route_cost = route_turnover * route_rate
        value = combined_exposure * anchor_day["return"] - route_cost
        result[day] = {
            "return": value,
            "signal_return": signal_return,
            "signal_exposure": signal_exposure,
            "volatility_exposure": volatility_exposure,
            "combined_exposure": combined_exposure,
            "rms": rms,
            "route_turnover": route_turnover,
            "route_cost": route_cost,
            "metric_score": score,
            "metric_source": source,
            "metric_start_ms": metric_start,
            "fee_return": combined_exposure * anchor_day["fee_return"],
            "slippage_return": combined_exposure * anchor_day["slippage_return"],
            "funding_return": combined_exposure * anchor_day["funding_return"],
        }
        state_signal_returns.append(signal_return)
        previous_signal_exposure = signal_exposure
        previous_combined_exposure = combined_exposure
    return result


def combine_calendar_route(
    state: dict[str, dict[str, Any]],
    candidates: dict[str, dict[str, SleeveDay]],
    mapping: dict[int, tuple[str, ...]],
    *,
    route_cost_bps: Decimal,
) -> dict[str, dict[str, Any]]:
    route_rate = route_cost_bps / Decimal("10000")
    previous_weights: dict[str, Decimal] = {}
    current_month = ""
    result: dict[str, dict[str, Any]] = {}
    for day, state_day in state.items():
        selected = mapping[int(day[5:7])]
        if any(day not in candidates[name] for name in selected):
            continue
        weights = {"state": Decimal("0.5")}
        weights.update({name: Decimal("1") / Decimal("6") for name in selected})
        turnover = Decimal("0")
        weights_before = dict(previous_weights)
        if day[:7] != current_month:
            turnover = (
                _route_turnover(previous_weights, weights) if previous_weights else Decimal("1")
            )
            previous_weights = weights
            current_month = day[:7]
        route_cost = turnover * route_rate
        candidate_return = sum(
            (candidates[name][day].daily_return for name in selected), Decimal("0")
        ) / Decimal("6")
        value = Decimal("0.5") * state_day["return"] + candidate_return - route_cost
        fee_return = Decimal("0.5") * state_day["fee_return"] + sum(
            (candidates[name][day].fee_return for name in selected), Decimal("0")
        ) / Decimal("6")
        slippage_return = Decimal("0.5") * state_day["slippage_return"] + sum(
            (candidates[name][day].slippage_return for name in selected), Decimal("0")
        ) / Decimal("6")
        funding_return = Decimal("0.5") * state_day["funding_return"] + sum(
            (candidates[name][day].funding_return for name in selected), Decimal("0")
        ) / Decimal("6")
        result[day] = {
            "return": value,
            "selected": selected,
            "route_weights": weights,
            "route_weights_before": weights_before,
            "route_turnover": turnover,
            "route_cost": route_cost,
            "fee_return": fee_return,
            "slippage_return": slippage_return,
            "funding_return": funding_return,
        }
    return result


def attach_events(
    day: SleeveDay, sleeve_id: str, instrument_id: str, *, capitalized: bool
) -> list[dict[str, Any]]:
    return [
        {
            **event,
            "sleeve_id": sleeve_id,
            "instrument_id": instrument_id,
            "capitalized": capitalized,
        }
        for event in day.events
        if event["event_type"] != "DAY_ACCOUNTING"
    ]


def _funding_by_bar(bars: list[Bar], values: list[FundingRate]) -> list[list[FundingRate]]:
    ends = [bar.end_ms for bar in bars]
    result: list[list[FundingRate]] = [[] for _bar in bars]
    for value in values:
        index = bisect.bisect_left(ends, value.timestamp_ms)
        if index < len(bars) and bars[index].start_ms <= value.timestamp_ms:
            result[index].append(value)
    return result


def _volatility_exposure(history: deque[Decimal]) -> tuple[Decimal, Decimal | None]:
    if len(history) < 20:
        return Decimal("1"), None
    rms = (sum((value * value for value in history), Decimal("0")) / Decimal(20)).sqrt()
    exposure = (
        Decimal("1.1")
        if rms == 0
        else min(Decimal("1.1"), max(Decimal("0.6"), Decimal("0.03") / rms))
    )
    return exposure, rms


def _route_turnover(previous: dict[str, Decimal], current: dict[str, Decimal]) -> Decimal:
    names = set(previous) | set(current)
    return sum(
        (abs(current.get(name, Decimal("0")) - previous.get(name, Decimal("0"))) for name in names),
        Decimal("0"),
    ) / Decimal("2")


def _aligned_labels(sleeves: dict[str, dict[str, SleeveDay]]) -> tuple[str, ...]:
    labels = tuple(next(iter(sleeves.values())).keys())
    if any(tuple(values.keys()) != labels for values in sleeves.values()):
        raise ValueError("independent sleeve daily labels are not aligned")
    return labels


def _target(value: Decimal | int | None) -> Decimal:
    return Decimal(value or 0)


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _day(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).date().isoformat()


def _day_end_ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000) + 86_400_000 - 1
