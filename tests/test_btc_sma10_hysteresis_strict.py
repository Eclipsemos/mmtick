import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma10_three_state_hysteresis_strict import hysteresis_targets  # noqa: E402

from mastermind_tick.bar_research import ResearchBar  # noqa: E402


def bars_from_closes(closes):
    return [
        ResearchBar(
            start_ms=index * 86_400_000,
            end_ms=(index + 1) * 86_400_000 - 1,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("1"),
        )
        for index, close in enumerate(closes)
    ]


def test_sma10_hysteresis_waits_two_bear_days():
    targets = hysteresis_targets(bars_from_closes([100] * 40 + [90, 90, 90]))

    assert targets[40] == Decimal("1.5")
    assert targets[41] == Decimal("0")


def test_sma10_hysteresis_recovers_after_one_non_bear_day():
    targets = hysteresis_targets(bars_from_closes([100] * 40 + [90, 90, 110]))

    assert targets[41] == Decimal("0")
    assert targets[42] == Decimal("1.5")
