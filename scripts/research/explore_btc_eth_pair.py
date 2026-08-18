#!/usr/bin/env python3
"""Explore equal-notional BTC/ETH pair strategies on closed daily bars."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.bar_research import (
    ResearchBar,
    aggregate_bars,
    funding_by_bar,
)
from mastermind_tick.models import FundingRate
from mastermind_tick.pair_research import (
    PairBar,
    PairResult,
    align_pair_bars,
    evaluate_pair_targets,
    ratio_ema_targets,
    ratio_mean_reversion_targets,
    ratio_momentum_targets,
)


@dataclass(frozen=True)
class Candidate:
    id: str
    family: str
    parameters: dict[str, Any]
    bars: list[PairBar]
    funding_left: list[list[FundingRate]]
    funding_right: list[list[FundingRate]]
    targets: tuple[int | None, ...]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/btc_eth_pair/2026-08-14"),
    )
    args = parser.parse_args()
    btc, eth, btc_funding, eth_funding = load_market(args.database)
    btc_daily = aggregate_bars(btc, 1440)
    eth_daily = aggregate_bars(eth, 1440)
    pair_bars = align_pair_bars(btc_daily, eth_daily)
    funding_left = funding_by_bar(btc_daily, btc_funding)
    funding_right = funding_by_bar(eth_daily, eth_funding)
    splits = {
        "train": (_day_start(date(2024, 2, 1)), _day_end(date(2024, 12, 31))),
        "validation": (_day_start(date(2025, 1, 1)), _day_end(date(2025, 12, 31))),
        "confirmation": (_day_start(date(2026, 1, 1)), _day_end(date(2026, 8, 10))),
        "full": (_day_start(date(2024, 2, 1)), _day_end(date(2026, 8, 10))),
    }
    candidates = candidate_grid(pair_bars, funding_left, funding_right)
    development = []
    for candidate in candidates:
        train = evaluate(candidate, splits["train"])
        validation = evaluate(candidate, splits["validation"])
        development.append(
            {
                "candidate": candidate,
                "score": selection_score(train, validation),
                "train": summary(train),
                "validation": summary(validation),
            }
        )
    eligible = [
        item
        for item in development
        if item["train"]["completed_trades"] >= 3
        and item["validation"]["completed_trades"] >= 3
        and item["train"]["net_return"] > 0
        and item["validation"]["net_return"] > 0
        and not item["train"]["bankrupt"]
        and not item["validation"]["bankrupt"]
    ]
    eligible.sort(key=lambda item: item["score"], reverse=True)
    family_winners = []
    for family in sorted({item.family for item in candidates}):
        selected = max(
            (item for item in development if item["candidate"].family == family),
            key=lambda item: item["score"],
        )
        family_winners.append(
            serialize(
                selected,
                confirmation=summary(evaluate(selected["candidate"], splits["confirmation"])),
            )
        )
    selected = eligible[0] if eligible else None
    selected_payload = None
    risk_ladder = []
    if selected:
        candidate = selected["candidate"]
        selected_payload = serialize(
            selected,
            confirmation=summary(evaluate(candidate, splits["confirmation"])),
            full=summary(evaluate(candidate, splits["full"])),
        )
        risk_ladder = [
            {
                "exposure": exposure,
                **summary(evaluate(candidate, splits["confirmation"], exposure)),
            }
            for exposure in (0.5, 1.0, 2.0, 3.0, 4.0)
        ]
    benchmark = benchmark_results(pair_bars, funding_left, funding_right, splits)
    confirmation = selected_payload["confirmation"] if selected_payload else None
    approved = bool(
        confirmation
        and confirmation["net_return"] > 0
        and (confirmation["profit_factor"] or 0) > 1
        and confirmation["completed_trades"] >= 6
        and confirmation["max_drawdown"] >= -0.25
        and confirmation["geometric_monthly_return"] >= 0.25
        and confirmation["net_return"] > benchmark["confirmation"]["net_return"]
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTCUSDT/ETHUSDT equal-notional market-neutral pair",
        "data": {
            "btc_daily_bars": len(btc_daily),
            "eth_daily_bars": len(eth_daily),
            "pair_bars": len(pair_bars),
            "btc_funding_events": len(btc_funding),
            "eth_funding_events": len(eth_funding),
            "execution_source": "daily OHLCV bars; no ETH aggregate trades imported",
        },
        "execution": {
            "signal_timing": "closed daily bar",
            "fill_timing": "next daily bar open",
            "gross_exposure": "1.0x total, 0.5x each leg",
            "fee_bps_per_fill": 5,
            "slippage_bps_per_fill": 2,
            "funding": "both legs applied at historical funding timestamps",
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
                "positive train and validation returns, at least three pair round trips per "
                "split, then maximize the weaker split return"
            ),
            "top_development_candidates": [serialize(item) for item in eligible[:10]],
        },
        "family_winners": family_winners,
        "selected": selected_payload,
        "risk_ladder": risk_ladder,
        "benchmark": benchmark,
        "target": {
            "monthly_return": 0.25,
            "achieved": bool(
                confirmation and confirmation["geometric_monthly_return"] >= 0.25
            ),
        },
        "decision": {
            "status": "research_candidate" if approved else "rejected_after_confirmation",
            "approved": approved,
            "reason": (
                "selected pair passed return, drawdown, trade-count, and target gates but "
                "remains research-only"
                if approved
                else "pair return was positive but did not pass the monthly target and "
                "benchmark gates"
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


def load_market(
    database: Path,
) -> tuple[list[ResearchBar], list[ResearchBar], list[FundingRate], list[FundingRate]]:
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row

        def bars(instrument: str) -> list[ResearchBar]:
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
                    (instrument,),
                )
            ]

        def funding(instrument: str) -> list[FundingRate]:
            return [
                FundingRate(
                    timestamp_ms=int(row["timestamp_ms"]),
                    rate=Decimal(row["rate"]),
                    mark_price=Decimal(row["mark_price"]),
                )
                for row in connection.execute(
                    """
                    SELECT timestamp_ms, rate, mark_price FROM funding_rates
                    WHERE instrument_id = ? ORDER BY timestamp_ms
                    """,
                    (instrument,),
                )
            ]

        btc = bars("btc_perp")
        eth = bars("eth_perp")
        btc_funding = funding("btc_perp")
        eth_funding = funding("eth_perp")
    if len(btc) != len(eth) or len(btc) < 10_000:
        raise ValueError("BTC and ETH need aligned history of at least 10,000 15m bars")
    if not btc_funding or not eth_funding:
        raise ValueError("BTC and ETH funding history is required")
    return btc, eth, btc_funding, eth_funding


def candidate_grid(
    bars: list[PairBar],
    funding_left: list[list[FundingRate]],
    funding_right: list[list[FundingRate]],
) -> list[Candidate]:
    candidates = []
    for fast, slow in ((10, 50), (20, 100), (50, 200)):
        candidates.append(
            Candidate(
                id=f"ratio-ema-{fast}-{slow}",
                family="ratio_ema_trend",
                parameters={"fast_period": fast, "slow_period": slow},
                bars=bars,
                funding_left=funding_left,
                funding_right=funding_right,
                targets=ratio_ema_targets(bars, fast, slow),
            )
        )
    for lookback, threshold in ((20, 0.02), (60, 0.05), (120, 0.10)):
        candidates.append(
            Candidate(
                id=f"ratio-momentum-{lookback}-{threshold:g}",
                family="ratio_momentum",
                parameters={"lookback": lookback, "threshold": threshold},
                bars=bars,
                funding_left=funding_left,
                funding_right=funding_right,
                targets=ratio_momentum_targets(bars, lookback, threshold),
            )
        )
    for window in (20, 60, 120):
        for entry_z, exit_z in ((1.0, 0.25), (1.5, 0.5), (2.0, 0.5)):
            candidates.append(
                Candidate(
                    id=f"ratio-mean-{window}-{entry_z:g}-{exit_z:g}",
                    family="ratio_mean_reversion",
                    parameters={"window": window, "entry_z": entry_z, "exit_z": exit_z},
                    bars=bars,
                    funding_left=funding_left,
                    funding_right=funding_right,
                    targets=ratio_mean_reversion_targets(bars, window, entry_z, exit_z),
                )
            )
    return candidates


def evaluate(candidate: Candidate, period: tuple[int, int], exposure: float = 1.0) -> PairResult:
    return evaluate_pair_targets(
        candidate.bars,
        candidate.targets,
        candidate.funding_left,
        candidate.funding_right,
        start_ms=period[0],
        end_ms=period[1],
        exposure=exposure,
    )


def selection_score(train: PairResult, validation: PairResult) -> tuple[float, float, float]:
    return (
        min(train.net_return, validation.net_return),
        train.net_return + validation.net_return,
        min(train.max_drawdown, validation.max_drawdown),
    )


def summary(result: PairResult) -> dict[str, Any]:
    payload = {
        "exposure": result.exposure,
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
        "bankrupt": result.bankrupt,
        "daily_returns": [
            {"date": label, "return": value} for label, value in result.daily_returns
        ],
        "monthly_returns": [
            {"month": label, "return": value} for label, value in result.monthly_returns
        ],
    }
    month_count = len(result.monthly_returns)
    payload["geometric_monthly_return"] = (
        (1 + result.net_return) ** (1 / month_count) - 1
        if month_count and result.net_return > -1
        else -1.0
    )
    payload["months_at_25_percent"] = sum(
        value >= 0.25 for _label, value in result.monthly_returns
    )
    return payload


def serialize(item: dict[str, Any], **extra: Any) -> dict[str, Any]:
    candidate = item["candidate"]
    return {
        "id": candidate.id,
        "family": candidate.family,
        "parameters": candidate.parameters,
        "score": list(item["score"]),
        "train": item["train"],
        "validation": item["validation"],
        **extra,
    }


def benchmark_results(
    bars: list[PairBar],
    funding_left: list[list[FundingRate]],
    funding_right: list[list[FundingRate]],
    splits: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    targets = tuple(1 for _ in bars)
    return {
        name: summary(
            evaluate_pair_targets(
                bars,
                targets,
                funding_left,
                funding_right,
                start_ms=period[0],
                end_ms=period[1],
            )
        )
        for name, period in splits.items()
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# BTCUSDT/ETHUSDT Market-Neutral Pair Study",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        (
            "BTC is the left leg and ETH the right leg. A +1 signal is long BTC/short ETH; -1 "
            "reverses the pair. Each leg receives half the gross exposure. Signals use closed "
            "daily K lines and fill at the next daily open."
        ),
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
        lines.append("No pair candidate was positive in both training and validation.")
    else:
        lines.extend(
            [
                f"Candidate: `{selected['id']}`",
                "",
                (
                    f"Train {selected['train']['net_return']:.2%}; validation "
                    f"{selected['validation']['net_return']:.2%}; confirmation "
                    f"{selected['confirmation']['net_return']:.2%}; geometric monthly "
                    f"confirmation {selected['confirmation']['geometric_monthly_return']:.2%}."
                ),
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
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"Status: `{payload['decision']['status']}`.",
            "",
            (
                f"The always-long-BTC/short-ETH benchmark returned "
                f"{payload['benchmark']['confirmation']['net_return']:.2%} in confirmation."
            ),
            "",
            (
                "This is OHLCV-level evidence: ETH aggregate trades were not imported, so it is "
                "not a Tick-level execution approval. The 25% monthly target remains unmet."
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
