from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from mastermind_tick.bar_research import (
    ResearchBar,
    evaluate_targets,
    funding_by_bar,
)
from mastermind_tick.calendar_router_model import (
    SleeveDay,
    apply_state_volatility_overlay,
    combine_calendar_route,
    combine_static_anchor,
    replay_independent_sleeve,
)
from mastermind_tick.factor_overlay import (
    SignalOverlayConfig,
    VolatilityTargetConfig,
    evaluate_signal_overlay,
    evaluate_signal_volatility_overlay,
)
from mastermind_tick.factor_portfolio import decimal_returns, evaluate_static_portfolio
from mastermind_tick.lead_lag_factor import evaluate_weighted_targets
from mastermind_tick.models import Bar, FundingRate


def _bars(count: int, *, hours: int = 4) -> list[Bar]:
    start = datetime(2026, 1, 20, tzinfo=UTC)
    interval_ms = hours * 3_600_000
    result = []
    for index in range(count):
        start_ms = int((start + timedelta(hours=hours * index)).timestamp() * 1000)
        open_price = Decimal("100") + Decimal(index) / Decimal("5")
        close_price = open_price + Decimal((index % 7) - 3) / Decimal("4")
        result.append(
            Bar(
                start_ms,
                start_ms + interval_ms - 1,
                open_price,
                max(open_price, close_price) + Decimal("1"),
                min(open_price, close_price) - Decimal("1"),
                close_price,
                Decimal("1000"),
                100,
            )
        )
    return result


def _research_bars(bars: list[Bar]) -> list[ResearchBar]:
    return [
        ResearchBar(
            bar.start_ms,
            bar.end_ms,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
        )
        for bar in bars
    ]


def _source_returns(result: object) -> tuple[tuple[str, Decimal], ...]:
    return decimal_returns(result.daily_returns)  # type: ignore[attr-defined]


def test_fixed_quantity_sleeve_matches_frozen_target_evaluator_day_by_day() -> None:
    bars = _bars(72)
    targets = tuple(
        None if index < 2 else 1 if index < 23 else -1 if index < 51 else 0
        for index in range(len(bars))
    )
    funding = [
        FundingRate(bars[index].start_ms + 1_000, Decimal("0.0001"), bars[index].open)
        for index in (5, 17, 29, 41, 53, 65)
    ]
    research_bars = _research_bars(bars)
    source = evaluate_targets(
        research_bars,
        targets,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].end_ms,
        funding=funding_by_bar(research_bars, funding),
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        close_final_position=False,
    )

    forward = replay_independent_sleeve(
        bars,
        targets,
        funding,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].end_ms,
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
    )

    assert tuple((day, row.daily_return) for day, row in forward.items()) == _source_returns(source)
    held_quantities = [row.quantity for row in forward.values() if row.target == Decimal("1")]
    assert len(set(held_quantities)) == 1


def test_weighted_state_sleeve_matches_frozen_evaluator_day_by_day() -> None:
    bars = _bars(84)
    targets = tuple(
        None
        if index < 2
        else Decimal("1.5")
        if index < 29
        else Decimal("-2")
        if index < 58
        else Decimal("0")
        for index in range(len(bars))
    )
    funding = [
        FundingRate(bars[index].start_ms + 1_000, Decimal("-0.00005"), bars[index].open)
        for index in range(7, len(bars), 12)
    ]
    research_bars = _research_bars(bars)
    source = evaluate_weighted_targets(
        research_bars,
        targets,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].end_ms,
        funding=funding_by_bar(research_bars, funding),
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        monthly_loss_limit=Decimal("0.15"),
    )

    forward = replay_independent_sleeve(
        bars,
        targets,
        funding,
        start_ms=bars[0].start_ms,
        end_ms=bars[-1].end_ms,
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        monthly_loss_limit=Decimal("0.15"),
    )

    assert tuple((day, row.daily_return) for day, row in forward.items()) == _source_returns(source)


def test_static_anchor_matches_frozen_independent_compounding_day_by_day() -> None:
    labels = tuple(f"2026-02-{day:02d}" for day in range(1, 7))
    returns = {
        "lead": (
            Decimal("0.10"),
            Decimal("-0.03"),
            Decimal("0.02"),
            Decimal("0"),
            Decimal("0.04"),
            Decimal("-0.01"),
        ),
        "eth_eth": (
            Decimal("-0.02"),
            Decimal("0.01"),
            Decimal("0.03"),
            Decimal("-0.01"),
            Decimal("0"),
            Decimal("0.02"),
        ),
        "btc_btc": (
            Decimal("0.01"),
            Decimal("0.02"),
            Decimal("-0.04"),
            Decimal("0.03"),
            Decimal("0.01"),
            Decimal("0"),
        ),
        "eth_btc": (
            Decimal("0"),
            Decimal("-0.01"),
            Decimal("0.01"),
            Decimal("0.02"),
            Decimal("-0.02"),
            Decimal("0.03"),
        ),
    }
    sleeves = {
        name: {
            label: SleeveDay(
                label,
                index,
                Decimal("100000"),
                Decimal("0"),
                Decimal("100000"),
                Decimal("0"),
                value,
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
            )
            for index, (label, value) in enumerate(zip(labels, values, strict=True))
        }
        for name, values in returns.items()
    }
    allocations = {
        "lead": Decimal("0.40"),
        "eth_eth": Decimal("0.15"),
        "btc_btc": Decimal("0.30"),
        "eth_btc": Decimal("0.15"),
    }
    source = evaluate_static_portfolio(
        {name: tuple(zip(labels, values, strict=True)) for name, values in returns.items()},
        allocations,
        leverage=Decimal("4"),
    )

    forward = combine_static_anchor(sleeves, allocations, leverage=Decimal("4"))

    assert tuple((day, row["return"]) for day, row in forward.items()) == source.daily_returns
    assert forward[labels[-1]]["equity"] == source.final_equity


