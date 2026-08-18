import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

from mastermind_tick.volatility_spread import SpreadBar, SpreadFeatures


def _module():
    path = (
        Path(__file__).parents[1]
        / "scripts"
        / "research"
        / "explore_soxl_cross_asset_spread_filter.py"
    )
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("mmtick_cross_asset_filter", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bar(index: int) -> SpreadBar:
    return SpreadBar(
        start_ms=index * 900_000,
        end_ms=(index + 1) * 900_000 - 1,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
    )


def test_cross_asset_filter_uses_only_time_aligned_btc_bar() -> None:
    module = _module()
    btc_bars = [_bar(0), _bar(1)]
    soxl_bars = [_bar(0), _bar(1), _bar(2)]
    features = SpreadFeatures(
        ratios=(0.5, 1.5),
        slow_ranges=(None, None),
        prior_highs=(None, None),
        prior_lows=(None, None),
        compression_seen=(False, False),
        volume_ratios=(None, None),
        prior_means=(None, None),
    )

    state_filter = module._state_filter(
        soxl_bars,
        btc_bars,
        features,
        {"mode": "low_vol", "threshold": 1.0},
    )

    assert state_filter == (None, 0, 0)
