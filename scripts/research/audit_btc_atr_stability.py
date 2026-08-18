#!/usr/bin/env python3
"""Compare structurally different BTC ATR strategies for stability."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from explore_btc_strategy_families import Candidate, load_market

from mastermind_tick.bar_research import (
    ResearchResult,
    aggregate_bars,
    atr_mean_reversion_targets,
    atr_trailing_stop_targets,
    chandelier_breakout_targets,
    evaluate_targets,
    funding_by_bar,
    keltner_breakout_targets,
)
from mastermind_tick.models import FundingRate

OUTPUT_DIR = Path("reports/experiments/btc_atr/2026-08-14-stability")
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
    periods = {name: (_day_start(start), _day_end(end)) for name, (start, end) in SPLITS.items()}
    candidates = candidate_grid(bars_by_interval, funding_by_interval)
    rows = []
    for candidate in candidates:
        results = {name: evaluate(candidate, period) for name, period in periods.items()}
        rows.append(
            {
                "candidate": candidate,
                "results": results,
                "score": selection_score(results["train"], results["validation"]),
            }
        )

    winners = []
    for family in sorted({candidate.family for candidate in candidates}):
        family_rows = [row for row in rows if row["candidate"].family == family]
        ordered = sorted(family_rows, key=lambda row: row["score"], reverse=True)
        winner = ordered[0]
        neighbors = ordered[: min(5, len(ordered))]
        neighbor_pass_rate = sum(
            neighbor["results"]["confirmation"].net_return > 0
            and neighbor["results"]["confirmation"].max_drawdown >= -0.25
            for neighbor in neighbors
        ) / len(neighbors)
        stress = {
            name: evaluate(candidate=winner["candidate"], period=period, fee_bps=10, slippage_bps=5)
            for name, period in periods.items()
        }
        gates = stability_gates(winner["results"], stress, neighbor_pass_rate)
        winners.append(
            {
                **serialize_row(winner),
                "development_top_five_confirmation_pass_rate": neighbor_pass_rate,
                "stress": {name: summary(result) for name, result in stress.items()},
                "gates": gates,
                "stable": all(gates.values()),
            }
        )

    ex_post_base_pass = [serialize_row(row) for row in rows if base_gates(row["results"])]
    stable_winners = [winner for winner in winners if winner["stable"]]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTCUSDT structurally diverse ATR strategy stability audit",
        "data": {
            "source_bars_15m": len(source_bars),
            "first_bar": timestamp(source_bars[0].start_ms),
            "last_bar": timestamp(source_bars[-1].end_ms),
            "funding_events": len(funding_rates),
        },
        "execution": {
            "signal_timing": "closed 1h, 4h, or 1d bar",
            "fill_timing": "next bar open",
            "base_fee_bps_per_fill": 5,
            "base_slippage_bps_per_fill": 2,
            "stress_fee_bps_per_fill": 10,
            "stress_slippage_bps_per_fill": 5,
            "funding": "historical Binance funding while positioned",
            "exposure": 1.0,
            "liquidation_modeled": False,
        },
        "splits": {
            name: {"start": timestamp(start), "end": timestamp(end)}
            for name, (start, end) in periods.items()
        },
        "families": {
            "atr_trailing_stop": "close-based Wilder ATR adaptive trend stop",
            "keltner_breakout": "EMA plus ATR channel breakout; exit through EMA",
            "atr_mean_reversion": "fade close distance from rolling mean measured in ATR",
            "chandelier_breakout": "prior-channel entry with Chandelier ATR trailing exit",
        },
        "candidate_count": len(candidates),
        "selection": {
            "confirmation_used_for_family_winner_selection": False,
            "rule": (
                "within each family maximize weaker train/validation return, then combined "
                "return, then drawdown"
            ),
            "sequential_research_warning": (
                "ATR hypotheses were created after the archive had already been inspected. "
                "The 2026 segment is diagnostic confirmation, not pristine unseen evidence."
            ),
        },
        "stability_gates": {
            "all_splits_positive": "positive net return in train, validation, and confirmation",
            "drawdown_controlled": "maximum drawdown no worse than -25% in every base split",
            "confirmation_trades": "at least six completed confirmation trades",
            "confirmation_months": "at least 55% positive confirmation calendar months",
            "parameter_neighborhood": (
                "at least 60% of the family's top five development candidates have positive "
                "confirmation return and drawdown no worse than -25%"
            ),
            "cost_stress": (
                "all splits remain positive with drawdown no worse than -25% at 10 bps fee "
                "plus 5 bps slippage per fill"
            ),
        },
        "family_winners": winners,
        "ex_post_base_pass_candidates": ex_post_base_pass,
        "stable_family_winners": stable_winners,
        "prior_tick_baseline": {
            "description": "15m Tick ATR reversal, periods 14/21/28 x multipliers 2/2.5/3",
            "development_winner": "ATR(14) x 3.0",
            "development_return": 0.0064,
            "july_return": -0.0576,
            "august_1_to_10_return": -0.0123,
            "status": "rejected",
        },
        "decision": {
            "status": "no_stable_candidate" if not stable_winners else "research_candidate",
            "approved_for_trading": False,
            "reason": (
                "No development-selected ATR family winner passed every stability gate."
                if not stable_winners
                else "A diagnostic candidate passed, but new frozen forward evidence is required."
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


def candidate_grid(
    bars_by_interval: dict[int, list],
    funding_by_interval: dict[int, list[list[FundingRate]]],
) -> list[Candidate]:
    candidates = []
    for interval in (60, 240, 1440):
        bars = bars_by_interval[interval]
        funding = funding_by_interval[interval]
        for direction in ("long_only", "long_short"):
            for period in (14, 21, 28):
                for multiplier in (2.0, 3.0, 4.0):
                    candidates.append(
                        Candidate(
                            id=f"atr-stop-{interval}m-{period}-{multiplier:g}-{direction}",
                            family="atr_trailing_stop",
                            interval_minutes=interval,
                            direction=direction,
                            parameters={"atr_period": period, "multiplier": multiplier},
                            bars=bars,
                            funding=funding,
                            targets=atr_trailing_stop_targets(bars, period, multiplier, direction),
                        )
                    )
            for center, atr_period in ((20, 14), (50, 14), (50, 28)):
                for multiplier in (1.5, 2.0, 2.5):
                    candidates.append(
                        Candidate(
                            id=(
                                f"keltner-{interval}m-{center}-{atr_period}-"
                                f"{multiplier:g}-{direction}"
                            ),
                            family="keltner_breakout",
                            interval_minutes=interval,
                            direction=direction,
                            parameters={
                                "ema_period": center,
                                "atr_period": atr_period,
                                "multiplier": multiplier,
                            },
                            bars=bars,
                            funding=funding,
                            targets=keltner_breakout_targets(
                                bars, center, atr_period, multiplier, direction
                            ),
                        )
                    )
                for entry_distance in (1.0, 1.5, 2.0):
                    candidates.append(
                        Candidate(
                            id=(
                                f"atr-mean-{interval}m-{center}-{atr_period}-"
                                f"{entry_distance:g}-{direction}"
                            ),
                            family="atr_mean_reversion",
                            interval_minutes=interval,
                            direction=direction,
                            parameters={
                                "center_period": center,
                                "atr_period": atr_period,
                                "entry_distance_atr": entry_distance,
                            },
                            bars=bars,
                            funding=funding,
                            targets=atr_mean_reversion_targets(
                                bars, center, atr_period, entry_distance, direction
                            ),
                        )
                    )
            for entry_window, atr_period in ((20, 14), (55, 14), (55, 28)):
                for exit_multiplier in (2.0, 3.0, 4.0):
                    candidates.append(
                        Candidate(
                            id=(
                                f"chandelier-{interval}m-{entry_window}-{atr_period}-"
                                f"{exit_multiplier:g}-{direction}"
                            ),
                            family="chandelier_breakout",
                            interval_minutes=interval,
                            direction=direction,
                            parameters={
                                "entry_window": entry_window,
                                "atr_period": atr_period,
                                "exit_multiplier": exit_multiplier,
                            },
                            bars=bars,
                            funding=funding,
                            targets=chandelier_breakout_targets(
                                bars, entry_window, atr_period, exit_multiplier, direction
                            ),
                        )
                    )
    return candidates


def evaluate(
    candidate: Candidate,
    period: tuple[int, int],
    *,
    fee_bps: int = 5,
    slippage_bps: int = 2,
) -> ResearchResult:
    return evaluate_targets(
        candidate.bars,
        candidate.targets,
        start_ms=period[0],
        end_ms=period[1],
        funding=candidate.funding,
        fee_bps=Decimal(fee_bps),
        slippage_bps=Decimal(slippage_bps),
    )


def selection_score(
    train: ResearchResult, validation: ResearchResult
) -> tuple[float, float, float]:
    return (
        min(train.net_return, validation.net_return),
        train.net_return + validation.net_return,
        min(train.max_drawdown, validation.max_drawdown),
    )


def base_gates(results: dict[str, ResearchResult]) -> bool:
    positive = all(result.net_return > 0 for result in results.values())
    drawdown = all(result.max_drawdown >= -0.25 for result in results.values())
    confirmation = results["confirmation"]
    positive_month_rate = sum(value > 0 for _, value in confirmation.monthly_returns) / len(
        confirmation.monthly_returns
    )
    return (
        positive and drawdown and confirmation.completed_trades >= 6 and positive_month_rate >= 0.55
    )


def stability_gates(
    results: dict[str, ResearchResult],
    stress: dict[str, ResearchResult],
    neighbor_pass_rate: float,
) -> dict[str, bool]:
    confirmation = results["confirmation"]
    positive_month_rate = sum(value > 0 for _, value in confirmation.monthly_returns) / len(
        confirmation.monthly_returns
    )
    return {
        "all_splits_positive": all(result.net_return > 0 for result in results.values()),
        "drawdown_controlled": all(result.max_drawdown >= -0.25 for result in results.values()),
        "confirmation_trades": confirmation.completed_trades >= 6,
        "confirmation_months": positive_month_rate >= 0.55,
        "parameter_neighborhood": neighbor_pass_rate >= 0.60,
        "cost_stress": all(
            result.net_return > 0 and result.max_drawdown >= -0.25 for result in stress.values()
        ),
    }


def summary(result: ResearchResult) -> dict[str, Any]:
    payload = asdict(result)
    payload.pop("trades")
    payload["daily_returns"] = [
        {"date": label, "return": value} for label, value in result.daily_returns
    ]
    payload["monthly_returns"] = [
        {"month": label, "return": value} for label, value in result.monthly_returns
    ]
    payload["positive_month_rate"] = sum(
        value > 0 for _label, value in result.monthly_returns
    ) / len(result.monthly_returns)
    return payload


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
        "# BTCUSDT ATR Strategy Stability Audit",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        (
            "This study compares structurally different ATR strategies rather than only changing "
            "one trailing-stop period and multiplier. Signals use closed 1h, 4h, or daily bars "
            "and fill at the next bar open. Base results include 5 bps fee, 2 bps slippage, and "
            "historical funding at 1.0x exposure."
        ),
        "",
        f"Data: {payload['data']['first_bar']} through {payload['data']['last_bar']}; "
        f"{payload['data']['source_bars_15m']:,} complete source bars.",
        "",
        "## ATR Families",
        "",
        *[f"- `{name}`: {description}." for name, description in payload["families"].items()],
        "",
        "## Family Winners",
        "",
        (
            "Each winner is selected on 2024 training and 2025 validation only. The 2026 segment "
            "is then shown as confirmation."
        ),
        "",
        (
            "| Family | Candidate | Train | Validation | Confirmation | Confirm DD | Trades | "
            "Positive months | Neighbor pass | Stable |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for winner in payload["family_winners"]:
        confirmation = winner["confirmation"]
        lines.append(
            f"| {winner['family']} | `{winner['id']}` | {winner['train']['net_return']:.2%} | "
            f"{winner['validation']['net_return']:.2%} | "
            f"{confirmation['net_return']:.2%} | {confirmation['max_drawdown']:.2%} | "
            f"{confirmation['completed_trades']} | {confirmation['positive_month_rate']:.0%} | "
            f"{winner['development_top_five_confirmation_pass_rate']:.0%} | "
            f"{'yes' if winner['stable'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## Gate Detail",
            "",
            "| Family | Failed gates | Stress confirmation | Stress DD |",
            "|---|---|---:|---:|",
        ]
    )
    for winner in payload["family_winners"]:
        failed = ", ".join(name for name, passed in winner["gates"].items() if not passed)
        stress = winner["stress"]["confirmation"]
        lines.append(
            f"| {winner['family']} | {failed or 'none'} | {stress['net_return']:.2%} | "
            f"{stress['max_drawdown']:.2%} |"
        )
    baseline = payload["prior_tick_baseline"]
    lines.extend(
        [
            "",
            "## Prior Tick Baseline",
            "",
            (
                f"The earlier `{baseline['description']}` study selected "
                f"`{baseline['development_winner']}` at {baseline['development_return']:.2%} "
                f"development return, then lost {baseline['july_return']:.2%} in July and "
                f"{baseline['august_1_to_10_return']:.2%} during August 1-10. It remains rejected."
            ),
            "",
            "## Decision",
            "",
            f"Status: `{payload['decision']['status']}`.",
            "",
            payload["decision"]["reason"],
            "",
            (
                f"An ex-post scan found {len(payload['ex_post_base_pass_candidates'])} of "
                f"{payload['candidate_count']} candidates that met the base gates. This count is "
                "diagnostic and cannot override the development-selected family-winner protocol."
            ),
            "",
            payload["selection"]["sequential_research_warning"],
            "",
            "No result in this report is approved for paper or live trading.",
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