def test_full_router_daily_returns_match_frozen_research_pipeline() -> None:
    labels = tuple(
        (datetime(2026, 1, 20, tzinfo=UTC) + timedelta(days=index)).date().isoformat()
        for index in range(45)
    )
    component_returns = {
        "lead": tuple(Decimal((index % 9) - 4) / Decimal("1000") for index in range(45)),
        "eth_eth": tuple(Decimal((index % 7) - 3) / Decimal("1200") for index in range(45)),
        "btc_btc": tuple(Decimal((index % 5) - 2) / Decimal("900") for index in range(45)),
        "eth_btc": tuple(Decimal((index % 11) - 5) / Decimal("1500") for index in range(45)),
    }
    allocations = {
        "lead": Decimal("0.40"),
        "eth_eth": Decimal("0.15"),
        "btc_btc": Decimal("0.30"),
        "eth_btc": Decimal("0.15"),
    }
    component_sleeves = {
        name: _sleeve_days(labels, values) for name, values in component_returns.items()
    }
    source_anchor = evaluate_static_portfolio(
        {
            name: tuple(zip(labels, values, strict=True))
            for name, values in component_returns.items()
        },
        allocations,
        leverage=Decimal("4"),
    )
    scores = tuple(
        None if index < 3 else Decimal("-1.5") if index % 8 < 3 else Decimal("0.2")
        for index in range(45)
    )
    signal_config = SignalOverlayConfig(
        threshold=Decimal("1.25"),
        low_exposure=Decimal("0.8"),
        high_exposure=Decimal("2"),
        mode="below",
        turnover_bps=Decimal("7"),
    )
    volatility_config = VolatilityTargetConfig(
        lookback_days=20,
        target_daily_volatility=Decimal("0.03"),
        minimum_exposure=Decimal("0.6"),
        maximum_exposure=Decimal("1.1"),
        rebalance_frequency="daily",
        turnover_bps=Decimal("7"),
    )
    signals = tuple(zip(labels, scores, strict=True))
    source_signal_returns = evaluate_signal_overlay(
        source_anchor.daily_returns,
        signals,
        signal_config,
    ).daily_returns
    source_state = evaluate_signal_volatility_overlay(
        source_anchor.daily_returns,
        signals,
        signal_config,
        volatility_config,
        volatility_signal_returns=source_signal_returns,
    ).daily_returns
    candidates = {
        name: _sleeve_days(
            labels,
            tuple(Decimal(((index + offset) % 13) - 6) / Decimal("1800") for index in range(45)),
        )
        for offset, name in enumerate(("a", "b", "c", "d"))
    }
    mapping = {month: ("a", "b", "c") for month in range(1, 13)}
    mapping[2] = ("b", "c", "d")
    source_route = _source_calendar_route(
        source_state,
        {
            name: {day: row.daily_return for day, row in values.items()}
            for name, values in candidates.items()
        },
        mapping,
        Decimal("7"),
    )

    forward_anchor = combine_static_anchor(component_sleeves, allocations, leverage=Decimal("4"))
    forward_state = apply_state_volatility_overlay(
        forward_anchor,
        {day: (score, "golden", index) for index, (day, score) in enumerate(signals)},
        route_cost_bps=Decimal("7"),
    )
    forward_route = combine_calendar_route(
        forward_state,
        candidates,
        mapping,
        route_cost_bps=Decimal("7"),
    )

    assert tuple((day, row["return"]) for day, row in forward_route.items()) == source_route
    february_first = next(day for day in labels if day.startswith("2026-02"))
    assert forward_route[february_first]["route_turnover"] == Decimal("1") / Decimal("6")


def _sleeve_days(labels: tuple[str, ...], values: tuple[Decimal, ...]) -> dict[str, SleeveDay]:
    return {
        label: SleeveDay(
            label,
            index,
            Decimal("100000"),
            Decimal("0"),
            Decimal("100000"),
            Decimal("0"),
            value,
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        )
        for index, (label, value) in enumerate(zip(labels, values, strict=True))
    }


def _source_calendar_route(
    state: tuple[tuple[str, Decimal], ...],
    candidates: dict[str, dict[str, Decimal]],
    mapping: dict[int, tuple[str, ...]],
    route_cost_bps: Decimal,
) -> tuple[tuple[str, Decimal], ...]:
    previous_weights: dict[str, Decimal] = {}
    current_month = ""
    rate = route_cost_bps / Decimal("10000")
    result = []
    for label, state_return in state:
        selected = mapping[int(label[5:7])]
        weights = {"state": Decimal("0.5")}
        weights.update({name: Decimal("1") / Decimal("6") for name in selected})
        turnover = Decimal("0")
        if label[:7] != current_month:
            if previous_weights:
                names = set(previous_weights) | set(weights)
                turnover = sum(
                    (
                        abs(
                            weights.get(name, Decimal("0"))
                            - previous_weights.get(name, Decimal("0"))
                        )
                        for name in names
                    ),
                    Decimal("0"),
                ) / Decimal("2")
            else:
                turnover = sum(weights.values(), Decimal("0"))
            previous_weights = weights
            current_month = label[:7]
        value = Decimal("0.5") * state_return + sum(
            (candidates[name][label] for name in selected), Decimal("0")
        ) / Decimal("6")
        result.append((label, value - turnover * rate))
    return tuple(result)
