"""Causal BTC order-flow factors for research-only closed-bar evaluation."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import ResearchBar

FlowProgress = Callable[[str, float], None]


@dataclass(frozen=True)
class OrderFlowBar:
    start_ms: int
    bucket_count: int
    total_notional: Decimal
    buy_notional: Decimal
    sell_notional: Decimal
    unknown_notional: Decimal
    tick_rule_buy_notional: Decimal = Decimal("0")
    tick_rule_sell_notional: Decimal = Decimal("0")

    @property
    def reported_notional(self) -> Decimal:
        """Notional with Binance's aggressor-side field present."""
        return self.buy_notional + self.sell_notional

    @property
    def reported_imbalance(self) -> Decimal | None:
        return (
            (self.buy_notional - self.sell_notional) / self.reported_notional
            if self.reported_notional
            else None
        )

    @property
    def tick_rule_notional(self) -> Decimal:
        """Notional classified from each archived bucket's close versus open price."""
        return self.tick_rule_buy_notional + self.tick_rule_sell_notional

    @property
    def tick_rule_imbalance(self) -> Decimal | None:
        return (
            (self.tick_rule_buy_notional - self.tick_rule_sell_notional) / self.tick_rule_notional
            if self.tick_rule_notional
            else None
        )


@dataclass(frozen=True)
class FlowCandidate:
    feature: str
    window: int
    direction: str
    threshold: Decimal
    smoothing_bars: int
    minimum_hold_bars: int
    cooldown_bars: int
    confirmation_bars: int

    @property
    def id(self) -> str:
        threshold = f"{self.threshold:g}".replace(".", "p")
        return (
            f"{self.feature}-window-{self.window}-{self.direction}-threshold-{threshold}"
            f"-ema-{self.smoothing_bars}-hold-{self.minimum_hold_bars}"
            f"-cooldown-{self.cooldown_bars}-confirm-{self.confirmation_bars}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, **asdict(self), "threshold": float(self.threshold)}


def load_order_flow(
    database: Path,
    *,
    instrument_id: str,
    interval_minutes: int,
    periods: tuple[tuple[int, int], ...],
    callback: FlowProgress | None = None,
) -> dict[int, OrderFlowBar]:
    """Aggregate archived trade buckets once per requested period using a read-only connection."""
    interval_ms = interval_minutes * 60_000
    uri = f"file:{database.resolve()}?mode=ro"
    result: dict[int, OrderFlowBar] = {}
    chunks = _monthly_chunks(periods)
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for index, (start_ms, end_ms) in enumerate(chunks, start=1):
            rows = connection.execute(
                """
                SELECT
                    timestamp_ms / ? * ? AS start_ms,
                    COUNT(*) AS bucket_count,
                    SUM(CAST(notional AS REAL)) AS total_notional,
                    SUM(CASE WHEN buyer_is_maker = 0 THEN CAST(notional AS REAL) ELSE 0 END)
                        AS buy_notional,
                    SUM(CASE WHEN buyer_is_maker = 1 THEN CAST(notional AS REAL) ELSE 0 END)
                        AS sell_notional,
                    SUM(CASE WHEN buyer_is_maker IS NULL THEN CAST(notional AS REAL) ELSE 0 END)
                        AS unknown_notional,
                    SUM(
                        CASE
                            WHEN CAST(price AS REAL) > CAST(open_price AS REAL)
                            THEN CAST(notional AS REAL)
                            ELSE 0
                        END
                    ) AS tick_rule_buy_notional,
                    SUM(
                        CASE
                            WHEN CAST(price AS REAL) < CAST(open_price AS REAL)
                            THEN CAST(notional AS REAL)
                            ELSE 0
                        END
                    ) AS tick_rule_sell_notional
                FROM agg_trades
                WHERE instrument_id = ? AND timestamp_ms >= ? AND timestamp_ms <= ?
                GROUP BY start_ms
                ORDER BY start_ms
                """,
                (interval_ms, interval_ms, instrument_id, start_ms, end_ms),
            )
            for row in rows:
                result[int(row["start_ms"])] = OrderFlowBar(
                    start_ms=int(row["start_ms"]),
                    bucket_count=int(row["bucket_count"]),
                    total_notional=Decimal(str(row["total_notional"] or 0)),
                    buy_notional=Decimal(str(row["buy_notional"] or 0)),
                    sell_notional=Decimal(str(row["sell_notional"] or 0)),
                    unknown_notional=Decimal(str(row["unknown_notional"] or 0)),
                    tick_rule_buy_notional=Decimal(str(row["tick_rule_buy_notional"] or 0)),
                    tick_rule_sell_notional=Decimal(str(row["tick_rule_sell_notional"] or 0)),
                )
            if callback is not None:
                callback(f"聚合订单流月份 {index}/{len(chunks)}", index / len(chunks))
    return result


def causal_flow_features(
    bars: list[ResearchBar],
    flow_by_start: dict[int, OrderFlowBar],
    window: int,
) -> dict[str, tuple[Decimal | None, ...]]:
    """Build normalized factors using only observations available through each bar close."""
    if window < 8:
        raise ValueError("order-flow feature window must be at least eight bars")
    reported_imbalances: list[Decimal | None] = []
    tick_rule_imbalances: list[Decimal | None] = []
    notionals: list[Decimal | None] = []
    bucket_counts: list[Decimal | None] = []
    returns: list[Decimal | None] = []
    for index, bar in enumerate(bars):
        flow = flow_by_start.get(bar.start_ms)
        reported_imbalances.append(flow.reported_imbalance if flow is not None else None)
        tick_rule_imbalances.append(flow.tick_rule_imbalance if flow is not None else None)
        notionals.append(flow.total_notional if flow is not None else None)
        bucket_counts.append(Decimal(flow.bucket_count) if flow is not None else None)
        returns.append(bar.close / bars[index - 1].close - Decimal("1") if index else None)
    reported_imbalance_z = _causal_zscore(reported_imbalances, window)
    tick_rule_imbalance_z = _causal_zscore(tick_rule_imbalances, window)
    notional_z = _causal_zscore(notionals, window)
    bucket_z = _causal_zscore(bucket_counts, window)
    return_z = _causal_zscore(returns, window)
    return {
        "reported_imbalance_follow": reported_imbalance_z,
        "reported_imbalance_revert": _negate(reported_imbalance_z),
        "reported_price_confirm": _combine(reported_imbalance_z, return_z, Decimal("1")),
        "reported_absorption": _combine(return_z, reported_imbalance_z, Decimal("-1")),
        "reported_active_pressure": _pressure(reported_imbalance_z, notional_z, bucket_z),
        "tick_rule_imbalance_follow": tick_rule_imbalance_z,
        "tick_rule_imbalance_revert": _negate(tick_rule_imbalance_z),
        "tick_rule_price_confirm": _combine(tick_rule_imbalance_z, return_z, Decimal("1")),
        "tick_rule_absorption": _combine(return_z, tick_rule_imbalance_z, Decimal("-1")),
        "tick_rule_active_pressure": _pressure(tick_rule_imbalance_z, notional_z, bucket_z),
    }


def flow_targets(
    scores: tuple[Decimal | None, ...], candidate: FlowCandidate
) -> tuple[int | None, ...]:
    if candidate.direction not in {"long_only", "long_short"}:
        raise ValueError("order-flow direction must be long_only or long_short")
    if candidate.threshold <= 0:
        raise ValueError("order-flow threshold must be positive")
    alpha = (
        Decimal("1")
        if candidate.smoothing_bars <= 1
        else Decimal("2") / Decimal(candidate.smoothing_bars + 1)
    )
    targets: list[int | None] = []
    state = hold_count = cooldown = pending = pending_count = 0
    ema: Decimal | None = None
    for score in scores:
        if score is None:
            targets.append(None)
            continue
        ema = score if ema is None else ema + alpha * (score - ema)
        desired = (
            1
            if ema >= candidate.threshold
            else -1
            if candidate.direction == "long_short" and ema <= -candidate.threshold
            else 0
        )
        if state:
            hold_count += 1
            if desired == state:
                pending = pending_count = 0
            elif hold_count >= candidate.minimum_hold_bars:
                state = hold_count = 0
                cooldown = candidate.cooldown_bars
                pending = pending_count = 0
        if not state:
            if cooldown:
                cooldown -= 1
            elif desired:
                if desired == pending:
                    pending_count += 1
                else:
                    pending, pending_count = desired, 1
                if pending_count >= candidate.confirmation_bars:
                    state = desired
                    hold_count = pending = pending_count = 0
        targets.append(state)
    return tuple(targets)


def candidate_library() -> tuple[FlowCandidate, ...]:
    return tuple(
        FlowCandidate(
            feature, window, direction, threshold, smoothing, hold, cooldown, confirmation
        )
        for feature in (
            "reported_imbalance_follow",
            "reported_imbalance_revert",
            "reported_price_confirm",
            "reported_absorption",
            "reported_active_pressure",
            "tick_rule_imbalance_follow",
            "tick_rule_imbalance_revert",
            "tick_rule_price_confirm",
            "tick_rule_absorption",
            "tick_rule_active_pressure",
        )
        for window in (42, 126)
        for direction in ("long_only", "long_short")
        for threshold in (Decimal("0.75"), Decimal("1.25"))
        for smoothing in (1, 4)
        for hold in (1, 6)
        for cooldown in (0, 6)
        for confirmation in (1, 2)
    )


def _causal_zscore(values: list[Decimal | None], window: int) -> tuple[Decimal | None, ...]:
    result: list[Decimal | None] = []
    for index, value in enumerate(values):
        sample = [item for item in values[max(0, index - window) : index] if item is not None]
        if value is None or len(sample) < window:
            result.append(None)
            continue
        mean = sum(sample, Decimal("0")) / Decimal(len(sample))
        variance = sum((item - mean) ** 2 for item in sample) / Decimal(len(sample))
        result.append((value - mean) / variance.sqrt() if variance else Decimal("0"))
    return tuple(result)


def _negate(values: tuple[Decimal | None, ...]) -> tuple[Decimal | None, ...]:
    return tuple(-value if value is not None else None for value in values)


def _combine(
    left: tuple[Decimal | None, ...],
    right: tuple[Decimal | None, ...],
    right_multiplier: Decimal,
) -> tuple[Decimal | None, ...]:
    return tuple(
        first + right_multiplier * second if first is not None and second is not None else None
        for first, second in zip(left, right, strict=True)
    )


def _pressure(
    imbalance: tuple[Decimal | None, ...],
    notional: tuple[Decimal | None, ...],
    buckets: tuple[Decimal | None, ...],
) -> tuple[Decimal | None, ...]:
    return tuple(
        direction * (Decimal("1") + max(Decimal("0"), activity + frequency) / Decimal("2"))
        if direction is not None and activity is not None and frequency is not None
        else None
        for direction, activity, frequency in zip(imbalance, notional, buckets, strict=True)
    )


def _monthly_chunks(periods: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    chunks: list[tuple[int, int]] = []
    for start_ms, end_ms in periods:
        current = datetime.fromtimestamp(start_ms / 1000, UTC)
        while True:
            next_month = datetime(
                current.year + (current.month == 12),
                1 if current.month == 12 else current.month + 1,
                1,
                tzinfo=UTC,
            )
            chunk_start = max(start_ms, int(current.timestamp() * 1000))
            chunk_end = min(end_ms, int(next_month.timestamp() * 1000) - 1)
            chunks.append((chunk_start, chunk_end))
            if chunk_end >= end_ms:
                break
            current = next_month
    return tuple(chunks)
