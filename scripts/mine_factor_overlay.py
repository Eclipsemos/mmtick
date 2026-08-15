#!/usr/bin/env python3
"""Search a causal exposure overlay for the frozen static BTC/ETH factor anchor."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mine_factor_portfolio import CONFIRMATION, DISCOVERY
from train_continuous_factor import SELECTION_2024, SELECTION_2025
from train_walk_forward_factor import (
    ANCHOR_ALLOCATIONS,
    ANCHOR_LEVERAGE,
    _anchor_context,
    _evaluate_anchor,
)

from mastermind_tick.bar_research import ResearchBar, aggregate_bars
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_overlay import FactorOverlayConfig, evaluate_factor_overlay
from mastermind_tick.factor_portfolio import DailyReturns, PortfolioResult

PERIODS = {
    "discovery": DISCOVERY,
    "selection_2024": SELECTION_2024,
    "selection_2025": SELECTION_2025,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/factor_overlay/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading aligned BTC/ETH bars and frozen static anchor", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ("btc_perp", "eth_perp")}
    bars: dict[str, list[ResearchBar]] = {
        asset: aggregate_bars(loaded[asset][0], 240) for asset in loaded
    }
    anchor = _anchor_context(bars, loaded)
    anchor_results = {
        name: _evaluate_anchor(anchor, period, stress=False) for name, period in PERIODS.items()
    }
    signal_sets = _signal_sets(bars, anchor_results)

    print("searching causal anchor and market-state exposure overlays", flush=True)
    rows = []
    for signal_name, signals in signal_sets.items():
        for config in _candidate_library():
            results = {
                name: evaluate_factor_overlay(
                    result.daily_returns,
                    config,
                    signal_returns=signals[name],
                )
                for name, result in anchor_results.items()
            }
            rows.append(
                {
                    "signal": signal_name,
                    "config": config,
                    "results": results,
                    "score": _score(results),
                }
            )
    eligible = [row for row in rows if _eligible(row["results"])]
    ranked = sorted(eligible, key=lambda row: row["score"], reverse=True)
    selected = ranked[0] if ranked else None
    confirmation = None
    stress = None
    if selected:
        confirmation_anchor = _evaluate_anchor(anchor, CONFIRMATION, stress=False)
        stress_anchor = _evaluate_anchor(anchor, CONFIRMATION, stress=True)
        confirmation_signals = _confirmation_signals(
            bars, confirmation_anchor.daily_returns, selected["signal"]
        )
        confirmation = evaluate_factor_overlay(
            confirmation_anchor.daily_returns,
            selected["config"],
            signal_returns=confirmation_signals,
        )
        stress = evaluate_factor_overlay(
            stress_anchor.daily_returns,
            selected["config"],
            signal_returns=confirmation_signals,
        )
    payload = _report(bars, rows, eligible, selected, confirmation, stress)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"factor-overlay-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _candidate_library() -> tuple[FactorOverlayConfig, ...]:
    daily = tuple(
        FactorOverlayConfig(lookback, threshold, low, high, mode, "daily")
        for lookback in (5, 10, 20, 30, 60, 90)
        for threshold in (
            Decimal("-0.2"),
            Decimal("-0.1"),
            Decimal("0"),
            Decimal("0.1"),
            Decimal("0.2"),
            Decimal("0.3"),
        )
        for low in (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1"))
        for high in (
            Decimal("1.25"),
            Decimal("1.5"),
            Decimal("1.75"),
            Decimal("2"),
            Decimal("2.5"),
        )
        for mode in ("momentum", "contrarian")
    )
    monthly = tuple(
        FactorOverlayConfig(lookback, threshold, low, high, mode, "monthly")
        for lookback in (1, 2, 3, 6)
        for threshold in (
            Decimal("-0.1"),
            Decimal("0"),
            Decimal("0.1"),
            Decimal("0.2"),
            Decimal("0.3"),
        )
        for low in (Decimal("0.5"), Decimal("0.75"), Decimal("1"))
        for high in (
            Decimal("1.25"),
            Decimal("1.5"),
            Decimal("1.75"),
            Decimal("2"),
            Decimal("2.5"),
        )
        for mode in ("momentum", "contrarian")
    )
    return (*daily, *monthly)


def _signal_sets(
    bars: dict[str, list[ResearchBar]],
    anchor_results: dict[str, PortfolioResult],
) -> dict[str, dict[str, DailyReturns]]:
    market = {
        asset: {
            name: _market_daily_returns(bars[asset], period) for name, period in PERIODS.items()
        }
        for asset in bars
    }
    relative = {
        name: tuple(
            (label, (Decimal("1") + btc) / (Decimal("1") + eth) - Decimal("1"))
            for (label, btc), (eth_label, eth) in zip(
                market["btc_perp"][name], market["eth_perp"][name], strict=True
            )
            if label == eth_label
        )
        for name in PERIODS
    }
    return {
        "anchor": {name: result.daily_returns for name, result in anchor_results.items()},
        "btc_perp": market["btc_perp"],
        "eth_perp": market["eth_perp"],
        "btc_eth_relative": relative,
    }


def _confirmation_signals(
    bars: dict[str, list[ResearchBar]],
    anchor_returns: DailyReturns,
    signal: str,
) -> DailyReturns:
    if signal == "anchor":
        return anchor_returns
    btc = _market_daily_returns(bars["btc_perp"], CONFIRMATION)
    eth = _market_daily_returns(bars["eth_perp"], CONFIRMATION)
    if signal == "btc_perp":
        return btc
    if signal == "eth_perp":
        return eth
    if signal == "btc_eth_relative":
        return tuple(
            (label, (Decimal("1") + left) / (Decimal("1") + right) - Decimal("1"))
            for (label, left), (right_label, right) in zip(btc, eth, strict=True)
            if label == right_label
        )
    raise ValueError(f"unsupported factor overlay signal: {signal}")


def _market_daily_returns(bars: list[ResearchBar], period: tuple[int, int]) -> DailyReturns:
    daily_closes: dict[str, Decimal] = {}
    for bar in bars:
        label = datetime.fromtimestamp(bar.start_ms / 1000, UTC).date().isoformat()
        daily_closes[label] = bar.close
    result = []
    previous: Decimal | None = None
    for label, close in daily_closes.items():
        timestamp = int(datetime.fromisoformat(label).replace(tzinfo=UTC).timestamp() * 1000)
        value = Decimal("0") if previous is None else close / previous - Decimal("1")
        if period[0] <= timestamp <= period[1]:
            result.append((label, value))
        previous = close
    return tuple(result)


def _eligible(results: dict[str, PortfolioResult]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and not result.bankrupt
        for result in results.values()
    )


def _score(results: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
    values = tuple(results.values())
    return (
        min(result.target_month_rate for result in values),
        sum((result.target_month_rate for result in values), Decimal("0")),
        min(result.positive_month_rate for result in values),
        min(result.worst_month for result in values),
        min(result.net_return for result in values),
        min(result.max_drawdown for result in values),
    )


def _public_result(result: PortfolioResult, *, daily: bool = False) -> dict[str, Any]:
    return result.as_dict(include_daily=daily)


def _report(
    bars: dict[str, list[ResearchBar]],
    rows: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    confirmation: PortfolioResult | None,
    stress: PortfolioResult | None,
) -> dict[str, Any]:
    achieved = bool(
        confirmation
        and stress
        and confirmation.target_month_rate >= Decimal("0.5")
        and confirmation.max_drawdown >= Decimal("-0.35")
        and confirmation.net_return > 0
        and stress.net_return > 0
        and stress.max_drawdown >= Decimal("-0.35")
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "causal trailing-return exposure overlay on frozen static factor anchor",
        "data": {
            "first_bar": _timestamp(max(item[0].start_ms for item in bars.values())),
            "last_bar": _timestamp(min(item[-1].end_ms for item in bars.values())),
        },
        "anchor": {
            "allocations": {name: float(value) for name, value in ANCHOR_ALLOCATIONS.items()},
            "internal_leverage": float(ANCHOR_LEVERAGE),
            "frozen_before_overlay_search": True,
        },
        "selection": {
            "candidate_count": len(rows),
            "eligible_count": len(eligible),
            "confirmation_used_for_selection": False,
            "selected": (
                {
                    "config": selected["config"].as_dict(),
                    "signal": selected["signal"],
                    **{
                        name: _public_result(result) for name, result in selected["results"].items()
                    },
                }
                if selected
                else None
            ),
        },
        "confirmation": _public_result(confirmation, daily=True) if confirmation else None,
        "stress_confirmation": _public_result(stress) if stress else None,
        "target": {"monthly_return": 0.25, "minimum_target_month_rate": 0.5, "achieved": achieved},
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The overlay met the reused confirmation gates; fresh forward evidence "
                "remains required."
                if achieved
                else "The development-selected overlay failed monthly coverage, drawdown, "
                "or stress gates."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The overlay observes only prior closed daily anchor returns and acts on the next day.",
            "Exposure changes include 7 bps turnover cost; borrowing cost and liquidation "
            "are omitted.",
            "Portfolio drawdown is measured at daily closes.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only causal exposure overlay on the frozen static factor anchor.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Eligible configurations: `{payload['selection']['eligible_count']}` / "
        f"`{payload['selection']['candidate_count']}`.",
    ]
    if selected:
        config = selected["config"]
        lines.extend(
            [
                f"Selected signal `{selected['signal']}`, `{config['mode']}` "
                f"`{config['rebalance_frequency']}` lookback "
                f"`{config['lookback_periods']}`, threshold "
                f"`{config['threshold']:.2%}`, exposure `{config['low_exposure']:.2f}x` / "
                f"`{config['high_exposure']:.2f}x`.",
                "",
                "| Split | Return | Max DD | Positive months | 25% months |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for name, label in (
            ("discovery", "2021-2023 discovery"),
            ("selection_2024", "2024 selection"),
            ("selection_2025", "2025 selection"),
        ):
            lines.append(_metric_row(label, selected[name]))
    if confirmation and stress:
        lines.extend(
            [
                _metric_row("2026 reused confirmation", confirmation),
                _metric_row("2026 stress 10+5 bps", stress),
                "",
                "## 2026 monthly returns",
                "",
                "| Month | Base | Stress |",
                "|---|---:|---:|",
            ]
        )
        stressed = {row["label"]: row["return"] for row in stress["monthly_returns"]}
        lines.extend(
            f"| {row['label']} | {row['return']:.2%} | {stressed[row['label']]:.2%} |"
            for row in confirmation["monthly_returns"]
        )
    lines.extend(["", payload["decision"]["reason"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.append("")
    return "\n".join(lines)


def _metric_row(label: str, result: dict[str, Any]) -> str:
    reached = sum(row["return"] >= 0.25 for row in result["monthly_returns"])
    return (
        f"| {label} | {result['net_return']:.2%} | {result['max_drawdown']:.2%} | "
        f"{result['positive_month_rate']:.2%} | {reached}/{len(result['monthly_returns'])} |"
    )


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
