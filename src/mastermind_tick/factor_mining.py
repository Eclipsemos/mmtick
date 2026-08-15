"""Causal, research-only factor expression mining for perpetual futures."""

from __future__ import annotations

import itertools
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import (
    ResearchBar,
    ResearchResult,
    aggregate_bars,
    evaluate_targets,
    funding_by_bar,
    wilder_atr_values,
)
from mastermind_tick.models import FundingRate

EPSILON = Decimal("0.00000001")
CLAMP = Decimal("8")
BASE_FEATURES = (
    "ret_1_z",
    "ret_4_z",
    "ret_16_z",
    "trend_20_z",
    "range_z",
    "volume_z",
    "close_location_z",
    "atr_ratio_z",
    "funding_z",
)
PAIR_FEATURES = BASE_FEATURES[:6]
UNARY_OPERATORS = ("NEG", "ABS", "SIGN", "DELAY1", "DECAY3")
BINARY_OPERATORS = ("ADD", "SUB", "MUL", "DIV")
SEARCH_UNARY_OPERATORS = ("NEG", "DELAY1", "DECAY3")
SEARCH_BINARY_OPERATORS = ("ADD", "SUB", "MUL")


@dataclass(frozen=True)
class Formula:
    """A validated postfix expression over causal factor series."""

    tokens: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_formula(self.tokens)

    @property
    def id(self) -> str:
        return "-".join(token.lower() for token in self.tokens)

    @property
    def display(self) -> str:
        stack: list[str] = []
        for token in self.tokens:
            if token in BASE_FEATURES:
                stack.append(token)
            elif token in UNARY_OPERATORS:
                operand = stack.pop()
                stack.append(f"{token.lower()}({operand})")
            else:
                right = stack.pop()
                left = stack.pop()
                stack.append(f"({left} {token.lower()} {right})")
        return stack[0]


@dataclass(frozen=True)
class FactorCandidate:
    formula: Formula
    threshold: Decimal
    direction: str
    interval_minutes: int

    @property
    def id(self) -> str:
        threshold = f"{self.threshold:g}".replace(".", "p")
        return f"{self.interval_minutes}m-{self.direction}-{self.formula.id}-threshold-{threshold}"


@dataclass(frozen=True)
class FactorMiningConfig:
    instrument_id: str
    direction_options: tuple[str, ...]
    intervals: tuple[int, ...] = (60, 240)
    thresholds: tuple[Decimal, ...] = (Decimal("0"), Decimal("0.5"))
    fee_bps: Decimal = Decimal("5")
    slippage_bps: Decimal = Decimal("2")
    stress_fee_bps: Decimal = Decimal("10")
    stress_slippage_bps: Decimal = Decimal("5")


def load_market(
    database: Path,
    instrument_id: str,
) -> tuple[list[ResearchBar], list[FundingRate]]:
    """Load immutable closed 15-minute bars and funding from the research warehouse."""
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        bars = [
            ResearchBar(
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
                WHERE instrument_id = ? AND interval_minutes = 15 AND is_closed = 1
                ORDER BY start_ms
                """,
                (instrument_id,),
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
                FROM funding_rates
                WHERE instrument_id = ?
                ORDER BY timestamp_ms
                """,
                (instrument_id,),
            )
        ]
    if not bars:
        raise ValueError(f"no complete bars for {instrument_id}")
    return bars, funding


