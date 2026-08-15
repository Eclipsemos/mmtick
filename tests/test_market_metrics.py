import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.market_metrics import (
    causal_metric_features,
    load_metric_archives,
    metric_targets,
    prior_utc_day_metric_signals,
)

HEADER = (
    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
    "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
)


def _write_archive(root: Path, rows: list[str]) -> None:
    directory = root / "BTCUSDT"
    directory.mkdir()
    archive = directory / "BTCUSDT-metrics-2024-01-01.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("BTCUSDT-metrics-2024-01-01.csv", HEADER + "\n".join(rows) + "\n")


def _bar(start_ms: int, close: str = "101") -> ResearchBar:
    return ResearchBar(
        start_ms=start_ms,
        end_ms=start_ms + 14_400_000 - 1,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal(close),
    )


def test_metric_archive_uses_last_snapshot_and_aggregates_taker_flow(tmp_path: Path) -> None:
    _write_archive(
        tmp_path,
        [
            "2024-01-01 00:00:00,BTCUSDT,100,1000,1,1.1,1.2,1",
            "2024-01-01 03:50:00,BTCUSDT,999,9999,9,9,9,",
            "2024-01-01 03:55:00,BTCUSDT,110,1200,1.1,1.2,1.3,4",
        ],
    )

    metrics = load_metric_archives(tmp_path, "BTCUSDT")

    assert len(metrics) == 1
    metric = metrics[1704067200000]
    assert metric.open_interest == Decimal("110")
    assert metric.sample_count == 2
    assert metric.taker_log_mean == Decimal("4").ln() / Decimal("2")


def test_metric_features_do_not_change_when_future_bars_are_appended() -> None:
    bars = [_bar(index * 14_400_000) for index in range(14)]
    from mastermind_tick.market_metrics import FuturesMetricBar

    metrics = {
        bar.start_ms: FuturesMetricBar(
            start_ms=bar.start_ms,
            end_ms=bar.end_ms,
            open_interest=Decimal(100 + index),
            open_interest_value=Decimal(1000 + index),
            top_account_ratio=Decimal("1.1"),
            top_position_ratio=Decimal("1.2"),
            global_account_ratio=Decimal("1.3"),
            taker_ratio=Decimal("1.4"),
            taker_log_mean=Decimal("1.4").ln(),
            sample_count=48,
        )
        for index, bar in enumerate(bars)
    }

    original = causal_metric_features(bars[:13], metrics, normalization_window=12)
    extended = causal_metric_features(bars, metrics, normalization_window=12)

    assert {name: values[:-1] for name, values in extended.items()} == original


def test_metric_targets_support_fade_and_long_only() -> None:
    values = (None, Decimal("2"), Decimal("-2"), Decimal("0"))

    assert metric_targets(
        values, threshold=Decimal("1"), polarity="fade", direction="long_only"
    ) == (None, 0, 1, 0)


def test_prior_utc_day_signal_uses_bar_only_after_it_closes() -> None:
    day_ms = 86_400_000
    bars = [
        _bar(day_ms - 14_400_000),
        _bar(day_ms),
    ]

    signals = prior_utc_day_metric_signals(
        bars,
        (Decimal("1.5"), Decimal("9")),
        ("1970-01-01", "1970-01-02"),
    )

    assert signals == (
        ("1970-01-01", None),
        ("1970-01-02", Decimal("1.5")),
    )


def test_prior_utc_day_signal_rejects_misaligned_values() -> None:
    with pytest.raises(ValueError, match="aligned"):
        prior_utc_day_metric_signals([_bar(0)], (), ("1970-01-01",))
