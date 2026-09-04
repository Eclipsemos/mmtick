import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_sma12_three_state_stability import exact_sign_pvalue  # noqa: E402


def test_exact_sign_pvalue() -> None:
    assert exact_sign_pvalue(6, 7) == pytest.approx(8 / 128)


def test_exact_sign_pvalue_rejects_invalid_counts() -> None:
    with pytest.raises(ValueError, match="wins must be between"):
        exact_sign_pvalue(2, 1)