def causal_features(
    bars: list[ResearchBar], funding: list[list[FundingRate]]
) -> dict[str, tuple[Decimal | None, ...]]:
    """Build normalized factor inputs using only data available at each bar close."""
    if len(bars) != len(funding):
        raise ValueError("bars and funding must have matching lengths")
    closes = tuple(bar.close for bar in bars)
    volumes = tuple(bar.volume for bar in bars)
    atr = wilder_atr_values(bars, 14)
    raw: dict[str, tuple[Decimal | None, ...]] = {
        "ret_1_z": _return_series(closes, 1),
        "ret_4_z": _return_series(closes, 4),
        "ret_16_z": _return_series(closes, 16),
        "trend_20_z": _trend_series(closes, 20),
        "range_z": tuple((bar.high - bar.low) / bar.close if bar.close else None for bar in bars),
        "volume_z": _volume_surprise(volumes, 20),
        "close_location_z": tuple(
            (bar.close - bar.low) / (bar.high - bar.low) - Decimal("0.5")
            if bar.high > bar.low
            else Decimal("0")
            for bar in bars
        ),
        "atr_ratio_z": tuple(
            value / bar.close if value is not None and bar.close else None
            for value, bar in zip(atr, bars, strict=True)
        ),
        "funding_z": tuple(
            sum((event.rate for event in events), Decimal("0")) * Decimal("10000")
            for events in funding
        ),
    }
    return {name: _causal_zscore(values, window=32) for name, values in raw.items()}


def evaluate_formula(
    formula: Formula,
    features: dict[str, tuple[Decimal | None, ...]],
) -> tuple[Decimal | None, ...]:
    """Evaluate a postfix formula without changing its causal alignment."""
    missing = set(BASE_FEATURES) - set(features)
    if missing:
        raise ValueError(f"missing formula features: {', '.join(sorted(missing))}")
    stack: list[tuple[Decimal | None, ...]] = []
    for token in formula.tokens:
        if token in BASE_FEATURES:
            stack.append(features[token])
        elif token in UNARY_OPERATORS:
            stack.append(_apply_unary(token, stack.pop()))
        else:
            right = stack.pop()
            left = stack.pop()
            stack.append(_apply_binary(token, left, right))
    return stack[0]


def factor_targets(
    values: tuple[Decimal | None, ...],
    threshold: Decimal,
    direction: str,
) -> tuple[int | None, ...]:
    if direction not in {"long_only", "long_short"}:
        raise ValueError(f"unsupported direction: {direction}")
    if threshold < 0:
        raise ValueError("factor threshold must be non-negative")
    targets: list[int | None] = []
    for value in values:
        if value is None:
            targets.append(None)
        elif value > threshold:
            targets.append(1)
        elif direction == "long_short" and value < -threshold:
            targets.append(-1)
        else:
            targets.append(0)
    return tuple(targets)


def formula_library() -> tuple[Formula, ...]:
    """Return a compact, deterministic formula set suitable for data-snooping controls."""
    formulas = [Formula((feature,)) for feature in BASE_FEATURES]
    formulas.extend(
        Formula((feature, operator))
        for feature in BASE_FEATURES
        for operator in SEARCH_UNARY_OPERATORS
    )
    formulas.extend(
        Formula((left, right, operator))
        for left, right in itertools.combinations(PAIR_FEATURES, 2)
        for operator in SEARCH_BINARY_OPERATORS
    )
    return tuple(formulas)


def candidate_library(config: FactorMiningConfig) -> tuple[FactorCandidate, ...]:
    return tuple(
        FactorCandidate(formula, threshold, direction, interval)
        for interval in config.intervals
        for formula in formula_library()
        for threshold in config.thresholds
        for direction in config.direction_options
    )


def split_periods(instrument_id: str, data_end_ms: int) -> dict[str, tuple[int, int]]:
    """Use fixed long-history splits; constrain SOXL to an explicitly inadequate short study."""
    confirmation_end = min(data_end_ms, _day_end(date(2026, 8, 10)))
    if instrument_id in {"btc_perp", "eth_perp"}:
        return {
            "train": (_day_start(date(2024, 2, 1)), _day_end(date(2024, 12, 31))),
            "validation": (_day_start(date(2025, 1, 1)), _day_end(date(2025, 12, 31))),
            "confirmation": (_day_start(date(2026, 1, 1)), confirmation_end),
        }
    if instrument_id == "soxl_perp":
        return {
            "train": (_day_start(date(2026, 5, 17)), _day_end(date(2026, 6, 30))),
            "validation": (_day_start(date(2026, 7, 1)), _day_end(date(2026, 7, 31))),
            "confirmation": (_day_start(date(2026, 8, 1)), confirmation_end),
        }
    raise ValueError(f"unsupported factor-mining instrument: {instrument_id}")


