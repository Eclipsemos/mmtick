#!/usr/bin/env python3
"""Audit short-horizon BTC/ETH relative-value mean reversion."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import ResearchBar, aggregate_bars, funding_by_bar
from mastermind_tick.models import FundingRate
from mastermind_tick.pair_research import (
    PairBar,
    PairResult,
    align_pair_bars,
    evaluate_pair_targets,
    relative_shock_targets,
    short_horizon_ratio_targets,
)

BASE_COST = (Decimal("5"), Decimal("2"))
STRESS_COST = (Decimal("10"), Decimal("5"))
INTERVALS = (15, 60, 240)
LOOKBACK_DAYS = (1, 3, 7)
ENTRY_Z_VALUES = (1.5, 2.0, 2.5)
EXIT_Z_VALUES = (0.25, 0.5)
HOLD_HOURS = (4, 12, 24)


@dataclass(frozen=True)
class Candidate:
    id: str
    family: str
    interval_minutes: int
    lookback_bars: int
    entry_z: float
    exit_z: float
    maximum_hold_bars: int
    bars: list[PairBar]
    funding_left: list[list[FundingRate]]
    funding_right: list[list[FundingRate]]
    targets: tuple[int | None, ...]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/short_horizon_relative_value/2026-08-18"),
    )
    args = parser.parse_args()
    btc, eth, btc_funding, eth_funding = load_market(args.database)
    candidates = candidate_grid(btc, eth, btc_funding, eth_funding)
    periods = {
        "train": (_start(date(2021, 1, 1)), _end(date(2023, 12, 31))),
        "validation": (_start(date(2024, 1, 1)), _end(date(2025, 12, 31))),
        "confirmation": (_start(date(2026, 1, 1)), _end(date(2026, 8, 11))),
    }
    development: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, 1):
        results = {
            split: {
                "base": summarize(evaluate(candidate, period, BASE_COST)),
                "stress": summarize(evaluate(candidate, period, STRESS_COST)),
            }
            for split, period in periods.items()
            if split != "confirmation"
        }
        score = selection_score(results)
        development.append({"candidate": candidate, "development": results, "score": score})
        if index % 25 == 0:
            print(f"evaluated development {index}/{len(candidates)}", flush=True)
    development.sort(key=lambda row: row["score"], reverse=True)
    eligible = [row for row in development if development_eligible(row)]
    selected = eligible[0] if eligible else development[0]
    finalist_rows = development[:10]
    confirmation_rows = []
    for row in finalist_rows:
        candidate = row["candidate"]
        confirmation_rows.append(
            {
                **serialize_development(row),
                "confirmation": {
                    "base": summarize(evaluate(candidate, periods["confirmation"], BASE_COST)),
                    "stress": summarize(evaluate(candidate, periods["confirmation"], STRESS_COST)),
                },
            }
        )
    selected_confirmation = next(
        row for row in confirmation_rows if row["id"] == selected["candidate"].id
    )
    selected_confirmation["zero_cost"] = {
        split: summarize(evaluate(selected["candidate"], period, (Decimal("0"), Decimal("0"))))
        for split, period in periods.items()
    }
    payload = build_payload(
        candidates,
        periods,
        eligible,
        selected,
        selected_confirmation,
        confirmation_rows,
        btc,
        eth,
        btc_funding,
        eth_funding,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(args.output_dir / "README.md", flush=True)


def load_market(
    database: Path,
) -> tuple[list[ResearchBar], list[ResearchBar], list[FundingRate], list[FundingRate]]:
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row

        def bars(instrument_id: str) -> list[ResearchBar]:
            return [
                ResearchBar(
                    start_ms=int(row["start_ms"]),
                    end_ms=int(row["end_ms"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]),
                )
                for row in connection.execute(
                    """
                    SELECT start_ms, end_ms, open, high, low, close, volume
                    FROM ohlcv_bars
                    WHERE instrument_id = ? AND interval_minutes = 15 AND is_closed = 1
                    ORDER BY start_ms
                    """,
                    (instrument_id,),
                )
            ]

        def funding(instrument_id: str) -> list[FundingRate]:
            return [
                FundingRate(
                    timestamp_ms=int(row["timestamp_ms"]),
                    rate=Decimal(row["rate"]),
                    mark_price=Decimal(row["mark_price"]),
                )
                for row in connection.execute(
                    """
                    SELECT
                        funding.timestamp_ms,
                        funding.rate,
                        COALESCE(
                            NULLIF(funding.mark_price, ''),
                            (
                                SELECT bars.close
                                FROM ohlcv_bars AS bars
                                WHERE bars.instrument_id = funding.instrument_id
                                  AND bars.interval_minutes = 15
                                  AND bars.is_closed = 1
                                  AND bars.start_ms <= funding.timestamp_ms
                                  AND bars.end_ms >= funding.timestamp_ms
                                ORDER BY bars.start_ms DESC
                                LIMIT 1
                            )
                        ) AS mark_price
                    FROM funding_rates AS funding
                    WHERE funding.instrument_id = ?
                      AND NULLIF(funding.rate, '') IS NOT NULL
                    ORDER BY funding.timestamp_ms
                    """,
                    (instrument_id,),
                )
                if row["mark_price"] is not None
            ]

        btc = bars("btc_perp")
        eth = bars("eth_perp")
        btc_funding = funding("btc_perp")
        eth_funding = funding("eth_perp")
    if len(btc) != len(eth) or len(btc) < 200_000:
        raise ValueError("BTC and ETH require aligned 15-minute history")
    return btc, eth, btc_funding, eth_funding


def candidate_grid(
    btc: list[ResearchBar],
    eth: list[ResearchBar],
    btc_funding: list[FundingRate],
    eth_funding: list[FundingRate],
) -> list[Candidate]:
    candidates = []
    for interval in INTERVALS:
        left = btc if interval == 15 else aggregate_bars(btc, interval)
        right = eth if interval == 15 else aggregate_bars(eth, interval)
        bars = align_pair_bars(left, right)
        funding_left = funding_by_bar(left, btc_funding)
        funding_right = funding_by_bar(right, eth_funding)
        bars_per_day = 1440 // interval
        bars_per_hour = 60 / interval
        for days in LOOKBACK_DAYS:
            window = days * bars_per_day
            for entry_z in ENTRY_Z_VALUES:
                for exit_z in EXIT_Z_VALUES:
                    for hold_hours in HOLD_HOURS:
                        maximum_hold_bars = max(1, round(hold_hours * bars_per_hour))
                        candidate_id = (
                            f"ratio-revert-{interval}m-window{days}d-entry{entry_z:g}-"
                            f"exit{exit_z:g}-hold{hold_hours}h"
                        )
                        candidates.append(
                            Candidate(
                                id=candidate_id,
                                family="level_reversion",
                                interval_minutes=interval,
                                lookback_bars=window,
                                entry_z=entry_z,
                                exit_z=exit_z,
                                maximum_hold_bars=maximum_hold_bars,
                                bars=bars,
                                funding_left=funding_left,
                                funding_right=funding_right,
                                targets=short_horizon_ratio_targets(
                                    bars, window, entry_z, exit_z, maximum_hold_bars
                                ),
                            )
                        )
        for days in LOOKBACK_DAYS:
            window = days * bars_per_day
            for entry_z in ENTRY_Z_VALUES:
                for hold_hours in (1, 4, 12):
                    maximum_hold_bars = max(1, round(hold_hours * bars_per_hour))
                    for mode in ("continuation", "reversion"):
                        candidate_id = (
                            f"relative-shock-{mode}-{interval}m-window{days}d-"
                            f"entry{entry_z:g}-hold{hold_hours}h"
                        )
                        candidates.append(
                            Candidate(
                                id=candidate_id,
                                family=f"shock_{mode}",
                                interval_minutes=interval,
                                lookback_bars=window,
                                entry_z=entry_z,
                                exit_z=0.0,
                                maximum_hold_bars=maximum_hold_bars,
                                bars=bars,
                                funding_left=funding_left,
                                funding_right=funding_right,
                                targets=relative_shock_targets(
                                    bars, window, entry_z, maximum_hold_bars, mode
                                ),
                            )
                        )
    return candidates


def evaluate(
    candidate: Candidate,
    period: tuple[int, int],
    costs: tuple[Decimal, Decimal],
) -> PairResult:
    return evaluate_pair_targets(
        candidate.bars,
        candidate.targets,
        candidate.funding_left,
        candidate.funding_right,
        start_ms=period[0],
        end_ms=period[1],
        fee_bps=costs[0],
        slippage_bps=costs[1],
    )


def summarize(result: PairResult) -> dict[str, Any]:
    return {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
        "bankrupt": result.bankrupt,
        "monthly_returns": [
            {"month": month, "return": value} for month, value in result.monthly_returns
        ],
    }


def selection_score(results: dict[str, dict[str, dict[str, Any]]]) -> tuple[float, ...]:
    values = [
        results[split][cost]["net_return"]
        for split in ("train", "validation")
        for cost in ("base", "stress")
    ]
    drawdowns = [
        results[split][cost]["max_drawdown"]
        for split in ("train", "validation")
        for cost in ("base", "stress")
    ]
    return (min(values), sum(values), min(drawdowns))


def development_eligible(row: dict[str, Any]) -> bool:
    results = row["development"]
    return all(
        results[split][cost]["net_return"] > 0
        and results[split][cost]["completed_trades"] >= 30
        and results[split][cost]["max_drawdown"] >= -0.20
        and not results[split][cost]["bankrupt"]
        for split in ("train", "validation")
        for cost in ("base", "stress")
    )


def serialize_development(row: dict[str, Any]) -> dict[str, Any]:
    candidate: Candidate = row["candidate"]
    return {
        "id": candidate.id,
        "parameters": {
            "family": candidate.family,
            "interval_minutes": candidate.interval_minutes,
            "lookback_bars": candidate.lookback_bars,
            "entry_z": candidate.entry_z,
            "exit_z": candidate.exit_z,
            "maximum_hold_bars": candidate.maximum_hold_bars,
        },
        "score": list(row["score"]),
        "development": row["development"],
    }


def build_payload(
    candidates: list[Candidate],
    periods: dict[str, tuple[int, int]],
    eligible: list[dict[str, Any]],
    selected: dict[str, Any],
    selected_confirmation: dict[str, Any],
    confirmation_rows: list[dict[str, Any]],
    btc: list[ResearchBar],
    eth: list[ResearchBar],
    btc_funding: list[FundingRate],
    eth_funding: list[FundingRate],
) -> dict[str, Any]:
    confirmation = selected_confirmation["confirmation"]
    approved = bool(
        eligible
        and confirmation["base"]["net_return"] > 0
        and confirmation["stress"]["net_return"] > 0
        and confirmation["base"]["profit_factor"] is not None
        and confirmation["base"]["profit_factor"] > 1
        and confirmation["stress"]["profit_factor"] is not None
        and confirmation["stress"]["profit_factor"] > 1
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTCUSDT/ETHUSDT short-horizon equal-notional relative value",
        "data": {
            "first_bar": _timestamp(btc[0].start_ms),
            "last_bar": _timestamp(btc[-1].end_ms),
            "btc_15m_bars": len(btc),
            "eth_15m_bars": len(eth),
            "btc_funding_events": len(btc_funding),
            "eth_funding_events": len(eth_funding),
            "historical_order_book": False,
        },
        "protocol": {
            "splits": {
                name: {"start": _timestamp(period[0]), "end": _timestamp(period[1])}
                for name, period in periods.items()
            },
            "signal": "closed-bar causal BTC/ETH log-ratio z-score",
            "fill": "next synchronized bar open",
            "gross_exposure": "1.0x total; 0.5x each leg",
            "base_cost_bps_per_leg_per_fill": {"fee": 5, "slippage": 2},
            "stress_cost_bps_per_leg_per_fill": {"fee": 10, "slippage": 5},
            "historical_funding": True,
            "liquidation_modeled": False,
            "confirmation_used_for_selection": False,
            "confirmation_is_fresh": False,
        },
        "search": {
            "candidate_count": len(candidates),
            "development_eligible_count": len(eligible),
            "interval_minutes": list(INTERVALS),
            "lookback_days": list(LOOKBACK_DAYS),
            "entry_z": list(ENTRY_Z_VALUES),
            "exit_z": list(EXIT_Z_VALUES),
            "maximum_hold_hours": list(HOLD_HOURS),
            "families": ["level_reversion", "shock_continuation", "shock_reversion"],
        },
        "selection": {
            "rule": (
                "positive train/validation under base/stress, at least 30 trades and no worse "
                "than -20% drawdown; maximize the weakest split/cost return"
            ),
            "development_selected": selected_confirmation,
            "top_development_candidates_with_confirmation": confirmation_rows,
        },
        "decision": {
            "status": "candidate_for_order_book_validation" if approved else "rejected",
            "approved_for_paper": False,
            "approved_for_live": False,
            "reason": (
                "The development-selected configuration remained profitable under base and "
                "stress costs in reused confirmation, but order-book validation is still required."
                if approved
                else "No development-robust short-horizon pair survived reused confirmation."
            ),
        },
        "limitations": [
            "This is relative-value speculation, not locked cash-and-carry arbitrage.",
            (
                "OHLCV next-open fills cannot model two-leg latency, bid/ask spread, depth, "
                "or leg risk."
            ),
            "The equal-notional hedge does not guarantee beta neutrality.",
            (
                "2026 has been viewed in earlier studies and is reused confirmation, not a "
                "fresh holdout."
            ),
        ],
    }


def markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["development_selected"]
    lines = [
        "# BTC/ETH Short-Horizon Relative-Value Audit",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        f"Decision: `{payload['decision']['status']}`. Paper/live approval: `false/false`.",
        "",
        (
            "This study fades the closed-bar BTC/ETH log-ratio z-score with equal notional legs, "
            "fills both legs at the next synchronized bar open, enforces a time exit, and charges "
            "fees, slippage, and historical funding. It is relative value, not risk-free arbitrage."
        ),
        "",
        "## Search",
        "",
        f"- Candidates: `{payload['search']['candidate_count']}`; development eligible: "
        f"`{payload['search']['development_eligible_count']}`.",
        "- Intervals: 15m, 1h, 4h; lookbacks: 1/3/7 days; maximum holds: 4/12/24 hours.",
        "- Train: 2021-2023; validation: 2024-2025; reused confirmation: 2026 through August 11.",
        "- Base cost: 5 bps fee + 2 bps slippage per leg per fill; stress: 10 + 5 bps.",
        "",
        "## Development-Selected Result",
        "",
        f"Candidate: `{selected['id']}`.",
        "",
        "| Split | Base return | Stress return | Base DD | Trades | Base PF |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for split in ("train", "validation"):
        base = selected["development"][split]["base"]
        stress = selected["development"][split]["stress"]
        lines.append(
            f"| {split} | {base['net_return']:.2%} | {stress['net_return']:.2%} | "
            f"{base['max_drawdown']:.2%} | {base['completed_trades']} | "
            f"{_number(base['profit_factor'])} |"
        )
    base = selected["confirmation"]["base"]
    stress = selected["confirmation"]["stress"]
    lines.append(
        f"| reused confirmation | {base['net_return']:.2%} | {stress['net_return']:.2%} | "
        f"{base['max_drawdown']:.2%} | {base['completed_trades']} | "
        f"{_number(base['profit_factor'])} |"
    )
    zero_cost = selected["zero_cost"]
    lines.extend(
        [
            "",
            (
                "Zero-cost diagnostic for the same candidate: "
                f"train `{zero_cost['train']['net_return']:.2%}`, validation "
                f"`{zero_cost['validation']['net_return']:.2%}`, reused confirmation "
                f"`{zero_cost['confirmation']['net_return']:.2%}`. The gross signal is weak and "
                "does not survive two-leg execution costs."
            ),
            "",
            "## Decision",
            "",
            payload["decision"]["reason"],
            "",
            "The historical warehouse has no bid/ask or order-book depth. A positive bar replay "
            "would only justify a forward recorder that measures synchronized executable spread, "
            "two-leg latency, partial fills, and adverse selection. It would not justify trading.",
            "",
            "## Limitations",
            "",
            *[f"- {item}" for item in payload["limitations"]],
            "",
        ]
    )
    return "\n".join(lines)


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _start(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)


def _end(value: date) -> int:
    return _start(date.fromordinal(value.toordinal() + 1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
