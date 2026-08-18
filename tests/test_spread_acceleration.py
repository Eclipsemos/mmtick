import importlib.util
import sys
from pathlib import Path

from mastermind_tick.volatility_spread import SpreadFeatures, SpreadParameters


def _module():
    path = (
        Path(__file__).parents[1] / "scripts" / "research" / "explore_soxl_spread_acceleration.py"
    )
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("mmtick_spread_acceleration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_acceleration_gate_uses_current_and_prior_closed_ratio_only() -> None:
    module = _module()
    features = SpreadFeatures(
        ratios=(None, 1.0, 1.2),
        slow_ranges=(None, None, None),
        prior_highs=(None, None, None),
        prior_lows=(None, None, None),
        compression_seen=(False, False, False),
        volume_ratios=(None, None, None),
        prior_means=(None, None, None),
    )
    parameters = SpreadParameters(
        variant="compression_release",
        direction="long_short",
        fast_window=2,
        slow_window=4,
        entry_ratio=1.1,
        exit_ratio=0.8,
        breakout_window=2,
        stop_atr=2.5,
        max_hold_bars=12,
    )

    result = module.acceleration_filter(
        features, parameters, {"mode": "delta", "minimum": 0.1, "name": "delta_0.1"}
    )

    assert result == (0, 0, None)
