#!/usr/bin/env python3
"""Search BTC/ETH bar-strategy sleeves around the frozen four-factor anchor."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from explore_btc_strategy_families import Candidate, candidate_grid
from mine_factor_portfolio import (
    BASE_FEE_BPS,
    BASE_SLIPPAGE_BPS,
    CONFIRMATION,
    DISCOVERY,
    STRESS_FEE_BPS,
    STRESS_SLIPPAGE_BPS,
    VALIDATION,
)
from train_walk_forward_factor import _anchor_context, _evaluate_anchor

from mastermind_tick.bar_research import aggregate_bars, evaluate_targets, funding_by_bar
from mastermind_tick.factor_mining import load_market
from mastermind_tick.factor_portfolio import (
    PortfolioResult,
    decimal_returns,
    evaluate_static_portfolio,
)

ASSETS = ("btc_perp", "eth_perp")
INTERVALS = (60, 240, 1440)
ANCHOR_WEIGHTS = tuple(Decimal(value) for value in ("0.25", "0.4", "0.5", "0.6", "0.75", "0.9"))
HYBRID_LEVERAGES = tuple(
    Decimal(value) for value in ("0.5", "0.75", "1", "1.25", "1.5", "1.75", "2", "2.25", "2.5")
)
SHORTLIST_SIZE = 100
TARGET_MONTHLY_RETURN = Decimal("0.15")
MIN_DEVELOPMENT_TARGET_RATE = Decimal("0.15")
MIN_CONFIRMATION_TARGET_RATE = Decimal("0.5")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/bar_factor_hybrid/2026-08-15"),
    )
    args = parser.parse_args()

    print("loading BTC/ETH bars and frozen anchor", flush=True)
    loaded = {asset: load_market(args.database, asset) for asset in ASSETS}
    bars_4h = {asset: aggregate_bars(loaded[asset][0], 240) for asset in ASSETS}
    anchor = _anchor_context(bars_4h, loaded)
    libraries = _candidate_libraries(loaded)
    periods = {"discovery": DISCOVERY, "validation": VALIDATION}
    anchor_results = {
        name: _evaluate_anchor(anchor, period, stress=False) for name, period in periods.items()
    }

    candidates = tuple(candidate for library in libraries.values() for candidate in library)
    print(f"evaluating {len(candidates)} bar-strategy sleeves", flush=True)
    candidate_rows = []
    for candidate in candidates:
        results = {
            name: _evaluate_candidate(candidate, period, stress=False)
            for name, period in periods.items()
        }
        candidate_rows.append(
            {
                "candidate": candidate,
                "results": results,
                "score": _candidate_score(results),
            }
        )
    candidate_eligible = [row for row in candidate_rows if _candidate_eligible(row["results"])]
    shortlist = sorted(candidate_eligible, key=lambda row: row["score"], reverse=True)[
        :SHORTLIST_SIZE
    ]

    print(f"searching hybrids for {len(shortlist)} development sleeves", flush=True)
    hybrid_rows = []
    for row in shortlist:
        for anchor_weight in ANCHOR_WEIGHTS:
            allocations = {"anchor": anchor_weight, "bar_factor": Decimal("1") - anchor_weight}
            for leverage in HYBRID_LEVERAGES:
                results = {
                    name: evaluate_static_portfolio(
                        {
                            "anchor": anchor_results[name].daily_returns,
                            "bar_factor": decimal_returns(row["results"][name].daily_returns),
                        },
                        allocations,
                        leverage=leverage,
                    )
                    for name in periods
                }
                if _hybrid_eligible(results):
                    hybrid_rows.append(
                        {
                            "candidate": row["candidate"],
                            "anchor_weight": anchor_weight,
                            "leverage": leverage,
                            "results": results,
                            "score": _hybrid_score(results),
                        }
                    )
    ranked = sorted(hybrid_rows, key=lambda row: row["score"], reverse=True)
    selected = ranked[0] if ranked else None
    confirmation = _confirm(selected, anchor, stress=False)
    stress = _confirm(selected, anchor, stress=True)
    confirmation_diagnostics = []
    for row in ranked:
        base_result = _confirm(row, anchor, stress=False)
        stress_result = _confirm(row, anchor, stress=True)
        if base_result is None or stress_result is None:
            continue
        confirmation_diagnostics.append(
            {
                "candidate": _candidate_payload(row["candidate"]),
                "anchor_weight": float(row["anchor_weight"]),
                "outer_leverage": float(row["leverage"]),
                "base": _public_result(base_result),
                "stress": _public_result(stress_result),
                "meets_confirmation_gates": _confirmation_eligible(base_result, stress_result),
            }
        )
    payload = _report(
        bars_4h,
        candidates,
        candidate_eligible,
        shortlist,
        ranked,
        selected,
        confirmation,
        stress,
        confirmation_diagnostics,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"bar-factor-hybrid-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}"
    payload["id"] = report_id
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(markdown_path, flush=True)


def _candidate_libraries(
    loaded: dict[str, tuple[list[Any], list[Any]]],
) -> dict[str, list[Candidate]]:
    result = {}
    for asset, (source_bars, funding_rates) in loaded.items():
        bars_by_interval = {
            interval: aggregate_bars(source_bars, interval) for interval in INTERVALS
        }
        funding_by_interval = {
            interval: funding_by_bar(bars_by_interval[interval], funding_rates)
            for interval in INTERVALS
        }
        result[asset] = [
            replace(candidate, id=f"{asset}-{candidate.id}")
            for candidate in candidate_grid(bars_by_interval, funding_by_interval)
        ]
    return result


def _evaluate_candidate(
    candidate: Candidate,
    period: tuple[int, int],
    *,
    stress: bool,
) -> Any:
    return evaluate_targets(
        candidate.bars,
        candidate.targets,
        start_ms=period[0],
        end_ms=period[1],
        funding=candidate.funding,
        fee_bps=STRESS_FEE_BPS if stress else BASE_FEE_BPS,
        slippage_bps=STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS,
    )


def _candidate_eligible(results: dict[str, Any]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= -0.50
        and result.completed_trades >= 12
        and not result.bankrupt
        for result in results.values()
    )


def _candidate_score(results: dict[str, Any]) -> tuple[Decimal, ...]:
    summaries = {name: _research_summary(result) for name, result in results.items()}
    discovery = summaries["discovery"]
    validation = summaries["validation"]
    return (
        min(discovery["target_month_rate"], validation["target_month_rate"]),
        discovery["target_month_rate"] + validation["target_month_rate"],
        min(discovery["positive_month_rate"], validation["positive_month_rate"]),
        min(
            Decimal(str(results["discovery"].net_return)),
            Decimal(str(results["validation"].net_return)),
        ),
        min(
            Decimal(str(results["discovery"].max_drawdown)),
            Decimal(str(results["validation"].max_drawdown)),
        ),
    )


def _hybrid_eligible(results: dict[str, PortfolioResult]) -> bool:
    return all(
        result.net_return > 0
        and result.max_drawdown >= Decimal("-0.35")
        and result.positive_month_rate >= Decimal("0.5")
        and _target_month_rate(result) >= MIN_DEVELOPMENT_TARGET_RATE
        and not result.bankrupt
        for result in results.values()
    )


def _hybrid_score(results: dict[str, PortfolioResult]) -> tuple[Decimal, ...]:
    discovery = results["discovery"]
    validation = results["validation"]
    return (
        min(_target_month_rate(discovery), _target_month_rate(validation)),
        _target_month_rate(discovery) + _target_month_rate(validation),
        min(discovery.positive_month_rate, validation.positive_month_rate),
        min(discovery.worst_month, validation.worst_month),
        min(discovery.net_return, validation.net_return),
        min(discovery.max_drawdown, validation.max_drawdown),
    )


def _research_summary(result: Any) -> dict[str, Decimal]:
    monthly = tuple(Decimal(str(value)) for _label, value in result.monthly_returns)
    if not monthly:
        return {"positive_month_rate": Decimal("0"), "target_month_rate": Decimal("0")}
    return {
        "positive_month_rate": Decimal(sum(value > 0 for value in monthly)) / Decimal(len(monthly)),
        "target_month_rate": Decimal(sum(value >= TARGET_MONTHLY_RETURN for value in monthly))
        / Decimal(len(monthly)),
    }


def _target_month_rate(result: PortfolioResult) -> Decimal:
    if not result.monthly_returns:
        return Decimal("0")
    return Decimal(
        sum(value >= TARGET_MONTHLY_RETURN for _label, value in result.monthly_returns)
    ) / Decimal(len(result.monthly_returns))


def _confirm(
    selected: dict[str, Any] | None,
    anchor: dict[str, Any],
    *,
    stress: bool,
) -> PortfolioResult | None:
    if selected is None:
        return None
    candidate_result = _evaluate_candidate(selected["candidate"], CONFIRMATION, stress=stress)
    anchor_result = _evaluate_anchor(anchor, CONFIRMATION, stress=stress)
    return evaluate_static_portfolio(
        {
            "anchor": anchor_result.daily_returns,
            "bar_factor": decimal_returns(candidate_result.daily_returns),
        },
        {
            "anchor": selected["anchor_weight"],
            "bar_factor": Decimal("1") - selected["anchor_weight"],
        },
        leverage=selected["leverage"],
    )


def _candidate_payload(candidate: Candidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "family": candidate.family,
        "instrument_id": candidate.id.split("-", 1)[0],
        "interval_minutes": candidate.interval_minutes,
        "direction": candidate.direction,
        "parameters": candidate.parameters,
    }


def _public_result(result: PortfolioResult, *, include_daily: bool = False) -> dict[str, Any]:
    payload = result.as_dict(include_daily=include_daily)
    payload["target_15pct_month_rate"] = float(_target_month_rate(result))
    return payload


def _confirmation_eligible(base: PortfolioResult, stress: PortfolioResult) -> bool:
    return bool(
        _target_month_rate(base) >= MIN_CONFIRMATION_TARGET_RATE
        and _target_month_rate(stress) >= MIN_CONFIRMATION_TARGET_RATE
        and base.net_return > 0
        and stress.net_return > 0
        and base.max_drawdown >= Decimal("-0.35")
        and stress.max_drawdown >= Decimal("-0.35")
    )


def _report(
    bars: dict[str, list[Any]],
    candidates: tuple[Candidate, ...],
    candidate_eligible: list[dict[str, Any]],
    shortlist: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    confirmation: PortfolioResult | None,
    stress: PortfolioResult | None,
    confirmation_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    achieved = bool(confirmation and stress and _confirmation_eligible(confirmation, stress))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTC/ETH bar-strategy sleeve plus frozen four-factor anchor",
        "data": {
            asset: {
                "first": _timestamp(series[0].start_ms),
                "last": _timestamp(series[-1].end_ms),
                "bars_4h": len(series),
            }
            for asset, series in bars.items()
        },
        "execution": {
            "signal": "closed 1h, 4h, or 1d bar",
            "fill": "next strategy-bar open",
            "base_fee_bps": float(BASE_FEE_BPS),
            "base_slippage_bps": float(BASE_SLIPPAGE_BPS),
            "stress_fee_bps": float(STRESS_FEE_BPS),
            "stress_slippage_bps": float(STRESS_SLIPPAGE_BPS),
            "historical_funding": True,
            "trading_integration": False,
        },
        "selection": {
            "candidate_count": len(candidates),
            "development_eligible_candidate_count": len(candidate_eligible),
            "shortlist_size": len(shortlist),
            "development_eligible_hybrid_count": len(ranked),
            "minimum_development_target_rate": float(MIN_DEVELOPMENT_TARGET_RATE),
            "confirmation_used_for_selection": False,
            "selected": (
                {
                    "candidate": _candidate_payload(selected["candidate"]),
                    "anchor_weight": float(selected["anchor_weight"]),
                    "bar_factor_weight": float(Decimal("1") - selected["anchor_weight"]),
                    "outer_leverage": float(selected["leverage"]),
                    "discovery": _public_result(selected["results"]["discovery"]),
                    "validation": _public_result(selected["results"]["validation"]),
                }
                if selected
                else None
            ),
            "top_development_hybrids": [
                {
                    "candidate": _candidate_payload(row["candidate"]),
                    "anchor_weight": float(row["anchor_weight"]),
                    "outer_leverage": float(row["leverage"]),
                    "score": [float(value) for value in row["score"]],
                }
                for row in ranked[:20]
            ],
        },
        "confirmation": _public_result(confirmation, include_daily=True) if confirmation else None,
        "stress_confirmation": _public_result(stress) if stress else None,
        "confirmation_neighborhood_diagnostic": {
            "used_for_selection": False,
            "configuration_count": len(confirmation_diagnostics),
            "meeting_gate_count": sum(
                row["meets_confirmation_gates"] for row in confirmation_diagnostics
            ),
            "configurations": confirmation_diagnostics,
        },
        "target": {
            "monthly_return": float(TARGET_MONTHLY_RETURN),
            "minimum_target_month_rate": float(MIN_CONFIRMATION_TARGET_RATE),
            "achieved": achieved,
        },
        "decision": {
            "status": "research_candidate" if achieved else "rejected_after_confirmation",
            "approved_for_trading": False,
            "reason": (
                "The development-selected bar-factor hybrid met reused base and stress "
                "confirmation gates; fresh forward evidence remains required."
                if achieved
                else "The development-selected bar-factor hybrid failed base or stress monthly "
                "coverage, return, or drawdown gates."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence and is not a fresh holdout.",
            "The four-factor anchor was selected in earlier studies using overlapping history.",
            "The search covers a finite hand-written bar-strategy grid, not all possible rules.",
            "Sleeves use fixed initial capital and do not model shared-margin liquidation.",
            "Drawdown is measured at daily closes; borrowing cost and market impact are omitted.",
        ],
    }


def _markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    confirmation = payload["confirmation"]
    stress = payload["stress_confirmation"]
    lines = [
        f"# {payload['id']}",
        "",
        "Research-only BTC/ETH bar-strategy sleeve around the frozen factor anchor.",
        "",
        f"Decision: `{payload['decision']['status']}`. Trading approval: `false`.",
        f"Eligible bar candidates: "
        f"`{payload['selection']['development_eligible_candidate_count']}` / "
        f"`{payload['selection']['candidate_count']}`; eligible hybrids: "
        f"`{payload['selection']['development_eligible_hybrid_count']}`.",
        f"Non-selective confirmation diagnostic: "
        f"`{payload['confirmation_neighborhood_diagnostic']['meeting_gate_count']}` / "
        f"`{payload['confirmation_neighborhood_diagnostic']['configuration_count']}` met gates.",
    ]
    if selected:
        candidate = selected["candidate"]
        lines.extend(
            [
                f"Selected `{candidate['id']}` with `{selected['anchor_weight']:.0%}` anchor, "
                f"`{selected['bar_factor_weight']:.0%}` bar sleeve, and "
                f"`{selected['outer_leverage']:.2f}x` outer leverage.",
                "",
                "| Split | Return | Max DD | Positive months | 15% months |",
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
    reached = sum(
        row["return"] >= float(TARGET_MONTHLY_RETURN) for row in result["monthly_returns"]
    )
    return (
        f"| {label} | {result['net_return']:.2%} | {result['max_drawdown']:.2%} | "
        f"{result['positive_month_rate']:.2%} | {reached}/{len(result['monthly_returns'])} |"
    )


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
