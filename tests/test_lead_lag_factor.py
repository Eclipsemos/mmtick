from dataclasses import replace
from decimal import Decimal

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.lead_lag_factor import (
    LeadLagCandidate,
    ShockSizing,
    causal_shock_scores,
    evaluate_weighted_targets,
    shock_targets,
    shock_weight_targets,
)


def _bars(count: int, multiplier: str = "1") -> list[ResearchBar]:
    scale = Decimal(multiplier)
    return [
        ResearchBar(
            start_ms=index * 240 * 60_000,
            end_ms=(index + 1) * 240 * 60_000 - 1,
            open=(Decimal("100") + Decimal(index)) * scale,
            high=(Decimal("101") + Decimal(index)) * scale,
            low=(Decimal("99") + Decimal(index)) * scale,
            close=(Decimal("100") + Decimal(index)) * scale,
            volume=Decimal("100"),
        )
        for index in range(count)
    ]


def test_shock_scores_do_not_change_when_future_bars_are_appended() -> None:
    btc = _bars(30)
    eth = _bars(30, "0.8")
    original = causal_shock_scores(btc, eth, 12)
    extended = causal_shock_scores(_bars(35), _bars(35, "0.8"), 12)

    assert extended[0][: len(btc)] == original[0]
    assert extended[1][: len(eth)] == original[1]


def test_shock_targets_hold_then_force_one_flat_signal_bar() -> None:
    candidate = LeadLagCandidate(30, Decimal("2"), 2, "long_short", "none")

    targets = shock_targets(
        (Decimal("3"), Decimal("4"), Decimal("4"), Decimal("-3"), Decimal("-3")),
        (Decimal("0"),) * 5,
        candidate,
    )

    assert targets == (1, 1, 0, -1, -1)


def test_underreaction_gate_rejects_completed_eth_response() -> None:
    candidate = LeadLagCandidate(30, Decimal("2"), 2, "long_short", "underreaction")

    targets = shock_targets(
        (Decimal("3"), Decimal("3")),
        (Decimal("2.5"), Decimal("1")),
        candidate,
    )

    assert targets == (0, 1)


def test_shock_sizing_is_frozen_for_each_active_trade() -> None:
    weighted = shock_weight_targets(
        (1, 1, 0, -1, -1),
        (Decimal("2.1"), Decimal("4"), Decimal("0"), Decimal("3.2"), Decimal("2")),
        ShockSizing(Decimal("0.5"), Decimal("1"), Decimal("2")),
    )

    assert weighted == (
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("0"),
        Decimal("-2"),
        Decimal("-2"),
    )


def test_weighted_flat_replay_has_zero_return() -> None:
    bars = _bars(5)
    result = evaluate_weighted_targets(
        bars,
        (Decimal("0"),) * len(bars),
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].start_ms,
    )

    assert result.net_return == 0
    assert result.completed_trades == 0


def test_monthly_loss_limit_reduces_persistent_long_loss() -> None:
    bars = _bars(200)
    falling = [
        replace(
            bar,
            open=Decimal("200") - Decimal(index) / Decimal("2"),
            high=Decimal("201") - Decimal(index) / Decimal("2"),
            low=Decimal("199") - Decimal(index) / Decimal("2"),
            close=Decimal("199.5") - Decimal(index) / Decimal("2"),
        )
        for index, bar in enumerate(bars)
    ]
    targets = (Decimal("2"),) * len(falling)

    unlimited = evaluate_weighted_targets(
        falling,
        targets,
        start_ms=falling[0].start_ms,
        end_ms=falling[-1].start_ms,
    )
    limited = evaluate_weighted_targets(
        falling,
        targets,
        start_ms=falling[0].start_ms,
        end_ms=falling[-1].start_ms,
        monthly_loss_limit=Decimal("0.10"),
    )

    assert limited.max_drawdown > unlimited.max_drawdown
