#!/usr/bin/env python3
# ruff: noqa: E501
"""Write a completed-trade report for the configured SOXL ATR strategy."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from mastermind_tick.backtest import (
    ReplayATRTickStrategy,
    ReplayBroker,
    ReplayCandidate,
    ReplayParameters,
    _candidate_result,
    _load_funding_rates,
    _load_warmup_bars,
)
from mastermind_tick.config import InstrumentSettings, Settings, load_settings
from mastermind_tick.models import Tick

BEIJING = ZoneInfo("Asia/Shanghai")
DEFAULT_START = "2026-08-01T00:00:00+00:00"
DEFAULT_OUTPUT = "reports/experiments/soxl_atr/2026-08-trades.md"


def _timestamp_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    return int(parsed.timestamp() * 1000)


def _format_timestamp(timestamp_ms: int) -> str:
    utc = datetime.fromtimestamp(timestamp_ms / 1000, UTC)
    beijing = utc.astimezone(BEIJING)
    return f"{utc:%Y-%m-%d %H:%M:%S} UTC<br>{beijing:%m-%d %H:%M:%S} 北京时间"


def _format_duration(start_ms: int, end_ms: int) -> str:
    seconds = (end_ms - start_ms) // 1000
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours}h {minutes}m {seconds}s"


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):+.2f}%"


def _tick_from_row(row: sqlite3.Row) -> Tick:
    return Tick(
        event_id=row["event_id"],
        timestamp_ms=int(row["timestamp_ms"]),
        price=Decimal(row["price"]),
        quantity=Decimal(row["quantity"]),
        source=row["source"],
        first_trade_id=row["first_trade_id"],
        last_trade_id=row["last_trade_id"],
        open_price=Decimal(row["open_price"]) if row["open_price"] is not None else None,
        high_price=Decimal(row["high_price"]) if row["high_price"] is not None else None,
        low_price=Decimal(row["low_price"]) if row["low_price"] is not None else None,
    )


def _build_candidate(
    settings: Settings,
    instrument: InstrumentSettings,
    warmup_bars: list[object],
) -> ReplayCandidate:
    strategy = ReplayATRTickStrategy(
        settings.strategy.atr_period,
        settings.strategy.atr_multiplier,
        settings.strategy.bar_minutes,
        settings.strategy.trend_efficiency_period,
        settings.strategy.minimum_trend_efficiency,
        settings.strategy.reversal_confirmation_atr,
    )
    strategy.bootstrap(warmup_bars)
    position_fraction = Decimal(
        str(instrument.position_fraction or settings.strategy.position_fraction)
    )
    fee_bps = Decimal(str(instrument.fee_bps or settings.execution.fee_bps))
    slippage_bps = Decimal(str(instrument.slippage_bps or settings.execution.slippage_bps))
    minimum_notional = Decimal(
        str(instrument.minimum_notional or settings.execution.minimum_notional)
    )
    return ReplayCandidate(
        parameters=ReplayParameters(
            atr_period=settings.strategy.atr_period,
            atr_multiplier=settings.strategy.atr_multiplier,
        ),
        strategy=strategy,
        broker=ReplayBroker(
            instrument,
            Decimal(str(settings.initial_cash)),
            position_fraction,
            fee_bps,
            slippage_bps,
            minimum_notional,
        ),
        direction="long_only",
    )


def _render_report(
    *,
    settings: Settings,
    instrument: InstrumentSettings,
    candidate: ReplayCandidate,
    start_ms: int,
    end_ms: int,
    tick_count: int,
    raw_trade_count: int,
    warmup_bars: int,
    funding_events: int,
    last_price: Decimal,
) -> str:
    result = _candidate_result(
        candidate,
        instrument,
        start_ms,
        end_ms,
        tick_count,
        raw_trade_count,
        warmup_bars,
        last_price,
    )
    trades = candidate.broker.trades
    realized_pnl = sum((trade.net_pnl for trade in trades), Decimal("0"))
    wins = sum(trade.net_pnl > 0 for trade in trades)
    win_rate = Decimal(wins) / Decimal(len(trades)) if trades else Decimal("0")
    gross_pnl = sum((trade.net_pnl + trade.fees - trade.funding for trade in trades), Decimal("0"))
    total_fees = sum((trade.fees for trade in trades), Decimal("0"))
    total_funding = sum((trade.funding for trade in trades), Decimal("0"))
    initial_equity = Decimal(str(result.initial_equity))
    final_equity = Decimal(str(result.final_equity))
    ending_unrealized = final_equity - initial_equity - realized_pnl
    position = candidate.broker

    lines = [
        "# SOXLUSDT ATR Strategy: August 2026 Trade Rounds",
        "",
        "## Scope",
        "",
        f"- Replay period: `{_format_timestamp(start_ms)}` to `{_format_timestamp(end_ms)}`.",
        f"- Market data cutoff: `{_format_timestamp(end_ms)}`; later August data was not present in the local warehouse when this report was generated.",
        f"- Data replayed: {tick_count:,} aggregated ticks representing {raw_trade_count:,} exchange trades; {warmup_bars} closed 15-minute bars used only for warmup.",
        f"- Funding observations in replay window: {funding_events}.",
        "",
        "## Strategy And Assumptions",
        "",
        f"- Direction: long-only; `SOXLUSDT` perpetual futures; {settings.strategy.bar_minutes}-minute Tick ATR execution.",
        f"- ATR: period {settings.strategy.atr_period}, multiplier {settings.strategy.atr_multiplier:g}; trend efficiency: {settings.strategy.trend_efficiency_period} bars / {settings.strategy.minimum_trend_efficiency:g} minimum.",
        f"- Reversal confirmation: {settings.strategy.reversal_confirmation_atr:g} ATR; one action per K line.",
        "- Profit protection: disabled. Continuation re-entry: disabled.",
        f"- Sizing: {instrument.leverage}x isolated leverage x {float(instrument.position_fraction or settings.strategy.position_fraction):.1%} equity allocation = {instrument.leverage * float(instrument.position_fraction or settings.strategy.position_fraction):.2f}x target exposure.",
        f"- Costs: {float(instrument.fee_bps or settings.execution.fee_bps):g} bps per fill and {float(instrument.slippage_bps or settings.execution.slippage_bps):g} bps simulated slippage per fill; recorded funding is included.",
        "",
        "## Summary",
        "",
        "| Completed rounds | Wins | Win rate | Realized net PnL | Gross PnL | Fees | Funding | Mark-to-market equity | Period return | Max drawdown |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {len(trades)} | {wins} | {_percent(win_rate)} | ${_money(realized_pnl)} | "
            f"${_money(gross_pnl)} | ${_money(total_fees)} | ${_money(total_funding)} | "
            f"${_money(final_equity)} | {_percent(Decimal(str(result.net_return)))} | "
            f"{_percent(Decimal(str(result.max_drawdown)))} |"
        ),
        "",
        "The per-round return below is net PnL divided by entry notional, not return on margin. "
        "The period return is mark-to-market equity return on the $100,000 initial account.",
        "",
        "## Completed Trade Rounds",
        "",
        "| # | Direction | Open (UTC / Beijing) | Close (UTC / Beijing) | Duration | Entry | Exit | Qty | Gross PnL | Fees | Funding | Net PnL | Net / entry notional |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, trade in enumerate(trades, start=1):
        gross = trade.net_pnl + trade.fees - trade.funding
        entry_notional = trade.entry_price * trade.quantity
        return_on_notional = trade.net_pnl / entry_notional if entry_notional else Decimal("0")
        lines.append(
            f"| {index} | {trade.direction} | {_format_timestamp(trade.entry_at_ms)} | "
            f"{_format_timestamp(trade.exit_at_ms)} | {_format_duration(trade.entry_at_ms, trade.exit_at_ms)} | "
            f"${trade.entry_price:.4f} | ${trade.exit_price:.4f} | {trade.quantity:.2f} | "
            f"${_money(gross)} | ${_money(trade.fees)} | ${_money(trade.funding)} | "
            f"${_money(trade.net_pnl)} | {_percent(return_on_notional)} |"
        )
    if not trades:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - |")

    lines.extend(["", "## Open Round At Data Cutoff", ""])
    if position.has_position and position.open_trade is not None:
        open_trade = position.open_trade
        unrealized = position.quantity * (last_price - position.average_price)
        if position.is_short:
            unrealized = -unrealized
        estimated_exit_fee = abs(position.quantity) * last_price * position.fee_rate
        estimated_net = unrealized - estimated_exit_fee + open_trade.funding - open_trade.entry_fee
        lines.extend(
            [
                "This position remains open and is excluded from completed-round statistics.",
                "",
                "| Direction | Open (UTC / Beijing) | Entry | Last mark | Qty | Unrealized before estimated exit fee | Entry fee + funding | Estimated net if closed at last mark |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
                (
                    f"| {open_trade.direction} | {_format_timestamp(open_trade.entry_at_ms)} | "
                    f"${open_trade.entry_price:.4f} | ${last_price:.4f} | {open_trade.quantity:.2f} | "
                    f"${_money(unrealized)} | ${_money(open_trade.entry_fee + open_trade.funding)} | "
                    f"${_money(estimated_net)} |"
                ),
                "",
                f"Marked unrealized contribution to ending equity (before a hypothetical exit cost): ${_money(ending_unrealized)}.",
            ]
        )
    else:
        lines.append("No open position at the local data cutoff.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--instrument", default="soxl_perp")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", help="Inclusive ISO-8601 end timestamp; defaults to local data cutoff")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    settings = load_settings(args.config)
    instrument = next(item for item in settings.instruments if item.id == args.instrument)
    start_ms = _timestamp_ms(args.start)
    database_uri = f"file:{settings.database_path}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        last_row = connection.execute(
            "SELECT MAX(timestamp_ms) AS last_ms FROM agg_trades WHERE instrument_id = ?",
            (instrument.market_id,),
        ).fetchone()
        if last_row is None or last_row["last_ms"] is None:
            raise ValueError(f"no aggTrade data for {instrument.id}")
        end_ms = min(_timestamp_ms(args.end), int(last_row["last_ms"])) if args.end else int(last_row["last_ms"])
        if end_ms <= start_ms:
            raise ValueError("end must be after start")
        warmup_bars = _load_warmup_bars(
            connection, instrument.market_id, start_ms, settings.warmup_bars
        )
        if len(warmup_bars) < settings.warmup_bars:
            raise ValueError("insufficient pre-replay warmup bars")
        funding_rates = _load_funding_rates(connection, instrument.market_id, start_ms, end_ms)
        candidate = _build_candidate(settings, instrument, warmup_bars)
        tick_count = 0
        raw_trade_count = 0
        last_price: Decimal | None = None
        rows = connection.execute(
            """
            SELECT event_id, timestamp_ms, price, open_price, high_price, low_price,
                   quantity, source, first_trade_id, last_trade_id
            FROM agg_trades
            WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
            ORDER BY timestamp_ms, received_at_ms, event_id
            """,
            (instrument.market_id, start_ms, end_ms),
        )
        for row in rows:
            tick = _tick_from_row(row)
            candidate.process_tick(tick, funding_rates)
            tick_count += 1
            raw_trade_count += (
                int(row["last_trade_id"]) - int(row["first_trade_id"]) + 1
                if row["first_trade_id"] is not None and row["last_trade_id"] is not None
                else 1
            )
            last_price = tick.price

    if last_price is None:
        raise ValueError("no ticks in requested replay range")
    report = _render_report(
        settings=settings,
        instrument=instrument,
        candidate=candidate,
        start_ms=start_ms,
        end_ms=end_ms,
        tick_count=tick_count,
        raw_trade_count=raw_trade_count,
        warmup_bars=len(warmup_bars),
        funding_events=len(funding_rates),
        last_price=last_price,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
