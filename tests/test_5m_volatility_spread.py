import importlib.util
import sys
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts" / "explore_soxl_5m_volatility_spread.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("mmtick_five_minute_spread", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resample_ticks_inserts_flat_no_trade_bars_without_execution() -> None:
    module = _module()
    rows = iter(
        [
            (0, "100", "101", "99", "100", "2"),
            (1_000, "100", "102", "100", "101", "3"),
            (600_000, "102", "104", "101", "103", "4"),
        ]
    )

    bars, executions = module._resample_ticks(rows)

    assert [bar.start_ms for bar in bars] == [0, 300_000, 600_000]
    assert bars[0].open == 100
    assert bars[0].close == 101
    assert bars[1].open == bars[1].high == bars[1].low == bars[1].close == 101
    assert bars[1].volume == 0
    assert executions[0].timestamp_ms == 0
    assert executions[1] is None
    assert executions[2].timestamp_ms == 600_000
