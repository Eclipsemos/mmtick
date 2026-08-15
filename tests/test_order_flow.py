from decimal import Decimal

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.order_flow import (
    FlowCandidate,
    OrderFlowBar,
    _monthly_chunks,
    causal_flow_features,
    flow_targets,
)


def _bars(count: int) -> list[ResearchBar]:
    return [
        ResearchBar(
            start_ms=index * 240 * 60_000,
            end_ms=(index + 1) * 240 * 60_000 - 1,
            open=Decimal("100") + Decimal(index),
            high=Decimal("101") + Decimal(index),
            low=Decimal("99") + Decimal(index),
            close=Decimal("100") + Decimal(index),
            volume=Decimal("10"),
        )
        for index in range(count)
    ]


def _flow(bars: list[ResearchBar]) -> dict[int, OrderFlowBar]:
    return {
        bar.start_ms: OrderFlowBar(
            start_ms=bar.start_ms,
            bucket_count=100 + index,
            total_notional=Decimal("1000") + Decimal(index * 10),
            buy_notional=Decimal("600") + Decimal(index),
            sell_notional=Decimal("400") + Decimal(index),
            unknown_notional=Decimal("0"),
        )
        for index, bar in enumerate(bars)
    }


def test_flow_features_do_not_change_when_future_flow_is_appended() -> None:
    bars = _bars(20)
    original = causal_flow_features(bars, _flow(bars), window=8)
    extended_bars = _bars(24)
    extended_flow = _flow(extended_bars)
    extended_flow[extended_bars[-1].start_ms] = OrderFlowBar(
        start_ms=extended_bars[-1].start_ms,
        bucket_count=9_999,
        total_notional=Decimal("999999"),
        buy_notional=Decimal("1"),
        sell_notional=Decimal("999998"),
        unknown_notional=Decimal("0"),
    )

    extended = causal_flow_features(extended_bars, extended_flow, window=8)

    for feature in original:
        assert extended[feature][: len(bars)] == original[feature]


def test_order_flow_sources_remain_separate() -> None:
    flow = OrderFlowBar(
        start_ms=0,
        bucket_count=4,
        total_notional=Decimal("100"),
        buy_notional=Decimal("10"),
        sell_notional=Decimal("20"),
        unknown_notional=Decimal("10"),
        tick_rule_buy_notional=Decimal("50"),
        tick_rule_sell_notional=Decimal("10"),
    )

    assert flow.reported_notional == Decimal("30")
    assert flow.reported_imbalance == Decimal("-10") / Decimal("30")
    assert flow.tick_rule_notional == Decimal("60")
    assert flow.tick_rule_imbalance == Decimal("40") / Decimal("60")


def test_flow_targets_apply_confirmation_hold_and_cooldown() -> None:
    candidate = FlowCandidate(
        feature="imbalance_follow",
        window=42,
        direction="long_only",
        threshold=Decimal("1"),
        smoothing_bars=1,
        minimum_hold_bars=2,
        cooldown_bars=2,
        confirmation_bars=2,
    )

    targets = flow_targets(
        (None, Decimal("2"), Decimal("2"), Decimal("0"), Decimal("0"), Decimal("2")),
        candidate,
    )

    assert targets == (None, 0, 1, 1, 0, 0)


def test_monthly_chunks_cover_period_without_overlap() -> None:
    periods = ((1577836800000, 1583020799999),)

    chunks = _monthly_chunks(periods)

    assert chunks == (
        (1577836800000, 1580515199999),
        (1580515200000, 1583020799999),
    )
