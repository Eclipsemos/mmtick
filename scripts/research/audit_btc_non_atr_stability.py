#!/usr/bin/env python3
"""Audit whether common non-ATR BTC strategies have stable profitability evidence."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from explore_btc_strategy_families import Candidate, candidate_grid, load_market

from mastermind_tick.bar_research import (
    aggregate_bars,
    bollinger_reversion_targets,
    evaluate_targets,
    funding_by_bar,
    macd_targets,
)
from mastermind_tick.models import FundingRate

OUTPUT_DIR = Path("reports/experiments/btc_non_atr_stability/2026-08-14")
SPLITS = {
    "train": (date(2024, 2, 1), date(2024, 12, 31)),
    "validation": (date(2025, 1, 1), date(2025, 12, 31)),
    "confirmation": (date(2026, 1, 1), date(2026, 8, 10)),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    source_bars, funding_rates = load_market(args.database)
    intervals = (60, 240, 1440)
    bars_by_interval = {interval: aggregate_bars(source_bars, interval) for interval in intervals}
    funding_by_interval = {
        interval: funding_by_bar(bars, funding_rates) for interval, bars in bars_by_interval.items()
    }
    candidates = [
        *candidate_grid(bars_by_interval, funding_by_interval),
        *macd_candidates(bars_by_interval, funding_by_interval),
        *bollinger_candidates(bars_by_interval, funding_by_interval),
    ]
    periods = {name: (_day_start(start), _day_end(end)) for name, (start, end) in SPLITS.items()}
    evaluations = []
    for candidate in candidates:
        results = {name: evaluate(candidate, period) for name, period in periods.items()}
        evaluations.append(
            {
                "candidate": candidate,
                "results": results,
                "score": selection_score(results["train"], results["validation"]),
            }
        )

    family_winners = []
    for family in sorted({candidate.family for candidate in candidates}):
        family_rows = [row for row in evaluations if row["candidate"].family == family]
        winner = max(family_rows, key=lambda row: row["score"])
        family_winners.append(serialize_row(winner))

    strict_rows = [row for row in evaluations if strict_base_pass(row["results"])]
    stress_results = {}
    for row in strict_rows:
        stress = {
            name: evaluate(candidate=row["candidate"], period=period, fee_bps=10, slippage_bps=5)
            for name, period in periods.items()
        }
        stress_results[row["candidate"].id] = {
            name: summary(result) for name, result in stress.items()
        }
    stable_rows = [row for row in strict_rows if stress_pass(stress_results[row["candidate"].id])]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTCUSDT common non-ATR strategy stability audit",
        "data": {
            "source_bars_15m": len(source_bars),
            "first_bar": timestamp(source_bars[0].start_ms),
            "last_bar": timestamp(source_bars[-1].end_ms),
            "funding_events": len(funding_rates),
        },
        "execution": {
            "signal_timing": "closed bar",
            "fill_timing": "next bar open",
            "base_fee_bps_per_fill": 5,
            "base_slippage_bps_per_fill": 2,
            "funding": "historical Binance funding while positioned",
            "base_exposure": 1.0,
            "liquidation_modeled": False,
        },
        "splits": {
            name: {"start": timestamp(start), "end": timestamp(end)}
            for name, (start, end) in periods.items()
        },
        "families": [
            "EMA trend and deadband",
            "MACD trend following",
            "Donchian breakout",
            "time-series momentum",
            "RSI mean reversion",
            "Bollinger-band mean reversion",
        ],
        "candidate_count": len(candidates),
        "selection_rule": (
            "Within each family, select on train and validation only: maximize the weaker return, "
            "then combined return, then lower drawdown. Confirmation is not used for selection."
        ),
        "stability_gates": {
            "base": [
                "positive net return in each disjoint split",
                "at least six completed trades in confirmation",
                "maximum drawdown no worse than -25% in every split",
                "at least 55% positive calendar months in confirmation",
            ],
            "stress": [
                "repeat all three splits at 10 bps fee plus 5 bps slippage per fill",
                "remain positive in each split with no drawdown worse than -25%",
            ],
        },
        "family_winners": family_winners,
        "strict_base_candidates": [serialize_row(row) for row in strict_rows],
        "cost_stress": stress_results,
        "stable_candidates": [serialize_row(row) for row in stable_rows],
        "decision": {
            "status": "no_stable_candidate" if not stable_rows else "research_candidate",
            "approved_for_trading": False,
            "reason": (
                "No candidate survived the predefined multi-split, drawdown, trade-count, "
                "monthly-consistency, and cost-stress gates."
                if not stable_rows
                else "Candidates are exploratory and require genuinely new forward evidence."
            ),
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False))
    print(args.output_dir / "results.json")
    print(args.output_dir / "README.md")


def macd_candidates(
    bars_by_interval: dict[int, list],
    funding_by_interval: dict[int, list[list[FundingRate]]],
) -> list[Candidate]:
    periods = {
        60: ((12, 26, 9), (24, 52, 18), (48, 104, 36)),
        240: ((6, 13, 5), (12, 26, 9), (24, 52, 18)),
        1440: ((6, 13, 5), (12, 26, 9), (24, 52, 18)),
    }
    candidates = []
    for interval, parameter_sets in periods.items():
        for fast, slow, signal in parameter_sets:
            for direction in ("long_only", "long_short"):
                candidates.append(
                    Candidate(
                        id=f"macd-{interval}m-{fast}-{slow}-{signal}-{direction}",
                        family="macd_trend",
                        interval_minutes=interval,
                        direction=direction,
                        parameters={
                            "fast_period": fast,
                            "slow_period": slow,
                            "signal_period": signal,
                        },
                        bars=bars_by_interval[interval],
                        funding=funding_by_interval[interval],
                        targets=macd_targets(
                            bars_by_interval[interval], fast, slow, signal, direction
                        ),
                    )
                )
    return candidates


def bollinger_candidates(
    bars_by_interval: dict[int, list],
    funding_by_interval: dict[int, list[list[FundingRate]]],
) -> list[Candidate]:
    parameter_sets = ((20, 1.5), (20, 2.0), (50, 2.0))
    candidates = []
    for interval in (60, 240, 1440):
        for period, standard_deviations in parameter_sets:
            for direction in ("long_only", "long_short"):
                candidates.append(
                    Candidate(
                        id=(f"bollinger-{interval}m-{period}-{standard_deviations:g}-{direction}"),
                        family="bollinger_reversion",
                        interval_minutes=interval,
                        direction=direction,
                        parameters={
                            "period": period,
                            "standard_deviations": standard_deviations,
                        },
                        bars=bars_by_interval[interval],
                        funding=funding_by_interval[interval],
                        targets=bollinger_reversion_targets(
                            bars_by_interval[interval], period, standard_deviations, direction
                        ),
                    )
                )
    return candidates


def evaluate(candidate: Candidate, period: tuple[int, int], **costs: int):
    return evaluate_targets(
        candidate.bars,
        candidate.targets,
        start_ms=period[0],
        end_ms=period[1],
        funding=candidate.funding,
        fee_bps=Decimal(str(costs.get("fee_bps", 5))),
        slippage_bps=Decimal(str(costs.get("slippage_bps", 2))),
    )


def selection_score(train, validation) -> tuple[float, float, float]:
    return (
        min(train.net_return, validation.net_return),
        train.net_return + validation.net_return,
        min(train.max_drawdown, validation.max_drawdown),
    )


def strict_base_pass(results: dict[str, Any]) -> bool:
    split_passes = (
        result.net_return > 0 and result.max_drawdown >= -0.25 for result in results.values()
    )
    if not all(split_passes):
        return False
    confirmation = results["confirmation"]
    positive_months = sum(value > 0 for _month, value in confirmation.monthly_returns)
    return (
        confirmation.completed_trades >= 6
        and positive_months / len(confirmation.monthly_returns) >= 0.55
    )


def stress_pass(results: dict[str, dict[str, Any]]) -> bool:
    return all(
        result["net_return"] > 0 and result["max_drawdown"] >= -0.25 for result in results.values()
    )


def summary(result) -> dict[str, Any]:
    data = asdict(result)
    data.pop("trades")
    data["daily_returns"] = [
        {"date": label, "return": value} for label, value in result.daily_returns
    ]
    data["monthly_returns"] = [
        {"month": label, "return": value} for label, value in result.monthly_returns
    ]
    data["positive_month_rate"] = (
        sum(value > 0 for _label, value in result.monthly_returns) / len(result.monthly_returns)
        if result.monthly_returns
        else 0.0
    )
    return data


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    candidate = row["candidate"]
    return {
        "id": candidate.id,
        "family": candidate.family,
        "interval_minutes": candidate.interval_minutes,
        "direction": candidate.direction,
        "parameters": candidate.parameters,
        "selection_score": list(row["score"]),
        **{name: summary(result) for name, result in row["results"].items()},
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# BTCUSDT Non-ATR Stability Audit",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        (
            "This audit compares common non-ATR signal families with closed-bar signals and "
            "next-bar-open fills. All base results use 5 bps fee and 2 bps slippage per fill, "
            "plus historical funding. It does not model liquidation."
        ),
        "",
        f"Data: {payload['data']['first_bar']} through {payload['data']['last_bar']}; "
        f"{payload['data']['source_bars_15m']:,} complete 15-minute bars.",
        "",
        "## Families",
        "",
        *[f"- {family}" for family in payload["families"]],
        "",
        "## Stability Gates",
        "",
        *[f"- {gate}" for gate in payload["stability_gates"]["base"]],
        *[f"- {gate}" for gate in payload["stability_gates"]["stress"]],
        "",
        "## Family Winners",
        "",
        "Family winners are selected using training and validation only. Confirmation is held out "
        "from that selection, but this remains exploratory research because the strategy families "
        "and grids were evaluated on the archived dataset.",
        "",
        (
            "| Family | Candidate | Train | Validation | Confirmation | Confirm DD | "
            "Trades | Positive months |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["family_winners"]:
        confirmation = row["confirmation"]
        lines.append(
            f"| {row['family']} | `{row['id']}` | {row['train']['net_return']:.2%} | "
            f"{row['validation']['net_return']:.2%} | {confirmation['net_return']:.2%} | "
            f"{confirmation['max_drawdown']:.2%} | {confirmation['completed_trades']} | "
            f"{confirmation['positive_month_rate']:.0%} |"
        )
    lines.extend(
        [
            "",
            "## Outcome",
            "",
            f"Status: `{payload['decision']['status']}`.",
            "",
            payload["decision"]["reason"],
            "",
        ]
    )
    if payload["strict_base_candidates"]:
        lines.extend(
            [
                "Candidates that met base gates but did not necessarily survive cost stress:",
                "",
                "| Candidate | Train | Validation | Confirmation | Confirmation DD |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in payload["strict_base_candidates"]:
            lines.append(
                f"| `{row['id']}` | {row['train']['net_return']:.2%} | "
                f"{row['validation']['net_return']:.2%} | "
                f"{row['confirmation']['net_return']:.2%} | "
                f"{row['confirmation']['max_drawdown']:.2%} |"
            )
        lines.append("")
    else:
        lines.extend(
            [
                "No candidate passed even the base gates, so no strategy advanced to a cost-stress "
                "approval test.",
                "",
            ]
        )
    lines.extend(
        [
            "The result is not a trading approval. The existing daily EMA(10,50) lead is excluded "
            "by the stability gates because it has only five confirmation trades and historical "
            "drawdown beyond the -25% limit.",
            "",
        ]
    )
    return "\n".join(lines)


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
