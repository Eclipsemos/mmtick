"""Frozen, read-only forward evaluation for volatility-spread candidates."""

from __future__ import annotations

import bisect
import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.models import FundingRate
from mastermind_tick.volatility_spread import (
    SpreadBar,
    SpreadExecution,
    SpreadParameters,
    build_spread_features,
    daily_path_metrics,
    evaluate_spread,
)

DAY_MS = 86_400_000
BAR_MS = 15 * 60_000
BARS_PER_DAY = DAY_MS // BAR_MS


@dataclass(frozen=True)
class FrozenSpreadCandidate:
    id: str
    instrument_id: str
    symbol: str
    status: str
    approved_for_trading: bool
    evidence_lock_date: date
    forward_evidence_start_date: date
    continuous_replay_start_ms: int
    source_report: str
    parameters: SpreadParameters
    bar_interval_minutes: int
    fee_bps_per_fill: Decimal
    slippage_bps_per_fill: Decimal
    quantity_step: Decimal
    initial_equity: Decimal
    funding_included: bool
    minimum_complete_days_for_interim_review: int
    minimum_completed_trades_for_interim_review: int
    minimum_complete_days_for_approval_review: int
    minimum_completed_trades_for_approval_review: int

    @property
    def parameter_hash(self) -> str:
        encoded = json.dumps(
            asdict(self.parameters), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


def load_frozen_candidate(path: Path) -> FrozenSpreadCandidate:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported frozen spread candidate schema")
    if payload.get("strategy_family") != "volatility_spread":
        raise ValueError("candidate is not a volatility-spread strategy")
    execution = payload["execution"]
    gates = payload["forward_gates"]
    lock_date = date.fromisoformat(payload["evidence_lock_date"])
    forward_start = date.fromisoformat(payload["forward_evidence_start_date"])
    if forward_start != lock_date + timedelta(days=1):
        raise ValueError("forward evidence must begin on the day after the evidence lock")
    parameters = SpreadParameters(**payload["parameters"])
    parameters.validate()
    candidate = FrozenSpreadCandidate(
        id=payload["id"],
        instrument_id=payload["instrument_id"],
        symbol=payload["symbol"],
        status=payload["status"],
        approved_for_trading=bool(payload["approved_for_trading"]),
        evidence_lock_date=lock_date,
        forward_evidence_start_date=forward_start,
        continuous_replay_start_ms=int(payload["continuous_replay_start_ms"]),
        source_report=payload["source_report"],
        parameters=parameters,
        bar_interval_minutes=int(execution["bar_interval_minutes"]),
        fee_bps_per_fill=Decimal(str(execution["fee_bps_per_fill"])),
        slippage_bps_per_fill=Decimal(str(execution["slippage_bps_per_fill"])),
        quantity_step=Decimal(execution["quantity_step"]),
        initial_equity=Decimal(execution["initial_equity"]),
        funding_included=bool(execution["funding_included"]),
        minimum_complete_days_for_interim_review=int(
            gates["minimum_complete_days_for_interim_review"]
        ),
        minimum_completed_trades_for_interim_review=int(
            gates["minimum_completed_trades_for_interim_review"]
        ),
        minimum_complete_days_for_approval_review=int(
            gates["minimum_complete_days_for_approval_review"]
        ),
        minimum_completed_trades_for_approval_review=int(
            gates["minimum_completed_trades_for_approval_review"]
        ),
    )
    if candidate.approved_for_trading:
        raise ValueError("forward evaluator only accepts unapproved research candidates")
    if candidate.bar_interval_minutes != 15:
        raise ValueError("volatility-spread forward replay currently requires 15m bars")
    if not candidate.funding_included:
        raise ValueError("frozen SOXL forward evaluation must include funding")
    return candidate


def load_forward_market(
    database: Path,
    candidate: FrozenSpreadCandidate,
) -> tuple[list[SpreadBar], list[list[FundingRate]], list[SpreadExecution | None]]:
    """Load market inputs through a read-only SQLite connection."""
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        bars = [
            SpreadBar(
                start_ms=int(row["start_ms"]),
                end_ms=int(row["end_ms"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
            )
            for row in connection.execute(
                """
                SELECT start_ms, end_ms, open, high, low, close, volume
                FROM ohlcv_bars
                WHERE instrument_id = ? AND interval_minutes = ? AND is_closed = 1
                ORDER BY start_ms
                """,
                (candidate.instrument_id, candidate.bar_interval_minutes),
            )
        ]
        funding = [
            FundingRate(
                timestamp_ms=int(row["timestamp_ms"]),
                rate=Decimal(row["rate"]),
                mark_price=Decimal(row["mark_price"]),
            )
            for row in connection.execute(
                """
                SELECT timestamp_ms, rate, mark_price
                FROM funding_rates WHERE instrument_id = ? ORDER BY timestamp_ms
                """,
                (candidate.instrument_id,),
            )
        ]
        execution_rows = connection.execute(
            """
            SELECT bar.start_ms, trade.timestamp_ms, trade.price
            FROM ohlcv_bars AS bar
            LEFT JOIN agg_trades AS trade ON trade.rowid = (
                SELECT candidate_tick.rowid
                FROM agg_trades AS candidate_tick
                WHERE candidate_tick.instrument_id = ?
                  AND candidate_tick.timestamp_ms >= bar.start_ms
                  AND candidate_tick.timestamp_ms <= bar.end_ms
                ORDER BY candidate_tick.timestamp_ms
                LIMIT 1
            )
            WHERE bar.instrument_id = ?
              AND bar.interval_minutes = ?
              AND bar.is_closed = 1
            ORDER BY bar.start_ms
            """,
            (
                candidate.instrument_id,
                candidate.instrument_id,
                candidate.bar_interval_minutes,
            ),
        ).fetchall()
    if not bars:
        raise ValueError(f"no closed bars for {candidate.instrument_id}")
    if len(execution_rows) != len(bars):
        raise ValueError("execution Tick rows do not align with closed bars")
    bar_ends = [bar.end_ms for bar in bars]
    funding_by_bar: list[list[FundingRate]] = [[] for _ in bars]
    for event in funding:
        index = bisect.bisect_left(bar_ends, event.timestamp_ms)
        if index < len(bars):
            funding_by_bar[index].append(event)
    executions = [
        (
            SpreadExecution(timestamp_ms=int(row["timestamp_ms"]), price=Decimal(row["price"]))
            if row["timestamp_ms"] is not None
            else None
        )
        for row in execution_rows
    ]
    return bars, funding_by_bar, executions


def complete_utc_days(
    bars: list[SpreadBar], executions: list[SpreadExecution | None]
) -> tuple[date, ...]:
    if len(bars) != len(executions):
        raise ValueError("bar and execution lengths differ")
    grouped: dict[date, list[tuple[SpreadBar, SpreadExecution | None]]] = {}
    for bar, execution in zip(bars, executions, strict=True):
        day = datetime.fromtimestamp(bar.start_ms / 1000, UTC).date()
        grouped.setdefault(day, []).append((bar, execution))
    complete = []
    for day, rows in sorted(grouped.items()):
        start_ms = _day_start_ms(day)
        expected_starts = list(range(start_ms, start_ms + DAY_MS, BAR_MS))
        actual_starts = [bar.start_ms for bar, _ in rows]
        all_ticks_present = all(
            execution is not None and bar.start_ms <= execution.timestamp_ms <= bar.end_ms
            for bar, execution in rows
        )
        closes_at_day_end = bool(rows) and rows[-1][0].end_ms == start_ms + DAY_MS - 1
        if (
            len(rows) == BARS_PER_DAY
            and actual_starts == expected_starts
            and all_ticks_present
            and closes_at_day_end
        ):
            complete.append(day)
    return tuple(complete)


def evaluate_frozen_forward(
    candidate: FrozenSpreadCandidate,
    bars: list[SpreadBar],
    funding_by_bar: list[list[FundingRate]],
    executions: list[SpreadExecution | None],
    *,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    if as_of_date is not None and as_of_date <= candidate.evidence_lock_date:
        raise ValueError(
            f"forward evidence must be after {candidate.evidence_lock_date.isoformat()} UTC"
        )
    if len(bars) != len(funding_by_bar) or len(bars) != len(executions):
        raise ValueError("market input lengths differ")
    complete_days = complete_utc_days(bars, executions)
    available = [day for day in complete_days if day >= candidate.forward_evidence_start_date]
    requested_through = as_of_date or (available[-1] if available else None)
    eligible = [day for day in available if requested_through is None or day <= requested_through]
    contiguous: list[date] = []
    expected = candidate.forward_evidence_start_date
    for day in eligible:
        if day != expected:
            break
        contiguous.append(day)
        expected += timedelta(days=1)

    base = {
        "schema_version": 1,
        "candidate_id": candidate.id,
        "instrument_id": candidate.instrument_id,
        "symbol": candidate.symbol,
        "candidate_status": candidate.status,
        "approved_for_trading": candidate.approved_for_trading,
        "parameter_hash_sha256": candidate.parameter_hash,
        "parameters": asdict(candidate.parameters),
        "source_report": candidate.source_report,
        "evidence_lock_date": candidate.evidence_lock_date.isoformat(),
        "forward_evidence_start_date": candidate.forward_evidence_start_date.isoformat(),
        "continuous_replay_start_ms": candidate.continuous_replay_start_ms,
        "parameter_search_performed": False,
        "costs": {
            "fee_bps_per_fill": float(candidate.fee_bps_per_fill),
            "slippage_bps_per_fill": float(candidate.slippage_bps_per_fill),
            "funding_included": candidate.funding_included,
            "initial_equity": str(candidate.initial_equity),
        },
    }
    if not contiguous:
        return {
            **base,
            "status": "awaiting_data",
            "message": (
                f"No complete UTC day is available on or after "
                f"{candidate.forward_evidence_start_date.isoformat()}."
            ),
            "data_through_date": None,
            "forward": _empty_forward_metrics(),
            "gates": _forward_gates(candidate, 0, 0),
            "target": {
                "geometric_daily_return": 0.05,
                "observed_in_available_sample": False,
                "achieved": False,
                "evaluable": False,
            },
        }

    through_date = contiguous[-1]
    features = build_spread_features(
        bars,
        fast_window=candidate.parameters.fast_window,
        slow_window=candidate.parameters.slow_window,
        breakout_window=candidate.parameters.breakout_window,
        compression_ratio=candidate.parameters.compression_ratio,
        compression_lookback=candidate.parameters.compression_lookback,
        spread_measure=candidate.parameters.spread_measure,
    )
    continuous = evaluate_spread(
        bars,
        features,
        candidate.parameters,
        start_ms=candidate.continuous_replay_start_ms,
        end_ms=_day_end_ms(through_date),
        funding_by_bar=funding_by_bar,
        execution_by_bar=executions,
        initial_equity=candidate.initial_equity,
        fee_bps=candidate.fee_bps_per_fill,
        slippage_bps=candidate.slippage_bps_per_fill,
        quantity_step=candidate.quantity_step,
    )
    daily = [
        (day, value)
        for day, value in continuous.daily_returns
        if candidate.forward_evidence_start_date.isoformat() <= day <= through_date.isoformat()
    ]
    if [day for day, _ in daily] != [day.isoformat() for day in contiguous]:
        raise ValueError("continuous replay did not produce every complete forward UTC day")
    metrics = daily_path_metrics([value for _, value in daily])
    forward_trades = [
        trade
        for trade in continuous.trades
        if _day_start_ms(candidate.forward_evidence_start_date)
        <= trade.exit_at_ms
        <= _day_end_ms(through_date)
    ]
    wins = [trade.net_pnl for trade in forward_trades if trade.net_pnl > 0]
    losses = [-trade.net_pnl for trade in forward_trades if trade.net_pnl < 0]
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = sum(losses, Decimal("0"))
    forward = {
        **metrics,
        "complete_days": len(daily),
        "completed_trades": len(forward_trades),
        "win_rate": (
            sum(trade.net_pnl > 0 for trade in forward_trades) / len(forward_trades)
            if forward_trades
            else None
        ),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else None,
        "profitable_day_rate": sum(value > 0 for _, value in daily) / len(daily),
        "days_at_or_above_target": sum(value >= 0.05 for _, value in daily),
        "target_day_rate": sum(value >= 0.05 for _, value in daily) / len(daily),
        "daily_returns": [{"date": day, "return": value} for day, value in daily],
        "trades": [_trade_payload(trade) for trade in forward_trades],
    }
    gates = _forward_gates(candidate, len(daily), len(forward_trades))
    return {
        **base,
        "status": "review_ready" if gates["interim_review_ready"] else "collecting_evidence",
        "message": "Frozen parameters evaluated without search.",
        "data_through_date": through_date.isoformat(),
        "forward": forward,
        "gates": gates,
        "target": {
            "geometric_daily_return": 0.05,
            "observed_in_available_sample": metrics["geometric_daily_return"] >= 0.05,
            "achieved": (
                metrics["geometric_daily_return"] >= 0.05 and gates["approval_review_sample_ready"]
            ),
            "evaluable": True,
        },
    }


def render_forward_markdown(payload: dict[str, Any]) -> str:
    forward = payload["forward"]
    lines = [
        f"# {payload['symbol']} Frozen Volatility-Spread Forward Monitor",
        "",
        f"Candidate: `{payload['candidate_id']}`  ",
        f"Parameter hash: `{payload['parameter_hash_sha256']}`  ",
        f"Evidence lock: `{payload['evidence_lock_date']} UTC`  ",
        f"Status: **{payload['status']}**",
        "",
        payload["message"],
        "",
        "This report performs no parameter search and is not a trading approval.",
        "",
    ]
    if payload["status"] == "awaiting_data":
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "## Forward Metrics",
            "",
            (
                "| Through UTC | Days | Return | Geo/day | Daily-close DD | Trades | "
                "Win rate | >=5% days |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|---:|",
            (
                f"| {payload['data_through_date']} | {forward['complete_days']} | "
                f"{_pct(forward['net_return'])} | {_pct(forward['geometric_daily_return'])} | "
                f"{_pct(forward['max_daily_close_drawdown'])} | "
                f"{forward['completed_trades']} | {_pct(forward['win_rate'])} | "
                f"{forward['days_at_or_above_target']} |"
            ),
            "",
            "## Daily Returns",
            "",
            "| UTC date | Return |",
            "|---|---:|",
        ]
    )
    lines.extend(f"| {row['date']} | {_pct(row['return'])} |" for row in forward["daily_returns"])
    lines.extend(
        [
            "",
            "The 5% objective is achieved only when the forward geometric daily return reaches "
            "5%; a small number of isolated +5% days does not satisfy it.",
            "",
        ]
    )
    return "\n".join(lines)


def _forward_gates(
    candidate: FrozenSpreadCandidate, complete_days: int, completed_trades: int
) -> dict[str, Any]:
    interim = (
        complete_days >= candidate.minimum_complete_days_for_interim_review
        and completed_trades >= candidate.minimum_completed_trades_for_interim_review
    )
    approval = (
        complete_days >= candidate.minimum_complete_days_for_approval_review
        and completed_trades >= candidate.minimum_completed_trades_for_approval_review
    )
    return {
        "interim_review_ready": interim,
        "approval_review_sample_ready": approval,
        "minimum_complete_days_for_interim_review": (
            candidate.minimum_complete_days_for_interim_review
        ),
        "minimum_completed_trades_for_interim_review": (
            candidate.minimum_completed_trades_for_interim_review
        ),
        "minimum_complete_days_for_approval_review": (
            candidate.minimum_complete_days_for_approval_review
        ),
        "minimum_completed_trades_for_approval_review": (
            candidate.minimum_completed_trades_for_approval_review
        ),
    }


def _empty_forward_metrics() -> dict[str, Any]:
    return {
        "net_return": 0.0,
        "geometric_daily_return": 0.0,
        "max_daily_close_drawdown": 0.0,
        "complete_days": 0,
        "completed_trades": 0,
        "win_rate": None,
        "profit_factor": None,
        "profitable_day_rate": 0.0,
        "days_at_or_above_target": 0,
        "target_day_rate": 0.0,
        "daily_returns": [],
        "trades": [],
    }


def _trade_payload(trade) -> dict[str, Any]:
    return {
        "direction": trade.direction,
        "entry_at": datetime.fromtimestamp(trade.entry_at_ms / 1000, UTC).isoformat(),
        "exit_at": datetime.fromtimestamp(trade.exit_at_ms / 1000, UTC).isoformat(),
        "entry_price": str(trade.entry_price),
        "exit_price": str(trade.exit_price),
        "quantity": str(trade.quantity),
        "fees": str(trade.fees),
        "funding": str(trade.funding),
        "net_pnl": str(trade.net_pnl),
    }


def _day_start_ms(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end_ms(value: date) -> int:
    return _day_start_ms(value + timedelta(days=1)) - 1


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2%}"
