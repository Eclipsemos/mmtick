#!/usr/bin/env python3
"""Compare distinct BTCUSDT bar-strategy families without using the confirmation split."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import (
    ResearchBar,
    aggregate_bars,
    buy_and_hold_targets,
    donchian_targets,
    ema_targets,
    evaluate_targets,
    funding_by_bar,
    momentum_targets,
    rsi_reversion_targets,
)
from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class Candidate:
    id: str
    family: str
    interval_minutes: int
    direction: str
    parameters: dict[str, Any]
    bars: list[ResearchBar]
    funding: list[list[FundingRate]]
    targets: tuple[int | None, ...]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/btc_strategy_families/2026-08-14"),
    )
    args = parser.parse_args()

    source_bars, funding_rates = load_market(args.database)
    intervals = (60, 240, 1440)
    bars_by_interval = {
        interval: aggregate_bars(source_bars, interval) for interval in intervals
    }
    funding_by_interval = {
        interval: funding_by_bar(bars, funding_rates)
        for interval, bars in bars_by_interval.items()
    }
    splits = {
        "train": (_day_start(date(2024, 2, 1)), _day_end(date(2024, 12, 31))),
        "validation": (_day_start(date(2025, 1, 1)), _day_end(date(2025, 12, 31))),
        "confirmation": (_day_start(date(2026, 1, 1)), _day_end(date(2026, 8, 10))),
        "full": (_day_start(date(2024, 2, 1)), _day_end(date(2026, 8, 10))),
    }
    candidates = candidate_grid(bars_by_interval, funding_by_interval)
    development = []
    for candidate in candidates:
        train = evaluate(candidate, splits["train"])
        validation = evaluate(candidate, splits["validation"])
        score = selection_score(train, validation)
        development.append(
            {
                "candidate": candidate,
                "selection_score": score,
                "train": summary(train),
                "validation": summary(validation),
            }
        )

    eligible = [
        item
        for item in development
        if item["train"]["completed_trades"] >= 4
        and item["validation"]["completed_trades"] >= 4
        and item["train"]["net_return"] > 0
        and item["validation"]["net_return"] > 0
        and not item["train"]["bankrupt"]
        and not item["validation"]["bankrupt"]
    ]
    eligible.sort(key=lambda item: item["selection_score"], reverse=True)

    family_results = []
    for family in sorted({item.family for item in candidates}):
        rows = [item for item in development if item["candidate"].family == family]
        selected = max(rows, key=lambda item: item["selection_score"])
        confirmation = evaluate(selected["candidate"], splits["confirmation"])
        family_results.append(
            serialize_evaluation(selected, confirmation=summary(confirmation))
        )
    notable_leads = []
    for row in family_results:
        if not all(row[name]["net_return"] > 0 for name in ("train", "validation", "confirmation")):
            continue
        candidate = next(item for item in candidates if item.id == row["id"])
        notable_leads.append(
            {
                **row,
                "risk_ladder": [
                    {
                        "exposure": exposure,
                        **summary(
                            evaluate(candidate, splits["confirmation"], exposure=exposure)
                        ),
                    }
                    for exposure in (0.5, 1.0, 2.0, 3.0, 4.0)
                ],
            }
        )

    selected = eligible[0] if eligible else None
    selected_payload = None
    risk_ladder = []
    confirmation_neighbor_rate = None
    if selected is not None:
        candidate = selected["candidate"]
        confirmation = evaluate(candidate, splits["confirmation"])
        full = evaluate(candidate, splits["full"])
        selected_payload = serialize_evaluation(
            selected,
            confirmation=summary(confirmation),
            full=summary(full),
        )
        for exposure in (0.5, 1.0, 2.0, 3.0, 4.0):
            risk_ladder.append(
                {
                    "exposure": exposure,
                    **summary(evaluate(candidate, splits["confirmation"], exposure=exposure)),
                }
            )
        neighbors = eligible[: min(5, len(eligible))]
        neighbor_confirmations = [
            evaluate(item["candidate"], splits["confirmation"]) for item in neighbors
        ]
        confirmation_neighbor_rate = sum(
            item.net_return > 0 and not item.bankrupt for item in neighbor_confirmations
        ) / len(neighbor_confirmations)

    benchmark = benchmark_results(bars_by_interval[60], funding_by_interval[60], splits)
    confirmation_summary = selected_payload["confirmation"] if selected_payload else None
    approved = bool(
        confirmation_summary
        and confirmation_summary["net_return"] > 0
        and confirmation_summary["profit_factor"] is not None
        and confirmation_summary["profit_factor"] > 1
        and confirmation_summary["completed_trades"] >= 6
        and confirmation_summary["max_drawdown"] >= -0.25
        and confirmation_neighbor_rate is not None
        and confirmation_neighbor_rate >= 0.6
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTCUSDT non-ATR strategy-family comparison",
        "data": {
            "source_bars_15m": len(source_bars),
            "bars_1h": len(bars_by_interval[60]),
            "bars_4h": len(bars_by_interval[240]),
            "bars_1d": len(bars_by_interval[1440]),
            "funding_events": len(funding_rates),
            "first_bar": _timestamp(source_bars[0].start_ms),
            "last_bar": _timestamp(source_bars[-1].end_ms),
        },
        "execution": {
            "signal_timing": "closed bar",
            "fill_timing": "next bar open",
            "fee_bps_per_fill": 5,
            "slippage_bps_per_fill": 2,
            "funding": "historical Binance funding applied while positioned",
            "initial_equity": 100000,
            "base_exposure": 1.0,
            "liquidation_modeled": False,
        },
        "splits": {
            name: {"start": _timestamp(period[0]), "end": _timestamp(period[1])}
            for name, period in splits.items()
        },
        "selection": {
            "candidate_count": len(candidates),
            "eligible_count": len(eligible),
            "confirmation_used_for_selection": False,
            "rule": (
                "require positive train and validation returns with at least four trades in each; "
                "rank by the weaker split return, total return, then drawdown"
            ),
            "top_development_candidates": [
                serialize_evaluation(item) for item in eligible[:10]
            ],
            "confirmation_positive_rate_among_top_five": confirmation_neighbor_rate,
            "sequential_research_note": (
                "The original 72-candidate family comparison revealed confirmation results. "
                "EMA deadband variants were added afterward to address observed churn and are "
                "diagnostic, not fresh confirmation evidence."
            ),
        },
        "family_winners": family_results,
        "notable_leads": notable_leads,
        "selected": selected_payload,
        "risk_ladder": risk_ladder,
        "benchmark": benchmark,
        "target": {
            "monthly_return": 0.25,
            "achieved": bool(
                confirmation_summary
                and confirmation_summary["geometric_monthly_return"] >= 0.25
            ),
        },
        "decision": {
            "status": "research_candidate" if approved else "rejected_after_confirmation",
            "approved": approved,
            "reason": (
                "development winner passed confirmation gates but remains research-only"
                if approved
                else "no development-selected strategy passed all confirmation stability gates"
            ),
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False))
    if selected_payload:
        print(
            "selected",
            selected_payload["id"],
            f"train={selected_payload['train']['net_return']:.2%}",
            f"validation={selected_payload['validation']['net_return']:.2%}",
            f"confirmation={selected_payload['confirmation']['net_return']:.2%}",
        )
    print(args.output_dir / "results.json")
    print(args.output_dir / "README.md")


def load_market(database: Path) -> tuple[list[ResearchBar], list[FundingRate]]:
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        bars = [
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
                WHERE instrument_id = 'btc_perp' AND interval_minutes = 15 AND is_closed = 1
                ORDER BY start_ms
                """
            )
        ]
        rates = [
            FundingRate(
                timestamp_ms=int(row["timestamp_ms"]),
                rate=Decimal(row["rate"]),
                mark_price=Decimal(row["mark_price"]),
            )
            for row in connection.execute(
                """
                SELECT timestamp_ms, rate, mark_price FROM funding_rates
                WHERE instrument_id = 'btc_perp' ORDER BY timestamp_ms
                """
            )
        ]
    if len(bars) < 10_000:
        raise ValueError("BTCUSDT requires at least 10,000 complete 15m bars")
    return bars, rates


