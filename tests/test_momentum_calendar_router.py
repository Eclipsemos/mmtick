import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

from mastermind_tick.bar_research import ResearchBar


def _momentum_module():
    path = Path(__file__).parents[1] / "scripts" / "research" / "mine_momentum_calendar_router.py"
    spec = importlib.util.spec_from_file_location("momentum_calendar_router", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


MOMENTUM = _momentum_module()


def _bar(day, close):
    start = day * 86_400_000
    return ResearchBar(start, start + 86_400_000 - 1, close, close, close, close)


def test_momentum_regime_uses_only_prior_closed_days() -> None:
    bars = [
        _bar(0, Decimal("100")),
        _bar(1, Decimal("100")),
        _bar(2, Decimal("50")),
        _bar(3, Decimal("50")),
    ]

    regimes = MOMENTUM._prior_day_momentum_regimes(bars, 1, Decimal("-0.1"))
    labels = sorted(regimes)

    assert regimes[labels[2]] is False
    assert regimes[labels[3]] is True


def test_dual_direction_route_switches_after_bearish_regime_and_charges_turnover() -> None:
    state = (
        ("2025-01-02", Decimal("0")),
        ("2025-01-03", Decimal("0")),
    )
    candidates = {
        "long": {"2025-01-02": Decimal("0.02"), "2025-01-03": Decimal("0.02")},
        "short": {"2025-01-02": Decimal("0.03"), "2025-01-03": Decimal("0.03")},
    }
    long_mapping = {month: ("long",) for month in range(1, 13)}
    short_mapping = {month: ("short",) for month in range(1, 13)}

    result = MOMENTUM._dual_direction_returns(
        state,
        candidates,
        long_mapping,
        short_mapping,
        {"2025-01-02": False, "2025-01-03": True},
        Decimal("0.5"),
        Decimal("10"),
        2025,
    )

    assert result == (
        ("2025-01-02", Decimal("0.009")),
        ("2025-01-03", Decimal("0.0145")),
    )


def test_momentum_confirmation_excludes_partial_august() -> None:
    assert max(MOMENTUM.VALIDATION_YEARS) == 2025
    assert MOMENTUM.COMPLETE_CONFIRMATION_END.isoformat() == "2026-07-31"
