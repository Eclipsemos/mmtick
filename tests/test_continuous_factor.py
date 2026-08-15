from decimal import Decimal

import pytest

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.continuous_factor import (
    FEATURE_NAMES,
    ContinuousSignalCandidate,
    cross_asset_features,
    forward_open_returns,
    managed_targets,
)


def _bars(count: int, offset: str = "0") -> list[ResearchBar]:
    adjustment = Decimal(offset)
    return [
        ResearchBar(
            start_ms=index * 240 * 60_000,
            end_ms=(index + 1) * 240 * 60_000 - 1,
            open=Decimal("100") + Decimal(index) + adjustment,
            high=Decimal("102") + Decimal(index) + adjustment,
            low=Decimal("99") + Decimal(index) + adjustment,
            close=Decimal("101") + Decimal(index) + adjustment,
            volume=Decimal("1000") + Decimal(index * 3),
        )
        for index in range(count)
    ]


def test_cross_asset_features_are_causal_when_future_bars_are_appended() -> None:
    np = pytest.importorskip("numpy")
    original = cross_asset_features(
        np, _bars(60), _bars(60, "20"), [[] for _ in range(60)], [[] for _ in range(60)]
    )
    extended = cross_asset_features(
        np, _bars(70), _bars(70, "20"), [[] for _ in range(70)], [[] for _ in range(70)]
    )

    assert original.shape == (60, len(FEATURE_NAMES))
    assert np.allclose(original, extended[:60], equal_nan=True)


def test_forward_open_return_starts_after_signal_bar() -> None:
    np = pytest.importorskip("numpy")
    bars = _bars(8)

    labels = forward_open_returns(np, bars, 2)

    assert labels[0] == pytest.approx(float(bars[3].open / bars[1].open - Decimal("1")))
    assert np.isnan(labels[-3:]).all()


def test_managed_targets_require_confirmation_and_minimum_hold() -> None:
    candidate = ContinuousSignalCandidate(3, "long_short", 0.5, 0.8, 1, 2, 2)
    original = managed_targets([None, 0.6, 0.7, 0.1, 0.1, -0.8, -0.9], candidate)
    extended = managed_targets([None, 0.6, 0.7, 0.1, 0.1, -0.8, -0.9, 1.0], candidate)

    assert original == extended[: len(original)]
    assert original == (
        None,
        Decimal("0"),
        Decimal("1"),
        Decimal("1"),
        Decimal("0"),
        Decimal("0"),
        Decimal("-1"),
    )


def test_continuous_signal_candidate_validates_risk_controls() -> None:
    with pytest.raises(ValueError, match="exposure"):
        ContinuousSignalCandidate(3, "long_only", 0.5, 0.8, 1, 1, 1, Decimal("11"))
