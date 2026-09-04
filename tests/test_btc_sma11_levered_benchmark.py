import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma11_levered_benchmark import constant_targets


def test_constant_targets_emits_one_initial_target() -> None:
    assert constant_targets(3, Decimal("1.5")) == (Decimal("1.5"), None, None)


def test_constant_targets_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="positive"):
        constant_targets(0, Decimal("1.5"))
