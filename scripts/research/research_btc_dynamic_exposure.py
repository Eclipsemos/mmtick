#!/usr/bin/env python3
"""Research a 1x BTC core with causal SMA-based exposure up to 3x."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import ResearchBar, funding_by_bar
from mastermind_tick.models import FundingRate
from mastermind_tick.sma_trend import (
    aggregate_complete_periods,
    four_sma_targets,
    map_targets_to_source,
)


@dataclass(frozen=True)
class DynamicResult:
    net_return: float
    max_drawdown: float
    completed_trades: int
    total_fees: float
    total_funding: float
    bankrupt: bool
    equity_curve: tuple[tuple[int, float], ...] = ()
    exposure_curve: tuple[tuple[int, float], ...] = ()
    risk_curve: tuple[tuple[int, float, float, float, float], ...] = ()


def main() -> None:
    output_dir = Path("reports/experiments/btc_dynamic_exposure/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding_events = load_funding("BTCUSDT", bars)
    funding = funding_by_bar(bars, funding_events)
    splits = split_periods(bars)
    signals = build_signals(bars)
    variants = {
        "buy_and_hold_1x": tuple(1 for _ in bars),
        "trend_1x_or_flat": signals["1h"],
        "core_plus_1h": dynamic_targets(signals["1h"], signals["4h"], 2),
        "core_plus_1h_and_4h": dynamic_targets(signals["1h"], signals["4h"], 3),
        "core_plus_4h": dynamic_targets(signals["4h"], signals["1h"], 2),
    }
    rows = []
    for name, targets in variants.items():
        metrics = {}
        for split, (start, end) in splits.items():
            # Compare against spot-style B&H without funding, then show the
            # separate perpetual-funding result so the two benchmarks are not mixed.
            base = replay_dynamic(bars, targets, None, start, end)
            stress = replay_dynamic(
                bars, targets, None, start, end, fee_bps=Decimal("10"), slippage_bps=Decimal("5")
            )
            perp = replay_dynamic(bars, targets, funding, start, end)
            metrics[split] = {
                "base": as_dict(base),
                "stress": as_dict(stress),
                "perp_with_funding": as_dict(perp),
            }
        rows.append({"id": name, "periods": metrics})
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "symbol": "BTCUSDT",
            "source": "Binance USD-M completed local 15m bars",
            "core": "always 1x long unless variant explicitly says trend-only",
            "dynamic_rule": "1h alignment adds 1x; simultaneous 4h alignment adds another 1x",
            "maximum_exposure": "3x",
            "timing": "completed aggregate candle; next 15m open rebalance",
            "base_cost": "5 bps fee + 2 bps slippage per fill, historical funding",
            "stress_cost": "10 bps fee + 5 bps slippage per fill, historical funding",
        },
        "data": {
            "bars": len(bars),
            "funding_events": len(funding_events),
            "last": iso(bars[-1].end_ms),
        },
        "buy_and_hold_price_baseline": {
            split: benchmark(bars, start, end) for split, (start, end) in splits.items()
        },
        "results": rows,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def build_signals(bars):
    signals = {}
    for timeframe, periods in (("1h", (25, 50, 100, 200)), ("4h", (20, 40, 80, 160))):
        aggregate, ends = aggregate_complete_periods(bars, timeframe)
        period_targets = four_sma_targets(aggregate, periods)
        signals[timeframe] = map_targets_to_source(len(bars), period_targets, ends)
    return signals


def dynamic_targets(primary, secondary, primary_add: int):
    """Build sparse causal target changes while retaining a 1x core position."""
    primary_state = 0
    secondary_state = 0
    targets: list[int | None] = [None] * len(primary)
    for index, (first, second) in enumerate(zip(primary, secondary, strict=True)):
        if first is not None:
            primary_state = first
        if second is not None:
            secondary_state = second
        if first is None and second is None:
            continue
        if primary_state == 1 and secondary_state == 1:
            targets[index] = primary_add
        elif primary_state == 1:
            targets[index] = 2 if primary_add == 2 else 2
        else:
            targets[index] = 1
    return tuple(targets)


def regime_exposure_targets(
    signal, active_exposure: Decimal, inactive_exposure: Decimal = Decimal("1")
):
    """Map a sparse binary regime signal to defensive/leveraged targets."""
    if not Decimal("1") <= active_exposure <= Decimal("3"):
        raise ValueError("active exposure must be between 1x and 3x")
    if not Decimal("0") <= inactive_exposure <= Decimal("1"):
        raise ValueError("inactive exposure must be between 0x and 1x")
    return tuple(
        None if target is None else active_exposure if target == 1 else inactive_exposure
        for target in signal
    )


def replay_dynamic(
    bars: list[ResearchBar],
    targets: tuple[int | Decimal | None, ...],
    funding: list[list[FundingRate]] | None,
    start_ms: int,
    end_ms: int,
    *,
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
    funding_on_excess_only: bool = False,
) -> DynamicResult:
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.start_ms <= end_ms]
    funding = funding if funding is not None else [[] for _ in bars]
    fee_rate = fee_bps / Decimal("10000")
    slip_rate = slippage_bps / Decimal("10000")
    cash = Decimal("100000")
    position = Decimal("0")
    entry_price = Decimal("0")
    previous_index = selected[0] - 1
    pending = Decimal("1")
    while previous_index >= 0:
        previous_target = targets[previous_index]
        if previous_target is not None:
            pending = Decimal(previous_target)
            break
        previous_index -= 1
    active_exposure = Decimal("0")
    entry_fee = Decimal("0")
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    trades = 0
    peak = cash
    max_dd = Decimal("0")
    bankrupt = False

    def close(price: Decimal) -> None:
        nonlocal cash, position, entry_price, entry_fee, total_fees, trades, active_exposure
        if position == 0:
            return
        fill = price * (Decimal("1") - slip_rate)
        exit_fee = abs(position) * fill * fee_rate
        cash += position * (fill - entry_price) - exit_fee
        total_fees += exit_fee
        position = Decimal("0")
        entry_price = Decimal("0")
        entry_fee = Decimal("0")
        active_exposure = 0
        trades += 1

    def open_position(exposure: int | Decimal, price: Decimal) -> None:
        nonlocal cash, position, entry_price, entry_fee, total_fees, active_exposure
        if exposure <= 0 or cash <= 0:
            return
        fill = price * (Decimal("1") + slip_rate)
        exposure_value = Decimal(exposure)
        quantity = cash * exposure_value / fill
        fee = quantity * fill * fee_rate
        cash -= fee
        total_fees += fee
        position = quantity
        entry_price = fill
        entry_fee = fee
        active_exposure = exposure_value

    for index in selected:
        bar = bars[index]
        if active_exposure != pending:
            close(bar.open)
            open_position(pending, bar.open)
        for event in funding[index]:
            if position:
                funded_position = position
                if funding_on_excess_only:
                    if active_exposure <= 1:
                        funded_position = Decimal("0")
                    else:
                        funded_position *= (active_exposure - 1) / active_exposure
                amount = -(funded_position * event.mark_price * event.rate)
                cash += amount
                total_funding += amount
        intrabar_low_equity = cash + position * (bar.low - entry_price)
        if intrabar_low_equity <= 0:
            max_dd = Decimal("-1")
            bankrupt = True
            break
        equity = cash + position * (bar.close - entry_price)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(
                max_dd,
                equity / peak - Decimal("1"),
                intrabar_low_equity / peak - Decimal("1"),
            )
        if equity <= 0:
            bankrupt = True
            break
        if targets[index] is not None:
            pending = targets[index]
    if selected and not bankrupt:
        close(bars[selected[-1]].close)
    final_equity = Decimal("0") if bankrupt else cash
    return DynamicResult(
        net_return=float(final_equity / Decimal("100000") - 1),
        max_drawdown=float(max_dd),
        completed_trades=trades,
        total_fees=float(total_fees),
        total_funding=float(total_funding),
        bankrupt=bankrupt,
    )


def replay_dynamic_incremental(
    bars: list[ResearchBar],
    targets: tuple[int | Decimal | None, ...],
    funding: list[list[FundingRate]] | None,
    start_ms: int,
    end_ms: int,
    *,
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
    funding_on_excess_only: bool = False,
    spot_exposure_cap: Decimal = Decimal("1"),
    record_equity: bool = False,
    record_exposure: bool = False,
    record_risk: bool = False,
) -> DynamicResult:
    """Replay sparse target changes with fixed quantities between rebalances.

    Exposure changes trade spot up to 1x and a linear futures overlay above 1x.
    Quantities remain fixed until the next target change, so the implementation does
    not silently rebalance leverage on every 15-minute bar.
    """
    if len(targets) != len(bars):
        raise ValueError("target and bar lengths differ")
    if not Decimal("0") <= spot_exposure_cap <= Decimal("1"):
        raise ValueError("spot exposure cap must be between zero and one")
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        raise ValueError("no bars in requested range")
    funding = funding if funding is not None else [[] for _ in bars]
    fee_rate = fee_bps / Decimal("10000")
    slippage_rate = slippage_bps / Decimal("10000")
    trade_cost_rate = fee_rate + slippage_rate
    initial_equity = Decimal("100000")
    cash_intercept = initial_equity
    spot_quantity = Decimal("0")
    futures_quantity = Decimal("0")
    peak = initial_equity
    max_drawdown = Decimal("0")
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    active_exposure = Decimal("0")
    pending = Decimal("1")
    previous_index = selected[0] - 1
    while previous_index >= 0:
        previous_target = targets[previous_index]
        if previous_target is not None:
            pending = Decimal(previous_target)
            break
        previous_index -= 1
    completed_trades = 0
    bankrupt = False
    equity_curve: list[tuple[int, float]] = []
    exposure_curve: list[tuple[int, float]] = []
    risk_curve: list[tuple[int, float, float, float, float]] = []

    for index in selected:
        bar = bars[index]
        total_quantity = spot_quantity + futures_quantity
        equity_at_open = cash_intercept + total_quantity * bar.open
        if equity_at_open <= 0:
            bankrupt = True
            max_drawdown = Decimal("-1")
            if record_equity:
                equity_curve.append((bar.end_ms, 0.0))
            if record_exposure:
                exposure_curve.append((bar.end_ms, float(active_exposure)))
            if record_risk:
                risk_curve.append((bar.end_ms, 0.0, 0.0, 0.0, float(active_exposure)))
            break
        if pending != active_exposure:
            (
                desired_spot,
                desired_futures,
                trade_cost,
                fee,
            ) = _target_quantities_after_cost(
                equity_at_open,
                bar.open,
                pending,
                spot_quantity,
                futures_quantity,
                trade_cost_rate,
                fee_rate,
                spot_exposure_cap,
            )
            equity_at_open -= trade_cost
            total_fees += fee
            spot_quantity = desired_spot
            futures_quantity = desired_futures
            total_quantity = spot_quantity + futures_quantity
            cash_intercept = equity_at_open - total_quantity * bar.open
            active_exposure = pending
            completed_trades += 1

        intrabar_low_equity = cash_intercept + total_quantity * bar.low
        if intrabar_low_equity <= 0:
            bankrupt = True
            max_drawdown = Decimal("-1")
            if record_equity:
                equity_curve.append((bar.end_ms, 0.0))
            if record_exposure:
                exposure_curve.append((bar.end_ms, float(active_exposure)))
            if record_risk:
                risk_curve.append((bar.end_ms, 0.0, 0.0, 0.0, float(active_exposure)))
            break
        for event in funding[index]:
            funded_quantity = futures_quantity if funding_on_excess_only else total_quantity
            amount = -(funded_quantity * event.mark_price * event.rate)
            cash_intercept += amount
            total_funding += amount
        intrabar_low_equity = min(
            intrabar_low_equity,
            cash_intercept + total_quantity * bar.low,
        )
        equity = cash_intercept + total_quantity * bar.close
        if equity <= 0 or intrabar_low_equity <= 0:
            bankrupt = True
            max_drawdown = Decimal("-1")
            if record_equity:
                equity_curve.append((bar.end_ms, 0.0))
            if record_exposure:
                exposure_curve.append((bar.end_ms, float(active_exposure)))
            if record_risk:
                risk_curve.append((bar.end_ms, 0.0, 0.0, 0.0, float(active_exposure)))
            break
        peak = max(peak, equity)
        max_drawdown = min(
            max_drawdown,
            equity / peak - Decimal("1"),
            intrabar_low_equity / peak - Decimal("1"),
        )
        signal = targets[index]
        if signal is not None:
            pending = Decimal(signal)
        if record_equity:
            equity_curve.append((bar.end_ms, float(equity)))
        if record_exposure:
            exposure_curve.append((bar.end_ms, float(active_exposure)))
        if record_risk:
            risk_curve.append(
                (
                    bar.end_ms,
                    float(intrabar_low_equity),
                    float(futures_quantity * bar.low),
                    float(total_quantity * bar.low),
                    float(active_exposure),
                )
            )

    if not bankrupt:
        final_price = bars[selected[-1]].close
        final_equity = cash_intercept + (spot_quantity + futures_quantity) * final_price
        closing_notional = (abs(spot_quantity) + abs(futures_quantity)) * final_price
        closing_cost = closing_notional * trade_cost_rate
        final_equity -= closing_cost
        total_fees += closing_notional * fee_rate
        if record_equity and equity_curve:
            equity_curve[-1] = (equity_curve[-1][0], float(final_equity))
    else:
        final_equity = Decimal("0")
    return DynamicResult(
        net_return=float(final_equity / initial_equity - Decimal("1")),
        max_drawdown=float(max_drawdown),
        completed_trades=completed_trades,
        total_fees=float(total_fees),
        total_funding=float(total_funding),
        bankrupt=bankrupt,
        equity_curve=tuple(equity_curve),
        exposure_curve=tuple(exposure_curve),
        risk_curve=tuple(risk_curve),
    )


def _target_quantities_after_cost(
    equity_before_cost: Decimal,
    price: Decimal,
    target: Decimal,
    current_spot: Decimal,
    current_futures: Decimal,
    trade_cost_rate: Decimal,
    fee_rate: Decimal,
    spot_exposure_cap: Decimal = Decimal("1"),
):
    if not Decimal("0") <= target <= Decimal("3"):
        raise ValueError("target exposure must be between zero and three")
    equity_after_cost = equity_before_cost
    desired_spot = Decimal("0")
    desired_futures = Decimal("0")
    changed_notional = Decimal("0")
    for _ in range(12):
        desired_spot = min(target, spot_exposure_cap) * equity_after_cost / price
        desired_futures = max(target - spot_exposure_cap, Decimal("0")) * equity_after_cost / price
        changed_notional = (
            abs(desired_spot - current_spot) + abs(desired_futures - current_futures)
        ) * price
        updated_equity = equity_before_cost - changed_notional * trade_cost_rate
        if abs(updated_equity - equity_after_cost) <= Decimal("0.00000001"):
            equity_after_cost = updated_equity
            break
        equity_after_cost = updated_equity
    trade_cost = changed_notional * trade_cost_rate
    fee = changed_notional * fee_rate
    return desired_spot, desired_futures, trade_cost, fee


def replay_dynamic_constant_exposure_legacy(
    bars: list[ResearchBar],
    targets: tuple[int | Decimal | None, ...],
    funding: list[list[FundingRate]] | None,
    start_ms: int,
    end_ms: int,
    *,
    fee_bps: Decimal = Decimal("5"),
    slippage_bps: Decimal = Decimal("2"),
    funding_on_excess_only: bool = False,
    record_equity: bool = False,
) -> DynamicResult:
    """Legacy constant-exposure replay retained only for model-difference audits.

    It compounds target exposure every bar without charging the implied continuous
    rebalancing costs and must not be used for candidate approval.
    """
    if len(targets) != len(bars):
        raise ValueError("target and bar lengths differ")
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        raise ValueError("no bars in requested range")
    funding = funding if funding is not None else [[] for _ in bars]
    fee_rate = fee_bps / Decimal("10000")
    slippage_rate = slippage_bps / Decimal("10000")
    trade_cost_rate = fee_rate + slippage_rate
    equity = Decimal("100000")
    peak = equity
    max_drawdown = Decimal("0")
    total_fees = Decimal("0")
    total_funding = Decimal("0")
    active_exposure = Decimal("0")
    pending = Decimal("1")
    previous_index = selected[0] - 1
    while previous_index >= 0:
        previous_target = targets[previous_index]
        if previous_target is not None:
            pending = Decimal(previous_target)
            break
        previous_index -= 1
    completed_trades = 0
    previous_close: Decimal | None = None
    bankrupt = False
    equity_curve: list[tuple[int, float]] = []

    for index in selected:
        bar = bars[index]
        if previous_close is not None:
            gap_return = bar.open / previous_close - Decimal("1")
            equity *= Decimal("1") + active_exposure * gap_return
        if equity <= 0:
            bankrupt = True
            max_drawdown = Decimal("-1")
            if record_equity:
                equity_curve.append((bar.end_ms, 0.0))
            break
        if pending != active_exposure:
            changed_notional = equity * abs(pending - active_exposure)
            cost = changed_notional * trade_cost_rate
            equity -= cost
            total_fees += changed_notional * fee_rate
            active_exposure = pending
            completed_trades += 1
        low_return = bar.low / bar.open - Decimal("1")
        intrabar_low_equity = equity * (Decimal("1") + active_exposure * low_return)
        if intrabar_low_equity <= 0:
            bankrupt = True
            equity = Decimal("0")
            max_drawdown = Decimal("-1")
            if record_equity:
                equity_curve.append((bar.end_ms, 0.0))
            break
        close_return = bar.close / bar.open - Decimal("1")
        equity *= Decimal("1") + active_exposure * close_return
        for event in funding[index]:
            funded_exposure = active_exposure
            if funding_on_excess_only:
                funded_exposure = max(Decimal("0"), active_exposure - Decimal("1"))
            amount = -(equity * funded_exposure * event.rate)
            equity += amount
            total_funding += amount
        peak = max(peak, equity)
        max_drawdown = min(
            max_drawdown,
            equity / peak - Decimal("1"),
            intrabar_low_equity / peak - Decimal("1"),
        )
        signal = targets[index]
        if signal is not None:
            pending = Decimal(signal)
        previous_close = bar.close
        if record_equity:
            equity_curve.append((bar.end_ms, float(equity)))

    if not bankrupt and active_exposure > 0:
        closing_notional = equity * active_exposure
        equity -= closing_notional * trade_cost_rate
        total_fees += closing_notional * fee_rate
        if record_equity and equity_curve:
            equity_curve[-1] = (equity_curve[-1][0], float(equity))
    return DynamicResult(
        net_return=float(equity / Decimal("100000") - Decimal("1")),
        max_drawdown=float(max_drawdown),
        completed_trades=completed_trades,
        total_fees=float(total_fees),
        total_funding=float(total_funding),
        bankrupt=bankrupt,
        equity_curve=tuple(equity_curve),
    )


def benchmark(bars, start, end):
    selected = [bar for bar in bars if start <= bar.start_ms <= end]
    if not selected:
        raise ValueError("no bars in requested benchmark range")
    first_open = selected[0].open
    peak = 1.0
    drawdown = 0.0
    for bar in selected:
        low_value = float(bar.low / first_open)
        close_value = float(bar.close / first_open)
        drawdown = min(drawdown, low_value / peak - 1)
        peak = max(peak, close_value)
    return {
        "net_return": float(selected[-1].close / first_open - Decimal("1")),
        "max_drawdown": drawdown,
    }


def as_dict(value):
    return {
        "net_return": value.net_return,
        "max_drawdown": value.max_drawdown,
        "completed_trades": value.completed_trades,
        "total_fees": value.total_fees,
        "total_funding": value.total_funding,
        "bankrupt": value.bankrupt,
    }


def markdown(payload):
    lines = [
        "# BTC 动态暴露（最大 3X）研究",
        "",
        "策略保留 1X BTC 核心仓位；1h SMA 趋势成立时提高到 2X，"
        "1h 与 4h 同时成立时提高到 3X。排序信号失效时只降回核心仓位，不立即清空。",
        "",
        "## 结果",
        "",
        "| 方案 | OOS收益 | OOS DD | 压力收益 | 压力DD | 永续Funding收益 | 全样本收益 | 全样本DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        oos = row["periods"]["oos"]
        stress = oos["stress"]
        perp = oos["perp_with_funding"]
        full = row["periods"]["full"]
        lines.append(
            f"| `{row['id']}` | {pct(oos['base']['net_return'])} | "
            f"{pct(oos['base']['max_drawdown'])} | "
            f"{pct(stress['net_return'])} | {pct(stress['max_drawdown'])} | "
            f"{pct(perp['net_return'])} | "
            f"{pct(full['base']['net_return'])} | {pct(full['base']['max_drawdown'])} |"
        )
    lines += ["", "## B&H 价格基线", ""]
    for name, value in payload["buy_and_hold_price_baseline"].items():
        lines.append(f"- {name}: {pct(value['net_return'])}, DD {pct(value['max_drawdown'])}")
    return "\n".join(lines) + "\n"


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return "n/a" if value is None else f"{value:.2%}"


if __name__ == "__main__":
    main()
