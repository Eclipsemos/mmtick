from decimal import Decimal

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.event_meta_factor import (
    FEATURE_NAMES,
    build_event_samples,
    filtered_event_targets,
)


def _bars(count: int, scale: str = "1") -> list[ResearchBar]:
    multiplier = Decimal(scale)
    return [
        ResearchBar(
            start_ms=index * 240 * 60_000,
            end_ms=(index + 1) * 240 * 60_000 - 1,
            open=(Decimal("100") + Decimal(index) / Decimal("10")) * multiplier,
            high=(Decimal("101") + Decimal(index) / Decimal("10")) * multiplier,
            low=(Decimal("99") + Decimal(index) / Decimal("10")) * multiplier,
            close=(Decimal("100.5") + Decimal(index) / Decimal("10")) * multiplier,
            volume=Decimal("1000") + Decimal(index),
        )
        for index in range(count)
    ]


def test_event_samples_use_entry_features_and_realized_exit_label() -> None:
    count = 140
    targets = tuple(1 if 100 <= index < 112 else 0 for index in range(count))
    samples = build_event_samples(
        _bars(count),
        _bars(count, "0.8"),
        tuple(Decimal("2") for _index in range(count)),
        tuple(Decimal("0.5") for _index in range(count)),
        targets,
        [[] for _index in range(count)],
        hold_bars=12,
    )

    assert len(samples) == 1
    assert samples[0].index == 100
    assert samples[0].direction == 1
    assert len(samples[0].features) == len(FEATURE_NAMES)
    assert samples[0].net_return > 0


def test_event_features_do_not_change_when_future_bars_are_appended() -> None:
    targets = tuple(1 if 100 <= index < 112 else 0 for index in range(140))
    original = build_event_samples(
        _bars(140),
        _bars(140, "0.8"),
        tuple(Decimal("2") for _index in range(140)),
        tuple(Decimal("0.5") for _index in range(140)),
        targets,
        [[] for _index in range(140)],
        hold_bars=12,
    )
    extended_targets = (*targets, *(0 for _index in range(10)))
    extended = build_event_samples(
        _bars(150),
        _bars(150, "0.8"),
        tuple(Decimal("2") for _index in range(150)),
        tuple(Decimal("0.5") for _index in range(150)),
        extended_targets,
        [[] for _index in range(150)],
        hold_bars=12,
    )

    assert extended[0] == original[0]


def test_filtered_targets_keep_or_block_complete_events() -> None:
    count = 140
    base_targets = tuple(
        1 if 60 <= index < 72 else -1 if 100 <= index < 112 else 0 for index in range(count)
    )
    samples = build_event_samples(
        _bars(count),
        _bars(count, "0.8"),
        tuple(Decimal("2") for _index in range(count)),
        tuple(Decimal("0.5") for _index in range(count)),
        base_targets,
        [[] for _index in range(count)],
        hold_bars=12,
    )

    filtered = filtered_event_targets(
        base_targets,
        samples,
        (Decimal("0.8"), Decimal("0.2")),
        probability_threshold=Decimal("0.5"),
        exposure=Decimal("2"),
    )

    assert filtered[60:72] == (Decimal("2"),) * 12
    assert filtered[100:112] == (Decimal("0"),) * 12
