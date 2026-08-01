"""Tick-level ATR parameter replay against the persisted market warehouse."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.config import InstrumentSettings, Settings, load_settings
from mastermind_tick.models import Bar, FundingRate, Side, Tick
from mastermind_tick.strategy import ATRTickStrategy, true_range, wilder_atr

DEFAULT_PERIODS = (5, 7, 10, 14, 21, 28, 35, 42, 56)
DEFAULT_MULTIPLIERS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)


@dataclass(frozen=True)
class ReplayParameters:
    atr_period: int
    atr_multiplier: float


@dataclass
class ReplayTrade:
    direction: str
    entry_at_ms: int
    exit_at_ms: int
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal


@dataclass
class OpenReplayTrade:
    direction: str
    entry_at_ms: int
    entry_price: Decimal
    quantity: Decimal
    entry_fee: Decimal
    funding: Decimal = Decimal("0")


@dataclass
class ReplayResult:
    instrument_id: str
    symbol: str
    paper_model: str
    atr_period: int
    atr_multiplier: float
    start_ms: int
    end_ms: int
    tick_count: int
    raw_trade_count: int
    warmup_bars: int
    initial_equity: float
    final_equity: float
    net_profit: float
    net_return: float
    completed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float | None
    max_drawdown: float
    total_fees: float
    total_funding: float
    signals: int
    ending_position: str


class ReplayATRTickStrategy(ATRTickStrategy):
    """Equivalent ATR calculation that avoids replaying all prior bars per Tick."""

    def __init__(self, period: int, multiplier: float, bar_minutes: int):
        super().__init__(period, multiplier, bar_minutes)
        self._closed_signature: tuple[int, int | None] | None = None
        self._closed_atr: Decimal | None = None

    def _current_atr(self) -> Decimal | None:
        if self.current_bar is None:
            return None
        latest_start = self.completed_bars[-1].start_ms if self.completed_bars else None
        signature = (len(self.completed_bars), latest_start)
        if signature != self._closed_signature:
            self._closed_signature = signature
            self._closed_atr = wilder_atr(self.completed_bars, self.period)
        if self._closed_atr is None or len(self.completed_bars) < self.period:
            return wilder_atr([*self.completed_bars, self.current_bar], self.period)
        current_range = true_range(self.current_bar, self.completed_bars[-1].close)
        return (
            self._closed_atr * Decimal(self.period - 1) + current_range
        ) / Decimal(self.period)


class ReplayBroker:
    def __init__(
        self,
        instrument: InstrumentSettings,
        initial_cash: Decimal,
        position_fraction: Decimal,
        fee_bps: Decimal,
        slippage_bps: Decimal,
        minimum_notional: Decimal,
    ):
        self.instrument = instrument
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.position_fraction = position_fraction
        self.fee_rate = fee_bps / Decimal("10000")
        self.slippage_rate = slippage_bps / Decimal("10000")
        self.minimum_notional = minimum_notional
        self.step = Decimal(str(instrument.quantity_step))
        self.leverage = Decimal(instrument.leverage)
        self.quantity = Decimal("0")
        self.average_price = Decimal("0")
        self.total_fees = Decimal("0")
        self.total_funding = Decimal("0")
        self.trades: list[ReplayTrade] = []
        self.open_trade: OpenReplayTrade | None = None
        self.peak_equity = initial_cash
        self.max_drawdown = Decimal("0")

    @property
    def has_position(self) -> bool:
        return self.quantity != 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    def fill(self, side: Side, market_price: Decimal, timestamp_ms: int) -> bool:
        fill_price = market_price * (
            Decimal("1") + self.slippage_rate
            if side is Side.BUY
            else Decimal("1") - self.slippage_rate
        )
        if self.instrument.paper_model == "futures":
            return self._fill_futures(side, fill_price, timestamp_ms)
        return self._fill_spot(side, fill_price, timestamp_ms)

    def _fill_spot(self, side: Side, fill_price: Decimal, timestamp_ms: int) -> bool:
        if side is Side.BUY:
            if self.quantity > 0:
                return False
            budget = self.cash * self.position_fraction
            quantity = _floor_step(
                budget / (fill_price * (Decimal("1") + self.fee_rate)), self.step
            )
            notional = fill_price * quantity
            fee = notional * self.fee_rate
            if (
                quantity <= 0
                or notional < self.minimum_notional
                or notional + fee > self.cash
            ):
                return False
            self.cash -= notional + fee
            self.quantity = quantity
            self.average_price = fill_price
            self.total_fees += fee
            self.open_trade = OpenReplayTrade(
                direction="LONG",
                entry_at_ms=timestamp_ms,
                entry_price=fill_price,
                quantity=quantity,
                entry_fee=fee,
            )
            return True

        if self.quantity <= 0:
            return False
        quantity = self.quantity
        notional = fill_price * quantity
        fee = notional * self.fee_rate
        self.cash += notional - fee
        self.total_fees += fee
        self._complete_trade(fill_price, fee, timestamp_ms)
        self.quantity = Decimal("0")
        self.average_price = Decimal("0")
        return True

    def _fill_futures(self, side: Side, fill_price: Decimal, timestamp_ms: int) -> bool:
        desired_sign = Decimal("1") if side is Side.BUY else Decimal("-1")
        if self.quantity * desired_sign > 0:
            return False

        if self.quantity:
            close_quantity = abs(self.quantity)
            close_fee = fill_price * close_quantity * self.fee_rate
            close_realized = self.quantity * (fill_price - self.average_price) - close_fee
            self.cash += close_realized
            self.total_fees += close_fee
            self._complete_trade(fill_price, close_fee, timestamp_ms)
            self.quantity = Decimal("0")
            self.average_price = Decimal("0")

        budget = self.cash * self.position_fraction
        quantity = _floor_step(budget * self.leverage / fill_price, self.step)
        notional = fill_price * quantity
        fee = notional * self.fee_rate
        required_balance = notional / self.leverage + fee
        if (
            quantity <= 0
            or notional < self.minimum_notional
            or required_balance > self.cash
        ):
            return False
        self.quantity = desired_sign * quantity
        self.average_price = fill_price
        self.cash -= fee
        self.total_fees += fee
        self.open_trade = OpenReplayTrade(
            direction="LONG" if desired_sign > 0 else "SHORT",
            entry_at_ms=timestamp_ms,
            entry_price=fill_price,
            quantity=quantity,
            entry_fee=fee,
        )
        return True

    def _complete_trade(
        self, exit_price: Decimal, exit_fee: Decimal, timestamp_ms: int
    ) -> None:
        trade = self.open_trade
        if trade is None:
            return
        direction_sign = Decimal("1") if trade.direction == "LONG" else Decimal("-1")
        gross_pnl = direction_sign * trade.quantity * (exit_price - trade.entry_price)
        fees = trade.entry_fee + exit_fee
        self.trades.append(
            ReplayTrade(
                direction=trade.direction,
                entry_at_ms=trade.entry_at_ms,
                exit_at_ms=timestamp_ms,
                entry_price=trade.entry_price,
                exit_price=exit_price,
                quantity=trade.quantity,
                fees=fees,
                funding=trade.funding,
                net_pnl=gross_pnl - fees + trade.funding,
            )
        )
        self.open_trade = None

    def apply_funding(self, funding: FundingRate) -> Decimal:
        if self.instrument.paper_model != "futures" or not self.quantity:
            return Decimal("0")
        amount = -(self.quantity * funding.mark_price * funding.rate)
        self.cash += amount
        self.total_funding += amount
        if self.open_trade is not None:
            self.open_trade.funding += amount
        return amount

    def equity(self, market_price: Decimal) -> Decimal:
        if self.instrument.paper_model == "futures":
            return self.cash + self.quantity * (market_price - self.average_price)
        return self.cash + self.quantity * market_price

    def mark(self, market_price: Decimal) -> Decimal:
        equity = self.equity(market_price)
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            self.max_drawdown = min(
                self.max_drawdown, equity / self.peak_equity - Decimal("1")
            )
        return equity


@dataclass
class ReplayCandidate:
    parameters: ReplayParameters
    strategy: ReplayATRTickStrategy
    broker: ReplayBroker
    pending_side: Side | None = None
    pending_tick_id: str | None = None
    signals: int = 0
    funding_index: int = 0

    def process_tick(self, tick: Tick, funding_rates: list[FundingRate]) -> None:
        while (
            self.funding_index < len(funding_rates)
            and funding_rates[self.funding_index].timestamp_ms <= tick.timestamp_ms
        ):
            self.broker.apply_funding(funding_rates[self.funding_index])
            self.funding_index += 1

        if self.pending_side is not None and tick.event_id != self.pending_tick_id:
            self.broker.fill(self.pending_side, tick.price, tick.timestamp_ms)
            self.pending_side = None
            self.pending_tick_id = None

        signal = self.strategy.on_tick(
            tick,
            has_position=self.broker.has_position,
            has_pending_order=self.pending_side is not None,
            allow_short=self.broker.instrument.paper_model == "futures",
            is_short=self.broker.is_short,
        )
        if signal is not None:
            self.pending_side = signal.side
            self.pending_tick_id = signal.tick_id
            self.signals += 1
        self.broker.mark(tick.price)


def run_parameter_grid(
    settings: Settings,
    instrument: InstrumentSettings,
    parameters: Iterable[ReplayParameters],
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> tuple[dict[str, Any], list[ReplayResult]]:
    database_uri = f"file:{settings.database_path}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        available = connection.execute(
            """
            SELECT MIN(timestamp_ms) AS first_ms, MAX(timestamp_ms) AS last_ms,
                   COUNT(*) AS tick_count,
                   COALESCE(SUM(
                       CASE WHEN first_trade_id IS NOT NULL AND last_trade_id IS NOT NULL
                            THEN last_trade_id - first_trade_id + 1 ELSE 1 END
                   ), 0) AS raw_trade_count
            FROM agg_trades WHERE instrument_id = ?
            """,
            (instrument.id,),
        ).fetchone()
        if available is None or available["first_ms"] is None:
            raise ValueError(f"no aggTrade data for {instrument.id}")
        replay_start = max(int(available["first_ms"]), start_ms or int(available["first_ms"]))
        replay_end = min(int(available["last_ms"]), end_ms or int(available["last_ms"]))
        if replay_start >= replay_end:
            raise ValueError(f"invalid replay range for {instrument.id}")

        warmup_bars = _load_warmup_bars(
            connection, instrument.id, replay_start, settings.warmup_bars
        )
        if not warmup_bars:
            raise ValueError(f"no pre-replay OHLCV warmup for {instrument.id}")
        funding_rates = _load_funding_rates(connection, instrument.id, replay_start, replay_end)

        position_fraction = Decimal(str(
            instrument.position_fraction
            if instrument.position_fraction is not None
            else settings.strategy.position_fraction
        ))
        fee_bps = Decimal(str(
            instrument.fee_bps
            if instrument.fee_bps is not None
            else settings.execution.fee_bps
        ))
        slippage_bps = Decimal(str(
            instrument.slippage_bps
            if instrument.slippage_bps is not None
            else settings.execution.slippage_bps
        ))
        minimum_notional = Decimal(str(
            instrument.minimum_notional
            if instrument.minimum_notional is not None
            else settings.execution.minimum_notional
        ))
        candidates = []
        for item in parameters:
            strategy = ReplayATRTickStrategy(
                item.atr_period, item.atr_multiplier, settings.strategy.bar_minutes
            )
            strategy.bootstrap(warmup_bars)
            candidates.append(
                ReplayCandidate(
                    parameters=item,
                    strategy=strategy,
                    broker=ReplayBroker(
                        instrument,
                        Decimal(str(settings.initial_cash)),
                        position_fraction,
                        fee_bps,
                        slippage_bps,
                        minimum_notional,
                    ),
                )
            )

        tick_count = 0
        raw_trade_count = 0
        last_price: Decimal | None = None
        rows = connection.execute(
            """
            SELECT event_id, timestamp_ms, price, quantity, source,
                   first_trade_id, last_trade_id
            FROM agg_trades
            WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
            ORDER BY timestamp_ms, received_at_ms, event_id
            """,
            (instrument.id, replay_start, replay_end),
        )
        for row in rows:
            tick = Tick(
                event_id=row["event_id"],
                timestamp_ms=int(row["timestamp_ms"]),
                price=Decimal(row["price"]),
                quantity=Decimal(row["quantity"]),
                source=row["source"],
                first_trade_id=row["first_trade_id"],
                last_trade_id=row["last_trade_id"],
            )
            for candidate in candidates:
                candidate.process_tick(tick, funding_rates)
            tick_count += 1
            raw_trade_count += (
                int(row["last_trade_id"]) - int(row["first_trade_id"]) + 1
                if row["first_trade_id"] is not None and row["last_trade_id"] is not None
                else 1
            )
            last_price = tick.price

    if last_price is None:
        raise ValueError(f"no aggTrade data in selected range for {instrument.id}")

    results = [
        _candidate_result(
            candidate,
            instrument,
            replay_start,
            replay_end,
            tick_count,
            raw_trade_count,
            len(warmup_bars),
            last_price,
        )
        for candidate in candidates
    ]
    metadata = {
        "instrument_id": instrument.id,
        "symbol": instrument.symbol,
        "paper_model": instrument.paper_model,
        "start_ms": replay_start,
        "end_ms": replay_end,
        "tick_count": tick_count,
        "raw_trade_count": raw_trade_count,
        "warmup_bars": len(warmup_bars),
        "fee_bps": float(fee_bps),
        "slippage_bps": float(slippage_bps),
        "leverage": instrument.leverage,
        "position_fraction": float(position_fraction),
        "funding_events": len(funding_rates),
    }
    return metadata, results


def _candidate_result(
    candidate: ReplayCandidate,
    instrument: InstrumentSettings,
    start_ms: int,
    end_ms: int,
    tick_count: int,
    raw_trade_count: int,
    warmup_bars: int,
    last_price: Decimal,
) -> ReplayResult:
    broker = candidate.broker
    final_equity = broker.equity(last_price)
    net_profit = final_equity - broker.initial_cash
    wins = sum(trade.net_pnl > 0 for trade in broker.trades)
    losses = len(broker.trades) - wins
    ending_position = "SHORT" if broker.quantity < 0 else "LONG" if broker.quantity > 0 else "FLAT"
    return ReplayResult(
        instrument_id=instrument.id,
        symbol=instrument.symbol,
        paper_model=instrument.paper_model,
        atr_period=candidate.parameters.atr_period,
        atr_multiplier=candidate.parameters.atr_multiplier,
        start_ms=start_ms,
        end_ms=end_ms,
        tick_count=tick_count,
        raw_trade_count=raw_trade_count,
        warmup_bars=warmup_bars,
        initial_equity=float(broker.initial_cash),
        final_equity=float(final_equity),
        net_profit=float(net_profit),
        net_return=float(net_profit / broker.initial_cash),
        completed_trades=len(broker.trades),
        winning_trades=wins,
        losing_trades=losses,
        win_rate=wins / len(broker.trades) if broker.trades else None,
        max_drawdown=float(broker.max_drawdown),
        total_fees=float(broker.total_fees),
        total_funding=float(broker.total_funding),
        signals=candidate.signals,
        ending_position=ending_position,
    )


def _load_warmup_bars(
    connection: sqlite3.Connection,
    instrument_id: str,
    start_ms: int,
    limit: int = 200,
) -> list[Bar]:
    rows = connection.execute(
        """
        SELECT * FROM (
            SELECT start_ms, end_ms, open, high, low, close, volume, trade_count
            FROM ohlcv_bars
            WHERE instrument_id = ? AND interval_minutes = 15
              AND is_closed = 1 AND end_ms < ?
            ORDER BY start_ms DESC LIMIT ?
        ) ORDER BY start_ms
        """,
        (instrument_id, start_ms, limit),
    )
    return [
        Bar(
            start_ms=int(row["start_ms"]),
            end_ms=int(row["end_ms"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
            trade_count=int(row["trade_count"]),
        )
        for row in rows
    ]


def _load_funding_rates(
    connection: sqlite3.Connection,
    account_id: str,
    start_ms: int,
    end_ms: int,
) -> list[FundingRate]:
    rows = connection.execute(
        """
        SELECT timestamp_ms, rate, mark_price FROM funding_rates
        WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
        ORDER BY timestamp_ms
        """,
        (account_id, start_ms, end_ms),
    )
    return [
        FundingRate(
            timestamp_ms=int(row["timestamp_ms"]),
            rate=Decimal(row["rate"]),
            mark_price=Decimal(row["mark_price"]),
        )
        for row in rows
    ]


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def select_recommendation(results: list[ReplayResult]) -> ReplayResult:
    if not results:
        raise ValueError("cannot select from empty replay results")
    return max(results, key=lambda item: (item.net_return, item.max_drawdown))


def build_report(payload: dict[str, Any]) -> str:
    lines = [
        "# ATR Tick Replay Grid",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        (
            "Selection rule: highest net return after fees, slippage and funding; "
            "max drawdown breaks ties."
        ),
        "",
    ]
    for run in payload["runs"]:
        metadata = run["metadata"]
        recommendation = run["recommendation"]
        lines.extend(
            [
                f"## {metadata['symbol']} ({metadata['paper_model']})",
                "",
                (
                    f"Range: {_iso(metadata['start_ms'])} to {_iso(metadata['end_ms'])}; "
                    f"{metadata['tick_count']:,} stored ticks / "
                    f"{metadata['raw_trade_count']:,} underlying trades; "
                    f"{metadata['warmup_bars']} warmup bars."
                ),
                "",
                (
                    f"Recommended in this sample: ATR({recommendation['atr_period']}) x "
                    f"{recommendation['atr_multiplier']:.2f}."
                ),
                "",
                (
                    "| ATR | Mult | Net return | Net PnL | Trades | Win rate | "
                    "Max DD | Fees | Funding |"
                ),
                "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        ordered = sorted(
            run["results"],
            key=lambda item: (item["net_return"], item["max_drawdown"]),
            reverse=True,
        )
        for item in ordered:
            win_rate = "--" if item["win_rate"] is None else f"{item['win_rate']:.2%}"
            lines.append(
                f"| {item['atr_period']} | {item['atr_multiplier']:.2f} | "
                f"{item['net_return']:.2%} | {item['net_profit']:,.2f} | "
                f"{item['completed_trades']} | {win_rate} | {item['max_drawdown']:.2%} | "
                f"{item['total_fees']:,.2f} | {item['total_funding']:,.2f} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Interpretation",
            "",
            "The result is sample-optimal, not validated long-term. The stored sample spans "
            "only a few days. Futures rows are persisted 250 ms trade buckets; historical "
            "intrabucket high/low paths are unavailable, "
            "so replay uses each bucket close as its Tick price.",
            "",
        ]
    )
    return "\n".join(lines)


def _iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def _csv_numbers(value: str, converter: type[int] | type[float]) -> tuple[Any, ...]:
    return tuple(converter(item.strip()) for item in value.split(",") if item.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay persisted aggTrade data over an ATR grid")
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--instrument", action="append", dest="instruments")
    parser.add_argument("--periods", default=",".join(str(value) for value in DEFAULT_PERIODS))
    parser.add_argument(
        "--multipliers", default=",".join(str(value) for value in DEFAULT_MULTIPLIERS)
    )
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--start-ms", type=int)
    parser.add_argument("--end-ms", type=int)
    args = parser.parse_args()

    settings = load_settings(args.config)
    selected_ids = set(args.instruments or [item.id for item in settings.instruments])
    instruments = [item for item in settings.instruments if item.id in selected_ids]
    missing = selected_ids - {item.id for item in instruments}
    if missing:
        raise ValueError(f"unknown instruments: {', '.join(sorted(missing))}")
    periods = _csv_numbers(args.periods, int)
    multipliers = _csv_numbers(args.multipliers, float)
    parameters = [
        ReplayParameters(period, multiplier)
        for period in periods
        for multiplier in multipliers
    ]

    runs = []
    for instrument in instruments:
        print(f"Replaying {instrument.id}: {len(parameters)} ATR combinations...", flush=True)
        metadata, results = run_parameter_grid(
            settings,
            instrument,
            parameters,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
        )
        recommendation = select_recommendation(results)
        runs.append(
            {
                "metadata": metadata,
                "recommendation": asdict(recommendation),
                "results": [asdict(item) for item in results],
            }
        )
        print(
            f"  best ATR({recommendation.atr_period}) x {recommendation.atr_multiplier:.2f}: "
            f"{recommendation.net_return:.2%}, {recommendation.completed_trades} trades",
            flush=True,
        )

    generated_at = datetime.now(UTC).isoformat()
    payload = {"generated_at": generated_at, "runs": runs}
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"atr_tick_grid_{stamp}.json"
    markdown_path = output_dir / f"atr_tick_grid_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    markdown_path.write_text(build_report(payload))
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