def candidate_grid(
    bars_by_interval: dict[int, list[ResearchBar]],
    funding_by_interval: dict[int, list[list[FundingRate]]],
) -> list[Candidate]:
    candidates = []
    ema_pairs = {
        60: ((12, 48), (24, 96), (48, 192)),
        240: ((6, 24), (12, 48), (24, 96)),
        1440: ((10, 50), (20, 100), (50, 200)),
    }
    donchian_pairs = {
        60: ((24, 12), (72, 24), (168, 48)),
        240: ((6, 3), (18, 6), (42, 12)),
        1440: ((20, 10), (55, 20), (100, 50)),
    }
    momentum_pairs = {
        60: ((24, 0.01), (72, 0.02), (168, 0.04)),
        240: ((6, 0.01), (18, 0.02), (42, 0.04)),
        1440: ((20, 0.05), (60, 0.10), (120, 0.15)),
    }
    for interval in (60, 240, 1440):
        bars = bars_by_interval[interval]
        grouped_funding = funding_by_interval[interval]
        for direction in ("long_only", "long_short"):
            for fast, slow in ema_pairs[interval]:
                parameters = {"fast_period": fast, "slow_period": slow}
                candidates.append(
                    Candidate(
                        id=f"ema-{interval}m-{fast}-{slow}-{direction}",
                        family="ema_trend",
                        interval_minutes=interval,
                        direction=direction,
                        parameters=parameters,
                        bars=bars,
                        funding=grouped_funding,
                        targets=ema_targets(bars, fast, slow, direction),
                    )
                )
                if interval == 1440 and (fast, slow) in {(10, 50), (20, 100)}:
                    for separation in (0.01, 0.03, 0.05):
                        filtered_parameters = {
                            **parameters,
                            "minimum_separation": separation,
                        }
                        candidates.append(
                            Candidate(
                                id=(
                                    f"ema-deadband-{interval}m-{fast}-{slow}-"
                                    f"{separation:g}-{direction}"
                                ),
                                family="ema_deadband",
                                interval_minutes=interval,
                                direction=direction,
                                parameters=filtered_parameters,
                                bars=bars,
                                funding=grouped_funding,
                                targets=ema_targets(
                                    bars,
                                    fast,
                                    slow,
                                    direction,
                                    minimum_separation=separation,
                                ),
                            )
                        )
            for entry, exit_window in donchian_pairs[interval]:
                parameters = {"entry_window": entry, "exit_window": exit_window}
                candidates.append(
                    Candidate(
                        id=f"donchian-{interval}m-{entry}-{exit_window}-{direction}",
                        family="donchian_breakout",
                        interval_minutes=interval,
                        direction=direction,
                        parameters=parameters,
                        bars=bars,
                        funding=grouped_funding,
                        targets=donchian_targets(bars, entry, exit_window, direction),
                    )
                )
        for direction in ("long_only", "long_short"):
            for lookback, threshold in momentum_pairs[interval]:
                parameters = {"lookback": lookback, "threshold": threshold}
                candidates.append(
                    Candidate(
                        id=f"momentum-{interval}m-{lookback}-{threshold:g}-{direction}",
                        family="time_series_momentum",
                        interval_minutes=interval,
                        direction=direction,
                        parameters=parameters,
                        bars=bars,
                        funding=grouped_funding,
                        targets=momentum_targets(bars, lookback, threshold, direction),
                    )
                )
            for lower, upper in ((20, 80), (25, 75), (30, 70)):
                parameters = {"period": 14, "lower": lower, "upper": upper}
                candidates.append(
                    Candidate(
                        id=f"rsi-{interval}m-{lower}-{upper}-{direction}",
                        family="rsi_mean_reversion",
                        interval_minutes=interval,
                        direction=direction,
                        parameters=parameters,
                        bars=bars,
                        funding=grouped_funding,
                        targets=rsi_reversion_targets(bars, 14, lower, upper, direction),
                    )
                )
    return candidates


