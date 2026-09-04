import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "research"))

from audit_btc_margin_buffer import audit_candidate  # noqa: E402
from research_btc_dynamic_exposure import DynamicResult  # noqa: E402

from mastermind_tick.bar_research import ResearchBar  # noqa: E402


def test_margin_audit_reports_headroom_with_futures() -> None:
    bars = [ResearchBar(0, 899_999, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"))]
    result = DynamicResult(
        net_return=0.0,
        max_drawdown=0.0,
        completed_trades=0,
        total_fees=0.0,
        total_funding=0.0,
        bankrupt=False,
        risk_curve=((899_999, 100_000.0, 50_000.0, 150_000.0, 1.5),),
    )

    audited = audit_candidate(bars, result, (Decimal("0.02"),))

    assert audited["rates"]["0.02"]["minimum_buffer"] == pytest.approx(99_000.0)
    assert audited["rates"]["0.02"]["at_or_below_liquidation_buffer_bars"] == 0
    assert audited["rates"]["0.02"][
        "minimum_additional_price_decline_to_maintenance"
    ] == pytest.approx(99_000 / 149_000)
