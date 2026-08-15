"""Causal Binance futures market-metric features for research."""

from __future__ import annotations

import csv
import io
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from mastermind_tick.bar_research import ResearchBar

METRIC_FEATURES = (
    "oi_change_4h",
    "oi_change_24h",
    "taker_imbalance",
    "global_crowding",
    "top_account_crowding",
    "top_position_crowding",
    "top_retail_spread",
    "price_oi_interaction",
)
REQUIRED_COLUMNS = (
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)
EPSILON = Decimal("0.00000001")
CLAMP = Decimal("8")


@dataclass(frozen=True)
class FuturesMetricBar:
    start_ms: int
    end_ms: int
    open_interest: Decimal
    open_interest_value: Decimal
    top_account_ratio: Decimal
    top_position_ratio: Decimal
    global_account_ratio: Decimal
    taker_ratio: Decimal
    taker_log_mean: Decimal
    sample_count: int


def load_metric_archives(root: Path, symbol: str) -> dict[int, FuturesMetricBar]:
    """Load daily Binance metric ZIPs and reduce their 5m rows to closed 4h snapshots."""
    directory = root / symbol.upper()
    result: dict[int, FuturesMetricBar] = {}
    for archive in sorted(directory.glob(f"{symbol.upper()}-metrics-*.zip")):
        groups: dict[int, list[dict[str, str]]] = {}
        with zipfile.ZipFile(archive) as bundle:
            members = [name for name in bundle.namelist() if name.endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"expected one CSV in {archive}")
            with bundle.open(members[0]) as source:
                reader = csv.DictReader(io.TextIOWrapper(source, encoding="utf-8"))
                for row in reader:
                    if not _complete_metric_row(row):
                        continue
                    timestamp_ms = _timestamp_ms(row["create_time"])
                    bucket = timestamp_ms // 14_400_000 * 14_400_000
                    groups.setdefault(bucket, []).append(row)
        result.update(
            (start_ms, _metric_bar(start_ms, rows)) for start_ms, rows in groups.items()
        )
    return dict(sorted(result.items()))


def causal_metric_features(
    bars: list[ResearchBar],
    metrics: dict[int, FuturesMetricBar],
    *,
    normalization_window: int,
) -> dict[str, tuple[Decimal | None, ...]]:
    """Build trailing-normalized values known at each closed 4h bar."""
    if normalization_window < 12:
        raise ValueError("metric normalization window must be at least 12 bars")
    aligned = [metrics.get(bar.start_ms) for bar in bars]
    oi_4h = tuple(
        _change(current.open_interest, aligned[index - 1].open_interest)
        if current is not None and index >= 1 and aligned[index - 1] is not None
        else None
        for index, current in enumerate(aligned)
    )
    oi_24h = tuple(
        _change(current.open_interest, aligned[index - 6].open_interest)
        if current is not None and index >= 6 and aligned[index - 6] is not None
        else None
        for index, current in enumerate(aligned)
    )
    global_crowding = tuple(
        _log(metric.global_account_ratio) if metric is not None else None for metric in aligned
    )
    raw = {
        "oi_change_4h": oi_4h,
        "oi_change_24h": oi_24h,
        "taker_imbalance": tuple(
            metric.taker_log_mean if metric is not None else None for metric in aligned
        ),
        "global_crowding": global_crowding,
        "top_account_crowding": tuple(
            _log(metric.top_account_ratio) if metric is not None else None for metric in aligned
        ),
        "top_position_crowding": tuple(
            _log(metric.top_position_ratio) if metric is not None else None for metric in aligned
        ),
        "top_retail_spread": tuple(
            _log(metric.top_position_ratio / metric.global_account_ratio)
            if metric is not None and metric.global_account_ratio > 0
            else None
            for metric in aligned
        ),
        "price_oi_interaction": tuple(
            (bar.close / bar.open - Decimal("1")) * oi if oi is not None and bar.open > 0 else None
            for bar, oi in zip(bars, oi_4h, strict=True)
        ),
    }
    return {name: _causal_zscore(values, normalization_window) for name, values in raw.items()}


def metric_targets(
    values: tuple[Decimal | None, ...],
    *,
    threshold: Decimal,
    polarity: str,
    direction: str,
) -> tuple[int | None, ...]:
    if threshold < 0:
        raise ValueError("metric threshold must be non-negative")
    if polarity not in {"follow", "fade"}:
        raise ValueError(f"unsupported metric polarity: {polarity}")
    if direction not in {"long_only", "long_short"}:
        raise ValueError(f"unsupported metric direction: {direction}")
    multiplier = 1 if polarity == "follow" else -1
    targets: list[int | None] = []
    for value in values:
        if value is None:
            targets.append(None)
            continue
        signal = multiplier if value > threshold else -multiplier if value < -threshold else 0
        targets.append(signal if direction == "long_short" or signal > 0 else 0)
    return tuple(targets)


def _metric_bar(start_ms: int, rows: list[dict[str, str]]) -> FuturesMetricBar:
    latest = rows[-1]
    taker_logs = [_log(Decimal(row["sum_taker_long_short_vol_ratio"])) for row in rows]
    return FuturesMetricBar(
        start_ms=start_ms,
        end_ms=start_ms + 14_400_000 - 1,
        open_interest=Decimal(latest["sum_open_interest"]),
        open_interest_value=Decimal(latest["sum_open_interest_value"]),
        top_account_ratio=Decimal(latest["count_toptrader_long_short_ratio"]),
        top_position_ratio=Decimal(latest["sum_toptrader_long_short_ratio"]),
        global_account_ratio=Decimal(latest["count_long_short_ratio"]),
        taker_ratio=Decimal(latest["sum_taker_long_short_vol_ratio"]),
        taker_log_mean=sum(taker_logs, Decimal("0")) / Decimal(len(taker_logs)),
        sample_count=len(rows),
    )


def _timestamp_ms(value: str) -> int:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _complete_metric_row(row: dict[str, str]) -> bool:
    raw = tuple(row.get(name) for name in REQUIRED_COLUMNS)
    if any(not value for value in raw):
        return False
    try:
        values = tuple(Decimal(value) for value in raw if value is not None)
    except InvalidOperation:
        return False
    return all(value > 0 for value in values)


def _change(current: Decimal, previous: Decimal) -> Decimal | None:
    return current / previous - Decimal("1") if previous > 0 else None


def _log(value: Decimal) -> Decimal:
    return value.ln() if value > 0 else Decimal("0")


def _causal_zscore(values: tuple[Decimal | None, ...], window: int) -> tuple[Decimal | None, ...]:
    result: list[Decimal | None] = []
    sample: deque[Decimal | None] = deque()
    total = Decimal("0")
    total_squared = Decimal("0")
    missing = 0
    for value in values:
        sample.append(value)
        if value is None:
            missing += 1
        else:
            total += value
            total_squared += value * value
        if len(sample) > window:
            expired = sample.popleft()
            if expired is None:
                missing -= 1
            else:
                total -= expired
                total_squared -= expired * expired
        if len(sample) < window or missing or value is None:
            result.append(None)
            continue
        mean = total / Decimal(window)
        variance = max(Decimal("0"), total_squared / Decimal(window) - mean * mean)
        deviation = variance.sqrt()
        score = Decimal("0") if deviation <= EPSILON else (value - mean) / deviation
        result.append(max(-CLAMP, min(CLAMP, score)))
    return tuple(result)