def mine_instrument(
    bars: list[ResearchBar],
    funding_rates: list[FundingRate],
    config: FactorMiningConfig,
) -> dict[str, Any]:
    """Rank factor formulas by development splits, then separately inspect confirmation."""
    periods = split_periods(config.instrument_id, bars[-1].end_ms)
    by_interval = {interval: aggregate_bars(bars, interval) for interval in config.intervals}
    funding = {
        interval: funding_by_bar(interval_bars, funding_rates)
        for interval, interval_bars in by_interval.items()
    }
    features_by_interval = {
        interval: causal_features(interval_bars, funding[interval])
        for interval, interval_bars in by_interval.items()
    }
    candidates = candidate_library(config)
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        interval_bars = by_interval[candidate.interval_minutes]
        features = features_by_interval[candidate.interval_minutes]
        values = evaluate_formula(candidate.formula, features)
        targets = factor_targets(values, candidate.threshold, candidate.direction)
        results = {
            name: evaluate_targets(
                interval_bars,
                targets,
                start_ms=start,
                end_ms=end,
                funding=funding[candidate.interval_minutes],
                fee_bps=config.fee_bps,
                slippage_bps=config.slippage_bps,
            )
            for name, (start, end) in periods.items()
        }
        rows.append(
            {
                "candidate": candidate,
                "results": results,
                "score": _selection_score(results["train"], results["validation"]),
            }
        )
    eligible = [
        row
        for row in rows
        if row["results"]["train"].net_return > 0
        and row["results"]["validation"].net_return > 0
        and row["results"]["train"].completed_trades >= 4
        and row["results"]["validation"].completed_trades >= 4
    ]
    ranked = sorted(eligible or rows, key=lambda row: row["score"], reverse=True)
    selected = ranked[0] if ranked else None
    neighbor_rows = ranked[: min(10, len(ranked))]
    neighbor_confirmation_pass_rate = (
        sum(
            row["results"]["confirmation"].net_return > 0
            and row["results"]["confirmation"].max_drawdown >= -0.25
            for row in neighbor_rows
        )
        / len(neighbor_rows)
        if neighbor_rows
        else 0.0
    )
    selected_payload = _serialize_row(selected) if selected is not None else None
    stress: dict[str, dict[str, Any]] = {}
    gates: dict[str, bool] = {}
    if selected is not None:
        candidate = selected["candidate"]
        interval_bars = by_interval[candidate.interval_minutes]
        features = features_by_interval[candidate.interval_minutes]
        targets = factor_targets(
            evaluate_formula(candidate.formula, features), candidate.threshold, candidate.direction
        )
        stress_results = {
            name: evaluate_targets(
                interval_bars,
                targets,
                start_ms=start,
                end_ms=end,
                funding=funding[candidate.interval_minutes],
                fee_bps=config.stress_fee_bps,
                slippage_bps=config.stress_slippage_bps,
            )
            for name, (start, end) in periods.items()
        }
        stress = {name: _summary(result) for name, result in stress_results.items()}
        gates = _stability_gates(
            selected["results"], stress_results, neighbor_confirmation_pass_rate
        )

    history_days = (bars[-1].end_ms - bars[0].start_ms) / 86_400_000
    enough_history = config.instrument_id != "soxl_perp" and history_days >= 730
    stable = bool(selected is not None and enough_history and all(gates.values()))
    return {
        "instrument_id": config.instrument_id,
        "data": {
            "first_bar": _timestamp(bars[0].start_ms),
            "last_bar": _timestamp(bars[-1].end_ms),
            "source_bars_15m": len(bars),
            "funding_events": len(funding_rates),
            "history_days": history_days,
        },
        "execution": {
            "signal_timing": "closed bar",
            "fill_timing": "next bar open",
            "fee_bps_per_fill": float(config.fee_bps),
            "slippage_bps_per_fill": float(config.slippage_bps),
            "funding": "historical funding while positioned",
            "exposure": 1.0,
            "liquidation_modeled": False,
        },
        "periods": {
            name: {"start": _timestamp(start), "end": _timestamp(end)}
            for name, (start, end) in periods.items()
        },
        "formula_language": {
            "features": list(BASE_FEATURES),
            "unary_operators": list(UNARY_OPERATORS),
            "binary_operators": list(BINARY_OPERATORS),
            "causality": (
                "Every feature uses only data through its closed bar; rolling z-scores use a "
                "trailing 32-bar window and never a full-sample normalization."
            ),
        },
        "candidate_count": len(candidates),
        "development_eligible_count": len(eligible),
        "selection_rule": (
            "positive train and validation return with at least four completed trades each; "
            "rank by weaker return, combined return, then drawdown. Confirmation is excluded."
        ),
        "selected": selected_payload,
        "top_development_candidates": [_serialize_row(row) for row in ranked[:10]],
        "neighbor_confirmation_pass_rate": neighbor_confirmation_pass_rate,
        "stress": stress,
        "stability_gates": gates,
        "decision": {
            "status": (
                "research_candidate"
                if stable
                else "insufficient_history"
                if not enough_history
                else "rejected_after_confirmation"
            ),
            "approved_for_trading": False,
            "reason": (
                "SOXLUSDT has insufficient independent history for factor-mining approval."
                if not enough_history
                else "No development-selected formula passed all confirmation stability gates."
                if not stable
                else "Formula is frozen for forward observation only; trading remains disabled."
            ),
        },
    }


