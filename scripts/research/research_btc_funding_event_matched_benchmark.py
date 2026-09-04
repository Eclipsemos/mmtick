#!/usr/bin/env python3
"""Screen causal BTC funding-event signals against continuous 1.5X BTC."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from audit_btc_sma11_levered_benchmark import constant_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.funding_event_factor import (
    FundingEventCandidate,
    funding_event_scores,
    funding_event_targets,
)
from mastermind_tick.sma_trend import aggregate_complete_periods

OUTPUT = Path("reports/experiments/btc_funding_event_matched_benchmark/2026-09-03")
ACTIVE = Decimal("1.5")
LOOKBACK_EVENTS = (30, 90, 180)
THRESHOLDS = (Decimal("1"), Decimal("1.5"), Decimal("2"), Decimal("2.5"), Decimal("3"))
HOLD_BARS = (1, 2, 4, 8, 12)
MODES = ("reversal", "continuation")
DISPLAY_ROWS = 20


def candidate_library() -> tuple[FundingEventCandidate, ...]:
    return tuple(
        FundingEventCandidate(lookback, threshold, hold, mode, "long_only")
        for lookback in LOOKBACK_EVENTS
        for threshold in THRESHOLDS
        for hold in HOLD_BARS
        for mode in MODES
    )


def periods(last_end: int) -> dict[str, tuple[int, int]]:
    return {
        "research": (base.utc_ms(2020), base.utc_ms(2022, 12, 31, 23, 59, 59, 999000)),
        "validation": (base.utc_ms(2023), base.utc_ms(2024, 12, 31, 23, 59, 59, 999000)),
        "oos": (base.utc_ms(2025), last_end),
        "full": (base.utc_ms(2020), last_end),
    }


def target_indices(spot_count: int, four_hour_end_indices: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(spot_count + index for index in four_hour_end_indices)


def map_exposure_targets(
    signals: tuple[Decimal, ...], exposure: Decimal = ACTIVE
) -> tuple[Decimal, ...]:
    """Map the funding factor's 0/1 state to deliberate flat/long exposure."""
    return tuple(exposure if signal > 0 else Decimal("0") for signal in signals)


def public(result, matched, buy_and_hold) -> dict[str, float | bool]:
    return {
        "strategy_return": result.net_return,
        "matched_1p5x_return": matched.net_return,
        "one_x_buy_and_hold_return": buy_and_hold["net_return"],
        "matched_excess": result.net_return - matched.net_return,
        "one_x_excess": result.net_return - buy_and_hold["net_return"],
        "strategy_drawdown": result.max_drawdown,
        "matched_drawdown": matched.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def qualifies(metrics: dict[str, dict[str, float | bool]]) -> bool:
    return all(
        row["matched_excess"] > 0
        and row["maximum_intrabar_leverage"] <= 3
        and not row["liquidated"]
        for row in metrics.values()
    )


def main() -> None:
    spot, futures, _daily, _daily_indices, full_funding = base.load_hybrid_inputs()
    bars = spot + futures
    four_hour, four_hour_ends = aggregate_complete_periods(futures, "4h")
    funding_rates = [event for events in full_funding[len(spot) :] for event in events]
    four_hour_funding = funding_by_bar(four_hour, funding_rates)
    if sum(len(events) for events in four_hour_funding) < max(LOOKBACK_EVENTS):
        raise ValueError("insufficient BTC funding events for the configured windows")
    bounds = periods(bars[-1].end_ms)
    source_indices = target_indices(len(spot), four_hour_ends)
    matched_targets = constant_targets(len(bars), ACTIVE)
    matched = {
        name: base.replay(bars, matched_targets, full_funding, *period)
        for name, period in bounds.items()
    }
    buy_and_hold = {name: base.benchmark(bars, *period) for name, period in bounds.items()}
    scores = {
        lookback: funding_event_scores(four_hour_funding, lookback) for lookback in LOOKBACK_EVENTS
    }

    rows = []
    for candidate in candidate_library():
        event_targets = funding_event_targets(scores[candidate.lookback_events], candidate)
        targets = base.map_targets(len(bars), source_indices, map_exposure_targets(event_targets))
        development = {
            name: public(
                base.replay(bars, targets, full_funding, *bounds[name]),
                matched[name],
                buy_and_hold[name],
            )
            for name in ("research", "validation")
        }
        rows.append(
            {
                "id": candidate.id,
                "candidate": candidate.as_dict(),
                "development": development,
                "development_min_matched_excess": min(
                    row["matched_excess"] for row in development.values()
                ),
                "targets": targets,
            }
        )
    rows.sort(key=lambda row: row["development_min_matched_excess"], reverse=True)
    qualifying = [row for row in rows if qualifies(row["development"])]
    for row in qualifying:
        row["oos"] = public(
            base.replay(bars, row["targets"], full_funding, *bounds["oos"]),
            matched["oos"],
            buy_and_hold["oos"],
        )
        row["full"] = public(
            base.replay(bars, row["targets"], full_funding, *bounds["full"]),
            matched["full"],
            buy_and_hold["full"],
        )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / NOT_PROMOTED",
        "protocol": {
            "signals": "funding events normalized against prior events only; completed 4h bar",
            "execution": "next 15m open after the completed four-hour signal bar",
            "family": "extreme-funding continuation/reversal; long-only 0X/1.5X exposure",
            "lookback_events": LOOKBACK_EVENTS,
            "thresholds": [str(value) for value in THRESHOLDS],
            "hold_bars_4h": HOLD_BARS,
            "selection": "Research 2020-2022 and Validation 2023-2024 only",
            "oos": "2025 through latest 15m bar, unread unless development qualifies",
            "benchmark": (
                "continuous 1.5X BTC under identical 50/50 wallets, Funding, costs, and controls"
            ),
            "costs": "10 bps fee + 5 bps slippage per side; historical Funding",
            "hard_cap": "2X futures opening control; observed intrabar effective leverage <=3X",
        },
        "data": {
            "four_hour_bars": len(four_hour),
            "funding_events": sum(len(events) for events in four_hour_funding),
            "last": base.iso(bars[-1].end_ms),
        },
        "candidate_count": len(rows),
        "development_qualifying_count": len(qualifying),
        "matched_benchmark": {
            name: public(value, value, buy_and_hold[name]) for name, value in matched.items()
        },
        "results": [{key: value for key, value in row.items() if key != "targets"} for row in rows],
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def render(payload: dict) -> str:
    lines = [
        "# BTC Funding-Event Matched-Benchmark Screen",
        "",
        "Funding 结算事件只按此前事件归一化，信号在完成 4h K 线后、下一根 15m 开盘执行。",
        "",
        "| 配置 | R相对1.5X | V相对1.5X | 开发最差 | R相对1X | V相对1X |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"][:DISPLAY_ROWS]:
        research = row["development"]["research"]
        validation = row["development"]["validation"]
        lines.append(
            f"| `{row['id']}` | {research['matched_excess']:.2%} | "
            f"{validation['matched_excess']:.2%} | {row['development_min_matched_excess']:.2%} | "
            f"{research['one_x_excess']:.2%} | {validation['one_x_excess']:.2%} |"
        )
    lines += [
        "",
        (f"Markdown 仅显示按开发期最差表现排序的前 {DISPLAY_ROWS} 个；完整候选见 `results.json`。"),
        (
            "开发期合格成员："
            f"{payload['development_qualifying_count']} / {payload['candidate_count']}。"
        ),
        "只有开发期同时超过连续 1.5X BTC、无强平且盘中杠杆不超过 3X 的成员才读取 OOS。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
