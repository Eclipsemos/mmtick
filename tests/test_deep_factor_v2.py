from decimal import Decimal

import pytest

from mastermind_tick.deep_factor_v2 import (
    DeepFactorV2Config,
    V2SignalCandidate,
    _managed_targets,
    _rolling_mean,
    _select_portfolio,
    _signal_candidates,
)


def test_v2_defaults_use_cross_asset_four_hour_windows() -> None:
    config = DeepFactorV2Config()

    assert config.bar_interval_minutes == 240
    assert config.horizons == (1, 6, 18)
    assert config.ensemble_seeds == (11, 23, 42)
    assert config.market_metrics_dir == "data/futures_metrics"
    assert config.metric_normalization_window == 540


def test_v2_signal_management_is_causal() -> None:
    candidate = V2SignalCandidate("long_short", 6, 0.5, 1, 2, 2, 2)
    original = _managed_targets([None, 0.6, 0.7, 0.1, 0.1, -0.8], candidate)
    extended = _managed_targets([None, 0.6, 0.7, 0.1, 0.1, -0.8, 0.9], candidate)

    assert original == extended[: len(original)]
    assert original == (None, 0, 1, 1, 0, 0)


def test_v2_candidate_ids_include_risk_controls() -> None:
    candidate = V2SignalCandidate(
        "long_only", 18, 0.75, 4, 6, 4, 2, Decimal("2.5"), Decimal("0.10")
    )

    assert "exp2p5" in candidate.id
    assert "loss0p1" in candidate.id
    assert candidate.as_dict()["exposure"] == 2.5


def test_v2_candidate_library_is_deterministic() -> None:
    candidates = _signal_candidates((1, 6, 18))

    assert len(candidates) == 288
    assert len({candidate.id for candidate in candidates}) == len(candidates)


def test_v2_rolling_mean_does_not_use_future_values() -> None:
    np = pytest.importorskip("numpy")

    original = _rolling_mean(np, np.array([1.0, 2.0, 3.0]), 2)
    extended = _rolling_mean(np, np.array([1.0, 2.0, 3.0, 100.0]), 2)

    assert np.allclose(original, extended[:3], equal_nan=True)


def test_v2_rejects_non_positive_exposure_at_replay_boundary() -> None:
    with pytest.raises(ValueError):
        V2SignalCandidate("long_only", 1, 0.5, 1, 1, 0, 1, Decimal("0"))


def test_v2_portfolio_stops_when_no_component_passes_development_gates() -> None:
    searches = {
        "btc_perp": {"risk_eligible_count": 0},
        "eth_perp": {"risk_eligible_count": 0},
    }

    result = _select_portfolio({}, {}, searches)

    assert result["selection_status"] == "no_valid_components"
    assert result["selected"] is None
    assert result["confirmation"] is None
    assert not result["used_fallback_diagnostic"]