def _return_series(values: tuple[Decimal, ...], lookback: int) -> tuple[Decimal | None, ...]:
    return tuple(
        value / values[index - lookback] - Decimal("1") if index >= lookback else None
        for index, value in enumerate(values)
    )


def _trend_series(values: tuple[Decimal, ...], window: int) -> tuple[Decimal | None, ...]:
    return tuple(
        value / (sum(values[index - window + 1 : index + 1], Decimal("0")) / Decimal(window))
        - Decimal("1")
        if index + 1 >= window
        else None
        for index, value in enumerate(values)
    )


def _volume_surprise(values: tuple[Decimal, ...], window: int) -> tuple[Decimal | None, ...]:
    return tuple(
        value / (sum(values[index - window + 1 : index + 1], Decimal("0")) / Decimal(window))
        - Decimal("1")
        if index + 1 >= window
        else None
        for index, value in enumerate(values)
    )


def _causal_zscore(values: tuple[Decimal | None, ...], window: int) -> tuple[Decimal | None, ...]:
    result: list[Decimal | None] = []
    for index, value in enumerate(values):
        if value is None or index + 1 < window:
            result.append(None)
            continue
        sample = values[index - window + 1 : index + 1]
        if any(item is None for item in sample):
            result.append(None)
            continue
        concrete = tuple(item for item in sample if item is not None)
        mean = sum(concrete, Decimal("0")) / Decimal(window)
        variance = sum((item - mean) ** 2 for item in concrete) / Decimal(window)
        deviation = variance.sqrt()
        if deviation <= EPSILON:
            result.append(Decimal("0"))
        else:
            result.append(max(-CLAMP, min(CLAMP, (value - mean) / deviation)))
    return tuple(result)


def _apply_unary(operator: str, values: tuple[Decimal | None, ...]) -> tuple[Decimal | None, ...]:
    if operator == "NEG":
        return tuple(-value if value is not None else None for value in values)
    if operator == "ABS":
        return tuple(abs(value) if value is not None else None for value in values)
    if operator == "SIGN":
        return tuple(_sign(value) for value in values)
    if operator == "DELAY1":
        return (None, *values[:-1])
    if operator == "DECAY3":
        result: list[Decimal | None] = []
        for index, value in enumerate(values):
            if index < 2 or value is None or values[index - 1] is None or values[index - 2] is None:
                result.append(None)
            else:
                result.append(
                    value + Decimal("0.8") * values[index - 1] + Decimal("0.6") * values[index - 2]
                )
        return tuple(result)
    raise ValueError(f"unsupported unary operator: {operator}")


