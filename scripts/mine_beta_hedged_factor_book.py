#!/usr/bin/env python3
"""Search a shared-equity beta hedge for the frozen four-factor BTC/ETH book."""

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

from mine_factor_portfolio import (
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    CONFIRMATION,
    DISCOVERY,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    VALIDATION,
)
from train_walk_forward_factor import (
    ANCHOR_ALLOCATIONS,
    ANCHOR_LEVERAGE,
    _anchor_context,
)

from mastermind_tick.bar_research import ResearchBar, aggregate_bars, funding_by_bar
from mastermind_tick.factor_book import FactorBookResult, evaluate_factor_book
from mastermind_tick.factor_mining import load_market

PERIODS = {"discovery": DISCOVERY, "validation": VALIDATION}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/beta_hedged_factor_book/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading aligned BTC/ETH factor book inputs", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ("btc_perp", "eth_perp")}
    bars: dict[str, list[ResearchBar]] = {
        asset: aggregate_bars(loaded[asset][0], 240) for asset in loaded
    }
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in bars}
    anchor = _anchor_context(bars, loaded)
    raw_targets = _raw_anchor_targets(anchor)

    print("searching common-beta hedges and shared-book risk scales", flush=True)
    rows = []
    for hedge_asset in ("btc_perp", "eth_perp"):
        for beta in tuple(Decimal(value) for value in ("0.5", "0.75", "1", "1.25", "1.5")):
            for hedge_ratio in tuple(Decimal(value) for value in ("0", "0.25", "0.5", "0.75", "1")):
                for risk_scale in tuple(
                    Decimal(value) for value in ("0.5", "0.75", "1", "1.25", "1.5", "2", "2.5")
                ):
                    targets = _hedged_targets(
                        raw_targets, hedge_asset, beta, hedge_ratio, risk_scale
                    )
                    results = {
                        split: evaluate_factor_book(
                            bars,
                            targets,
                            start_ms=period[0],
                            end_ms=period[1],
                            funding=funding,
                            fee_bps=BASE_FEE_BPS,
                            slippage_bps=BASE_SLIPPAGE_BPS,
                        )
                        for split, period in PERIODS.items()
                    }
                    rows.append(
                        {
                            "hedge_asset": hedge_asset,
                            "beta": beta,
                            "hedge_ratio": hedge_ratio,
                            "risk_scale": risk_scale,
                            "targets": targets,
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
        confirmation = evaluate_factor_book(
            bars,
            selected["targets"],
            start_ms=CONFIRMATION[0],
            end_ms=CONFIRMATION[1],
            funding=funding,
            fee_bps=BASE_FEE_BPS,
            slippage_bps=BASE_SLIPPAGE_BPS,
        )
        stress = evaluate_factor_book(
            bars,
            selected["targets"],
            start_ms=CONFIRMATION[0],
            end_ms=CONFIRMATION[1],
            funding=funding,
            fee_bps=STRESS_FEE_BPS,
            slippage_bps=STRESS_SLIPPAGE_BPS,
        )
    payload = _report(bars, rows, eligible, selected, confirmation, stress)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"beta-hedged-factor-book-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _raw_anchor_targets(anchor: dict[str, Any]) -> dict[str, tuple[Decimal, ...]]:
    events = anchor["events"]
    eth_long = events[
        "event-eth_perp-to-eth_perp-continuation-60d-threshold-2p5-hold-12x4h-none-long_only"
    ].targets
    btc_short = events[
        "event-btc_perp-to-btc_perp-continuation-15d-threshold-2-hold-4x4h-none-long_short"
    ].targets
    btc_slow = events[
        "event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-hold-12x4h-underreaction-long_short"
    ].targets
    lead = anchor["lead_targets"]

    def value(target: Any) -> Decimal:
        return Decimal("0") if target is None else Decimal(target)

    return {
        "btc_perp": tuple(
            ANCHOR_LEVERAGE
            * (
                ANCHOR_ALLOCATIONS[btc_short_id] * value(first)
                + ANCHOR_ALLOCATIONS[btc_slow_id] * value(second)
            )
            for first, second in zip(btc_short, btc_slow, strict=True)
        ),
        "eth_perp": tuple(
            ANCHOR_LEVERAGE
            * (
                ANCHOR_ALLOCATIONS["lead_lag"] * value(first)
                + ANCHOR_ALLOCATIONS[eth_long_id] * value(second)
            )
            for first, second in zip(lead, eth_long, strict=True)
        ),
    }


eth_long_id = "event-eth_perp-to-eth_perp-continuation-60d-threshold-2p5-hold-12x4h-none-long_only"
btc_short_id = "event-btc_perp-to-btc_perp-continuation-15d-threshold-2-hold-4x4h-none-long_short"
btc_slow_id = (
    "event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-hold-12x4h-underreaction-long_short"
)


def _hedged_targets(
    raw: dict[str, tuple[Decimal, ...]],
    hedge_asset: str,
    beta: Decimal,
    hedge_ratio: Decimal,
    risk_scale: Decimal,
) -> dict[str, tuple[Decimal, ...]]:
    btc = []
    eth = []
    for btc_raw, eth_raw in zip(raw["btc_perp"], raw["eth_perp"], strict=True):
        btc_target = btc_raw
        eth_target = eth_raw
        if hedge_asset == "btc_perp":
            btc_target -= hedge_ratio * (btc_raw + beta * eth_raw)
        else:
            eth_target -= hedge_ratio * (eth_raw + btc_raw / beta)
        btc.append(risk_scale * btc_target)
        eth.append(risk_scale * eth_target)
    return {"btc_perp": tuple(btc), "eth_perp": tuple(eth)}


def _eligible(results: dict[str, FactorBookResult]) -> bool:
    return all(
        result.portfolio.net_return > 0
        and result.portfolio.max_drawdown >= Decimal("-0.35")
        and result.portfolio.positive_month_rate >= Decimal("0.5")
        and not result.portfolio.bankrupt
        for result in results.values()
    )


def _score(results: dict[str, FactorBookResult]) -> tuple[Decimal, ...]:
    discovery = results["discovery"].portfolio
    validation = results["validation"].portfolio
    return (
        min(discovery.target_month_rate, validation.target_month_rate),
        discovery.target_month_rate + validation.target_month_rate,
        min(discovery.positive_month_rate, validation.positive_month_rate),
        min(discovery.worst_month, validation.worst_month),
        min(discovery.net_return, validation.net_return),
        min(discovery.max_drawdown, validation.max_drawdown),
    )


def _report(
    bars: dict[str, list[ResearchBar]],
    rows: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    confirmation: FactorBookResult | None,
    stress: FactorBookResult | None,
) -> dict[str, Any]:
    base = confirmation.portfolio if confirmation else None
    stressed = stress.portfolio if stress else None
    achieved = bool(
        base
        and stressed
        and base.target_month_rate >= Decimal("0.5")
        and base.max_drawdown >= Decimal("-0.35")
        and base.net_return > 0
        and stressed.net_return > 0
        and stressed.max_drawdown >= Decimal("-0.35")
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "shared-equity beta-hedged four-factor BTC/ETH book",
        "data": {
            "first_bar": _timestamp(max(item[0].start_ms for item in bars.values())),
            "last_bar": _timestamp(min(item[-1].end_ms for item in bars.values())),
        },
        "execution": {
            "signal": "weighted frozen four-factor targets on closed 4h bars",
            "fill": "next 4h open in a shared mark-to-market equity account",
            "base_cost_bps": [float(BASE_FEE_BPS), float(BASE_SLIPPAGE_BPS)],
            "stress_cost_bps": [float(STRESS_FEE_BPS), float(STRESS_SLIPPAGE_BPS)],
            "historical_funding": True,
        },
        "selection": {
            "candidate_count": len(rows),
            "eligible_count": len(eligible),
            "confirmation_used_for_selection": False,
            "selected": (
                {
                    "hedge_asset": selected["hedge_asset"],
                    "beta": float(selected["beta"]),
                    "hedge_ratio": float(selected["hedge_ratio"]),
                    "risk_scale": float(selected["risk_scale"]),
                    **{name: result.as_dict() for name, result in selected["results"].items()},
                }
                if selected
                else None
            ),
        },
        "confirmation": confirmation.as_dict(include_daily=True) if confirmation else None,
        "stress_confirmation": stress.as_dict() if stress else None,
        "target": {"monthly_return": 0.25, "minimum_target_month_rate": 0.5, "achieved": achieved},
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The beta-hedged book met reused confirmation gates; fresh forward evidence "
                "remains required."
                if achieved
                else "The development-selected beta-hedged book failed monthly coverage, "
                "drawdown, or stress gates in reused confirmation."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The shared-equity replay assumes cross-margin netting without liquidation tiers.",
            "Beta is a fixed development-selected hedge coefficient, not a fitted 2026 value.",
            "Market impact, borrowing cost, and exchange failure are not modeled.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only shared-equity beta-hedged four-factor BTC/ETH book.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Eligible configurations: `{payload['selection']['eligible_count']}` / "
        f"`{payload['selection']['candidate_count']}`.",
    ]
    if selected:
        lines.extend(
            [
                f"Selected hedge asset `{selected['hedge_asset']}`, beta `{selected['beta']:.2f}`, "
                f"hedge ratio `{selected['hedge_ratio']:.0%}`, risk scale "
                f"`{selected['risk_scale']:.2f}x`.",
                "",
                "| Split | Return | Max DD | Positive months | 25% months |",
                "|---|---:|---:|---:|---:|",
                _metric_row("2021-2023 discovery", selected["discovery"]),
                _metric_row("2024-2025 validation", selected["validation"]),
            ]
        )
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