def evaluate(candidate: Candidate, period: tuple[int, int], exposure: float = 1.0):
    return evaluate_targets(
        candidate.bars,
        candidate.targets,
        start_ms=period[0],
        end_ms=period[1],
        funding=candidate.funding,
        exposure=exposure,
    )


def selection_score(train, validation) -> tuple[float, float, float]:
    return (
        min(train.net_return, validation.net_return),
        train.net_return + validation.net_return,
        min(train.max_drawdown, validation.max_drawdown),
    )


def summary(result) -> dict[str, Any]:
    payload = asdict(result)
    payload.pop("trades")
    month_count = len(result.monthly_returns)
    payload["geometric_monthly_return"] = (
        (1 + result.net_return) ** (1 / month_count) - 1
        if month_count and result.net_return > -1
        else -1.0
    )
    payload["months_at_25_percent"] = sum(
        value >= 0.25 for _label, value in result.monthly_returns
    )
    payload["daily_returns"] = [
        {"date": label, "return": value} for label, value in result.daily_returns
    ]
    payload["monthly_returns"] = [
        {"month": label, "return": value} for label, value in result.monthly_returns
    ]
    return payload


def serialize_evaluation(item, **extra) -> dict[str, Any]:
    candidate = item["candidate"]
    return {
        "id": candidate.id,
        "family": candidate.family,
        "interval_minutes": candidate.interval_minutes,
        "direction": candidate.direction,
        "parameters": candidate.parameters,
        "selection_score": list(item["selection_score"]),
        "train": item["train"],
        "validation": item["validation"],
        **extra,
    }


