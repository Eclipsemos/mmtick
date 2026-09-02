#!/usr/bin/env python3
"""Confirm 4h MACD-divergence exits with the underlying 5m candle path."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from research_macd_divergence_funding import load_funding
from research_macd_divergence_timeframes import aggregate_bars, load_5m_market

from mastermind_tick.macd_divergence import (
    DivergenceConfig,
    ExecutionConfig,
    _complete_open_trade,
    _exit_outcome,
    _floor_quantity,
    _funding_by_bar,
    _summary,
    divergence_structures,
    entry_signals,
    indicator_series,
    replay_signals,
    swing_points,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("reports/experiments/macd_divergence/2026-08-28/timeframes"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/macd_divergence/2026-08-28/path_confirmation"),
    )
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "signal_timeframe": "4h",
            "path_timeframe": "5m",
            "path_rule": (
                "scan completed 5m bars in chronological order; stop wins if both levels hit"
            ),
            "selection": "4h candidate was frozen by research/validation before OOS",
            "funding": "same historical Binance funding events as the main report",
        },
        "symbols": {},
    }
    for symbol in (value.upper() for value in args.symbols):
        timeframe_path = args.input / f"{symbol.lower()}-timeframes.json"
        source = load_5m_market(symbol)
        bars = aggregate_bars(source, 240)
        payload = json.loads(timeframe_path.read_text(encoding="utf-8"))
        selected = payload["timeframes"]["240"]["selected"]
        result["symbols"][symbol] = evaluate(symbol, bars, source, selected)
        print(f"{symbol}: path confirmation complete", flush=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(markdown(result), encoding="utf-8")
    print(args.output_dir / "README.md")


def evaluate(symbol: str, bars, path_bars, selected: dict[str, Any]) -> dict[str, Any]:
    config = DivergenceConfig(**selected["structure"])
    execution = ExecutionConfig(**selected["execution"])
    indicators = indicator_series(bars)
    lows = swing_points(bars, indicators.histogram, config, "low")
    highs = swing_points(bars, indicators.histogram, config, "high")
    structures = tuple(
        sorted(
            divergence_structures(lows, indicators.atr, config.points, "LONG")
            + divergence_structures(highs, indicators.atr, config.points, "SHORT"),
            key=lambda item: (item.known_at, item.id),
        )
    )
    signals = entry_signals(structures, indicators.histogram)
    funding = load_funding(symbol, path_bars)
    starts = [bar.start_ms for bar in bars]
    periods = {
        "research": (0, _index_at_or_after(starts, utc_ms(2023, 1, 1))),
        "validation": (
            _index_at_or_after(starts, utc_ms(2023, 1, 1)),
            _index_at_or_after(starts, utc_ms(2025, 1, 1)),
        ),
        "oos": (_index_at_or_after(starts, utc_ms(2025, 1, 1)), len(bars)),
        "full": (0, len(bars)),
    }
    output: dict[str, Any] = {
        "candidate": selected["id"],
        "bars_4h": len(bars),
        "bars_5m": len(path_bars),
        "funding_events": len(funding),
        "signal_count": len(signals),
        "periods": {},
    }
    for name, (left, right) in periods.items():
        standard_no_funding = replay_signals(
            bars,
            indicators,
            signals,
            execution,
            symbol=symbol,
            timeframe_minutes=240,
            start_index=left,
            end_index=right,
        )
        standard_with_funding = replay_signals(
            bars,
            indicators,
            signals,
            execution,
            symbol=symbol,
            timeframe_minutes=240,
            funding=funding,
            start_index=left,
            end_index=right,
        )
        path_no_funding = replay_path(
            bars,
            path_bars,
            indicators,
            signals,
            execution,
            symbol=symbol,
            start_index=left,
            end_index=right,
            funding=[],
        )
        path_with_funding = replay_path(
            bars,
            path_bars,
            indicators,
            signals,
            execution,
            symbol=symbol,
            start_index=left,
            end_index=right,
            funding=funding,
        )
        output["periods"][name] = {
            "standard_ohlc": summary(standard_no_funding),
            "standard_ohlc_funding": summary(standard_with_funding),
            "path_5m": summary(path_no_funding),
            "path_5m_funding": summary(path_with_funding),
            "delta_net_return": path_no_funding.net_return - standard_no_funding.net_return,
            "delta_net_return_funding": path_with_funding.net_return
            - standard_with_funding.net_return,
            "delta_average_r": (path_no_funding.average_r or 0.0)
            - (standard_no_funding.average_r or 0.0),
            "delta_average_r_funding": (path_with_funding.average_r or 0.0)
            - (standard_with_funding.average_r or 0.0),
            "exit_reason_changes": exit_reason_changes(
                standard_no_funding.trades, path_no_funding.trades
            ),
        }
    return output


def replay_path(
    bars,
    path_bars,
    indicators,
    signals,
    execution: ExecutionConfig,
    *,
    symbol: str,
    start_index: int,
    end_index: int,
    funding,
):
    """Replay high-timeframe entries while resolving each exit on 5m bars."""
    fee_rate = Decimal(str(execution.fee_bps)) / Decimal("10000")
    slippage_rate = Decimal(str(execution.slippage_bps)) / Decimal("10000")
    risk_fraction = Decimal(str(execution.risk_fraction))
    stop_atr = Decimal(str(execution.stop_atr))
    reward_risk = Decimal(str(execution.reward_risk))
    max_leverage = (
        Decimal(str(execution.max_leverage)) if execution.max_leverage is not None else None
    )
    path_groups: dict[int, list] = {}
    for bar in path_bars:
        bucket = bar.start_ms // (240 * 60 * 1000) * (240 * 60 * 1000)
        path_groups.setdefault(bucket, []).append(bar)
    funding_by_index = _funding_by_bar(bars, funding)
    equity = Decimal(str(execution.initial_equity))
    peak = equity
    equity_curve: list[tuple[int, float]] = []
    drawdown_curve: list[tuple[int, float]] = []
    trades = []
    signal_cursor = 0
    while signal_cursor < len(signals) and signals[signal_cursor].entry_index < start_index:
        signal_cursor += 1
    open_trade = None
    exposure_bars = ambiguous_bars = capped_trades = 0
    funding_paid = 0.0
    for index in range(start_index, end_index):
        bar = bars[index]
        was_open = open_trade is not None
        if open_trade is not None:
            equity, funding_paid = apply_funding(
                equity, funding_paid, open_trade, funding_by_index[index]
            )
            exposure_bars += 1
            open_trade, trade, equity, ambiguous_bars = scan_path(
                open_trade,
                path_groups.get(bar.start_ms, ()),
                bars,
                index,
                fee_rate,
                slippage_rate,
                equity,
                symbol,
                ambiguous_bars,
                trades,
            )
            if trade is not None:
                trades.append(trade)
        while signal_cursor < len(signals) and signals[signal_cursor].entry_index < index:
            signal_cursor += 1
        if (
            not was_open
            and open_trade is None
            and signal_cursor < len(signals)
            and signals[signal_cursor].entry_index == index
        ):
            signal = signals[signal_cursor]
            signal_cursor += 1
            trigger = bars[signal.trigger_index]
            atr = indicators.atr[signal.trigger_index]
            macd = indicators.macd[signal.trigger_index]
            macd_signal = indicators.signal[signal.trigger_index]
            histogram = indicators.histogram[signal.trigger_index]
            if None not in (atr, macd, macd_signal, histogram):
                direction_sign = (
                    Decimal("1") if signal.structure.direction == "LONG" else Decimal("-1")
                )
                raw_entry = bar.open
                entry_price = raw_entry * (
                    1 + slippage_rate if direction_sign > 0 else 1 - slippage_rate
                )
                stop = (
                    trigger.low - Decimal(str(atr)) * stop_atr
                    if direction_sign > 0
                    else trigger.high + Decimal(str(atr)) * stop_atr
                )
                stop_distance = direction_sign * (entry_price - stop)
                if stop_distance > 0 and equity > 0:
                    requested_risk = equity * risk_fraction
                    quantity = requested_risk / stop_distance
                    leverage_capped = False
                    if max_leverage is not None:
                        max_quantity = equity * max_leverage / entry_price
                        if quantity > max_quantity:
                            quantity = max_quantity
                            leverage_capped = True
                    quantity = _floor_quantity(quantity)
                    if quantity > 0:
                        take_profit = entry_price + direction_sign * reward_risk * stop_distance
                        entry_fee = abs(entry_price * quantity) * fee_rate
                        actual_risk = quantity * stop_distance
                        equity -= entry_fee
                        capped_trades += int(leverage_capped)
                        open_trade = {
                            "signal": signal,
                            "direction": signal.structure.direction,
                            "direction_sign": direction_sign,
                            "entry_price": entry_price,
                            "entry_equity": equity + entry_fee,
                            "entry_fee": entry_fee,
                            "entry_slippage": abs(entry_price - raw_entry) * quantity,
                            "quantity": quantity,
                            "stop": stop,
                            "take_profit": take_profit,
                            "risk_amount": actual_risk,
                            "risk_fraction": actual_risk / (equity + entry_fee),
                            "atr": atr,
                            "macd": macd,
                            "macd_signal": macd_signal,
                            "histogram": histogram,
                            "leverage_capped": leverage_capped,
                            "funding": Decimal("0"),
                        }
                        equity, funding_paid = apply_funding(
                            equity, funding_paid, open_trade, funding_by_index[index]
                        )
                        exposure_bars += 1
                        open_trade, trade, equity, ambiguous_bars = scan_path(
                            open_trade,
                            path_groups.get(bar.start_ms, ()),
                            bars,
                            index,
                            fee_rate,
                            slippage_rate,
                            equity,
                            symbol,
                            ambiguous_bars,
                            trades,
                        )
                        if trade is not None:
                            trades.append(trade)
        marked = equity
        if open_trade is not None:
            sign = open_trade["direction_sign"]
            marked += sign * open_trade["quantity"] * (bar.close - open_trade["entry_price"])
        peak = max(peak, marked)
        drawdown = marked / peak - 1 if peak > 0 else Decimal("-1")
        equity_curve.append((bar.end_ms, float(marked)))
        drawdown_curve.append((bar.end_ms, float(drawdown)))
    if open_trade is not None:
        last = path_groups.get(bars[end_index - 1].start_ms, ())
        exit_bar = last[-1] if last else bars[end_index - 1]
        trade, equity_delta = _complete_open_trade(
            open_trade,
            bars,
            exit_bar,
            end_index - 1,
            ("END_OF_DATA", exit_bar.close, False),
            fee_rate,
            slippage_rate,
            symbol,
            240,
        )
        equity += equity_delta
        trades.append(trade)
        equity_curve[-1] = (exit_bar.end_ms, float(equity))
        peak = max(value for _, value in equity_curve)
        drawdown_curve[-1] = (exit_bar.end_ms, float(equity) / peak - 1 if peak > 0 else -1.0)
    return _summary(
        bars[start_index:end_index],
        execution.initial_equity,
        float(equity),
        trades,
        equity_curve,
        drawdown_curve,
        exposure_bars,
        ambiguous_bars,
        capped_trades,
        funding_paid,
    )


def apply_funding(equity, funding_paid, open_trade, events):
    for event in events:
        amount = -(
            open_trade["direction_sign"]
            * open_trade["quantity"]
            * Decimal(str(event.mark_price))
            * event.rate
        )
        equity += amount
        open_trade["funding"] += amount
        funding_paid += float(amount)
    return equity, funding_paid


def scan_path(
    open_trade,
    path,
    bars,
    index,
    fee_rate,
    slippage_rate,
    equity,
    symbol,
    ambiguous_bars,
    trades,
):
    for path_bar in path:
        outcome = _exit_outcome(path_bar, open_trade)
        if outcome is None:
            continue
        trade, equity_delta = _complete_open_trade(
            open_trade,
            bars,
            path_bar,
            index,
            outcome,
            fee_rate,
            slippage_rate,
            symbol,
            240,
        )
        return None, trade, equity + equity_delta, ambiguous_bars + int(trade.ambiguous_exit)
    return open_trade, None, equity, ambiguous_bars


def summary(result) -> dict[str, Any]:
    return {
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "average_r": result.average_r,
        "profit_factor": result.profit_factor,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "ambiguous_bars": result.ambiguous_bars,
        "fees_paid": result.fees_paid,
        "funding_paid": result.funding_paid,
    }


def exit_reason_changes(standard, path) -> dict[str, int]:
    changes: dict[str, int] = {}
    for old, new in zip(standard, path, strict=False):
        if old.exit_reason != new.exit_reason:
            key = f"{old.exit_reason}->{new.exit_reason}"
            changes[key] = changes.get(key, 0) + 1
    return changes


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 4h MACD 背离：5m 路径确认",
        "",
        (
            "4h 信号和入场保持冻结回测协议；仅把每根 4h K 线内的 SL/TP "
            "触发顺序改为扫描已完成 5m K 线。"
        ),
        "同一根 5m 同时触及两价位仍按 Stop 优先。",
        "",
    ]
    for symbol, item in payload["symbols"].items():
        lines.extend([f"## {symbol}", "", f"候选：`{item['candidate']}`", ""])
        lines.extend(
            [
                "| 分区 | 标准收益 | 5m 路径收益 | 标准含 Funding | "
                "路径含 Funding | 路径平均 R | 歧义柱 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for period, values in item["periods"].items():
            old = values["standard_ohlc"]
            new = values["path_5m"]
            old_funding = values["standard_ohlc_funding"]
            new_funding = values["path_5m_funding"]
            lines.append(
                f"| {period} | {percent(old['net_return'])} | {percent(new['net_return'])} | "
                f"{percent(old_funding['net_return'])} | {percent(new_funding['net_return'])} | "
                f"{number(new['average_r'])} | {old['ambiguous_bars']}/{new['ambiguous_bars']} |"
            )
        lines.append("")
        for period, values in item["periods"].items():
            if values["exit_reason_changes"]:
                lines.append(f"{period} 退出原因变化：`{values['exit_reason_changes']}`。")
        lines.append("")
    return "\n".join(lines)


def _index_at_or_after(values: list[int], target: int) -> int:
    for index, value in enumerate(values):
        if value >= target:
            return index
    return len(values)


def utc_ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    main()