def _apply_binary(
    operator: str,
    left: tuple[Decimal | None, ...],
    right: tuple[Decimal | None, ...],
) -> tuple[Decimal | None, ...]:
    result: list[Decimal | None] = []
    for first, second in zip(left, right, strict=True):
        if first is None or second is None:
            result.append(None)
        elif operator == "ADD":
            result.append(_clamp(first + second))
        elif operator == "SUB":
            result.append(_clamp(first - second))
        elif operator == "MUL":
            result.append(_clamp(first * second))
        elif operator == "DIV":
            result.append(_clamp(first / second) if abs(second) > EPSILON else Decimal("0"))
        else:
            raise ValueError(f"unsupported binary operator: {operator}")
    return tuple(result)


def _clamp(value: Decimal) -> Decimal:
    return max(-CLAMP, min(CLAMP, value))


def _sign(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if value > 0:
        return Decimal("1")
    if value < 0:
        return Decimal("-1")
    return Decimal("0")


def _validate_formula(tokens: tuple[str, ...]) -> None:
    if not tokens:
        raise ValueError("formula cannot be empty")
    depth = 0
    for token in tokens:
        if token in BASE_FEATURES:
            depth += 1
        elif token in UNARY_OPERATORS:
            if depth < 1:
                raise ValueError(f"{token} requires one operand")
        elif token in BINARY_OPERATORS:
            if depth < 2:
                raise ValueError(f"{token} requires two operands")
            depth -= 1
        else:
            raise ValueError(f"unknown formula token: {token}")
    if depth != 1:
        raise ValueError("formula must leave exactly one value on the stack")


def _selection_score(
    train: ResearchResult, validation: ResearchResult
) -> tuple[float, float, float]:
    return (
        min(train.net_return, validation.net_return),
        train.net_return + validation.net_return,
        min(train.max_drawdown, validation.max_drawdown),
    )


def _stability_gates(
    results: dict[str, ResearchResult],
    stress: dict[str, ResearchResult],
    neighbor_confirmation_pass_rate: float,
) -> dict[str, bool]:
    confirmation = results["confirmation"]
    positive_month_rate = sum(value > 0 for _label, value in confirmation.monthly_returns) / len(
        confirmation.monthly_returns
    )
    return {
        "all_splits_positive": all(result.net_return > 0 for result in results.values()),
        "drawdown_controlled": all(result.max_drawdown >= -0.25 for result in results.values()),
        "confirmation_trades": confirmation.completed_trades >= 6,
        "confirmation_months": positive_month_rate >= 0.55,
        "parameter_neighborhood": neighbor_confirmation_pass_rate >= 0.60,
        "cost_stress": all(
            result.net_return > 0 and result.max_drawdown >= -0.25 for result in stress.values()
        ),
    }


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    candidate = row["candidate"]
    return {
        "id": candidate.id,
        "formula": {"tokens": list(candidate.formula.tokens), "display": candidate.formula.display},
        "threshold": float(candidate.threshold),
        "direction": candidate.direction,
        "interval_minutes": candidate.interval_minutes,
        "selection_score": list(row["score"]),
        **{name: _summary(result) for name, result in row["results"].items()},
    }


def _summary(result: ResearchResult) -> dict[str, Any]:
    payload = asdict(result)
    payload.pop("trades")
    payload.pop("daily_returns")
    payload["monthly_returns"] = [
        {"month": label, "return": value} for label, value in result.monthly_returns
    ]
    payload["positive_month_rate"] = sum(
        value > 0 for _label, value in result.monthly_returns
    ) / len(result.monthly_returns)
    return payload


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()