def benchmark_results(
    bars: list[ResearchBar],
    funding: list[list[FundingRate]],
    splits: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    targets = buy_and_hold_targets(bars)
    return {
        name: summary(
            evaluate_targets(
                bars,
                targets,
                start_ms=period[0],
                end_ms=period[1],
                funding=funding,
            )
        )
        for name, period in splits.items()
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# BTCUSDT Non-ATR Strategy Families",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        (
            "All signals use closed 1h, 4h, or 1d bars and fill at the next bar open. Results "
            "include "
            "5 bps fees and 2 bps slippage per fill plus historical funding. Confirmation data "
            "was not used for candidate selection."
        ),
        "",
        "## Families",
        "",
        "- EMA trend: fast/slow exponential moving-average direction.",
        "- EMA deadband: the same trend signal, but cash when EMA separation is too small.",
        "- Donchian breakout: enter on a prior-channel break and exit through a shorter channel.",
        "- Time-series momentum: direction from a fixed historical return and neutral threshold.",
        "- RSI mean reversion: fade RSI extremes and exit at RSI 50.",
        "",
        (
            "Sequential note: the first 72 candidates revealed confirmation results before EMA "
            "deadband variants were added. Deadband results are diagnostic and are not fresh "
            "confirmation evidence."
        ),
        "",
        "## Family Winners",
        "",
        "| Family | Candidate | Train | Validation | Confirmation | Confirm DD | Trades |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["family_winners"]:
        confirmation = row["confirmation"]
        lines.append(
            f"| {row['family']} | `{row['id']}` | {row['train']['net_return']:.2%} | "
            f"{row['validation']['net_return']:.2%} | {confirmation['net_return']:.2%} | "
            f"{confirmation['max_drawdown']:.2%} | {confirmation['completed_trades']} |"
        )
    lines.extend(["", "## Selected Development Winner", ""])
    selected = payload["selected"]
    if selected is None:
        lines.append("No candidate was positive in both training and validation.")
    else:
        lines.extend(
            [
                f"Candidate: `{selected['id']}`",
                "",
                (
                    f"Train {selected['train']['net_return']:.2%}; validation "
                    f"{selected['validation']['net_return']:.2%}; confirmation "
                    f"{selected['confirmation']['net_return']:.2%}."
                ),
                "",
                "### Confirmation Monthly Returns",
                "",
                "| Month | Return |",
                "|---|---:|",
            ]
        )
        for row in selected["confirmation"]["monthly_returns"]:
            lines.append(f"| {row['month']} | {row['return']:.2%} |")
        lines.extend(
            [
                "",
                "### Exposure Stress",
                "",
                "| Exposure | Confirmation return | Max DD | Bankrupt |",
                "|---:|---:|---:|---|",
            ]
        )
        for row in payload["risk_ladder"]:
            lines.append(
                f"| {row['exposure']:.1f}x | {row['net_return']:.2%} | "
                f"{row['max_drawdown']:.2%} | {'yes' if row['bankrupt'] else 'no'} |"
            )
    benchmark = payload["benchmark"]["confirmation"]
    lines.extend(["", "## Notable But Unapproved Leads", ""])
    if not payload["notable_leads"]:
        lines.append("No family winner was positive in all three splits.")
    for lead in payload["notable_leads"]:
        confirmation = lead["confirmation"]
        lines.extend(
            [
                (
                    f"`{lead['id']}` was positive in all three splits, but its training max "
                    f"drawdown was {lead['train']['max_drawdown']:.2%} and confirmation contained "
                    f"only {confirmation['completed_trades']} completed trades. Confirmation "
                    f"geometric monthly return was "
                    f"{confirmation['geometric_monthly_return']:.2%}."
                ),
                "",
                "| Exposure | Confirmation return | Monthly geometric | Max DD | Bankrupt |",
                "|---:|---:|---:|---:|---|",
            ]
        )
        for row in lead["risk_ladder"]:
            lines.append(
                f"| {row['exposure']:.1f}x | {row['net_return']:.2%} | "
                f"{row['geometric_monthly_return']:.2%} | {row['max_drawdown']:.2%} | "
                f"{'yes' if row['bankrupt'] else 'no'} |"
            )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"Status: `{payload['decision']['status']}`.",
            "",
            (
                f"The 1x buy-and-hold confirmation benchmark returned "
                f"{benchmark['net_return']:.2%} with {benchmark['max_drawdown']:.2%} max drawdown."
            ),
            "",
            (
                "The 25% monthly target is an evaluation threshold, not a parameter-selection "
                "override. No result is production-approved by this exploratory study."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
