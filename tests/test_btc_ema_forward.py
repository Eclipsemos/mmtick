import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _forward_module():
    path = Path(__file__).parents[1] / "scripts" / "evaluate_btc_ema_forward.py"
    spec = importlib.util.spec_from_file_location("btc_ema_forward", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_frozen_btc_ema_candidate_hash_is_valid() -> None:
    forward = _forward_module()
    candidate = json.loads(
        (
            Path(__file__).parents[1]
            / "strategies/candidates/btc_daily_ema_10_50_long_short_v1.json"
        ).read_text()
    )

    forward.validate_candidate(candidate)


def test_frozen_btc_ema_candidate_rejects_parameter_changes() -> None:
    forward = _forward_module()
    candidate = json.loads(
        (
            Path(__file__).parents[1]
            / "strategies/candidates/btc_daily_ema_10_50_long_short_v1.json"
        ).read_text()
    )
    candidate["parameters"]["fast_period"] = 11

    with pytest.raises(ValueError, match="hash mismatch"):
        forward.validate_candidate(candidate)
