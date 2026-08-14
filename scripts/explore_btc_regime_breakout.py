#!/usr/bin/env python3
"""Explore daily-regime-filtered 4h Donchian breakouts on BTCUSDT."""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from explore_btc_strategy_families import load_market

from mastermind_tick.bar_research import (
    aggregate_bars,
    donchian_targets,
    ema_targets,
    evaluate_targets,
    funding_by_bar,
)
from mastermind_tick.models import FundingRate


@dataclass(frozen=True)
class Candidate:
    id: str
    parameters: dict[str, Any]
    bars: list
    funding: list[list[FundingRate]]
    targets: tuple[int | None, ...]


def main() -> None:
    database = Path("data/paper.db")
    output_dir = Path("reports/experiments/btc_regime_breakout/2026-08-14")
    source, rates = load_market(database)
    bars4h = aggregate_bars(source, 240)
    bars1d = aggregate_bars(source, 1440)
    funding4h = funding_by_bar(bars4h, rates)
    candidates = candidate_grid(bars4h, bars1d, funding4h)
    splits = {
        "train": (_day_start(date(2024, 2, 1)), _day_end(date(2024, 12, 31))),
        "validation": (_day_start(date(2025, 1, 1)), _day_end(date(2025, 12, 31))),
        "confirmation": (_day_start(date(2026, 1, 1)), _day_end(date(2026, 8, 10))),
        "full": (_day_start(date(2024, 2, 1)), _day_end(date(2026, 8, 10))),
    }
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
        if item["train"]["completed_trades"] >= 4
        and item["validation"]["completed_trades"] >= 4
        and item["train"]["net_return"] > 0
        and item["validation"]["net_return"] > 0
    ]
    eligible.sort(key=lambda item: item["score"], reverse=True)
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
    family_results = []
    for direction in ("long_only", "long_short"):
        rows = [
            item
            for item in development
            if item["candidate"].parameters["direction"] == direction
        ]
        if not rows:
            continue
        row = max(rows, key=lambda item: item["score"])
        family_results.append(
            serialize(row, confirmation=summary(evaluate(row["candidate"], splits["confirmation"])))
        )
    confirmation = selected_payload["confirmation"] if selected_payload else None
    approved = bool(
        confirmation
        and confirmation["net_return"] > 0
        and (confirmation["profit_factor"] or 0) > 1
        and confirmation["completed_trades"] >= 6
        and confirmation["max_drawdown"] >= -0.25
        and confirmation["geometric_monthly_return"] >= 0.25
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": "BTCUSDT daily EMA regime-filtered 4h Donchian breakout",
        "definition": (
            "The regime is the prior closed daily EMA direction. A 4h Donchian breakout is "
            "accepted only when its direction matches that regime; regime changes force flat."
        ),
        "data": {"bars_4h": len(bars4h), "bars_1d": len(bars1d), "funding_events": len(rates)},
        "execution": {
            "signal_timing": "closed 4h bar and prior closed daily regime",
            "fill_timing": "next 4h open",
            "fee_bps_per_fill": 5,
            "slippage_bps_per_fill": 2,
            "funding": "historical BTC funding",
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
            "rule": "positive train and validation returns with at least four trades per split",
            "top_development_candidates": [serialize(item) for item in eligible[:10]],
        },
        "direction_winners": family_results,
        "selected": selected_payload,
        "risk_ladder": risk_ladder,
        "target": {"monthly_return": 0.25, "achieved": bool(approved)},
        "decision": {
            "status": "research_candidate" if approved else "rejected_after_confirmation",
            "approved": approved,
            "reason": (
                "regime-filtered breakout passed all research gates but remains research-only"
                if approved
                else "regime filtering did not produce a stable candidate at the monthly target"
            ),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False))
    if selected_payload:
        print(
            "selected",
            selected_payload["id"],
            f"train={selected_payload['train']['net_return']:.2%}",
            f"validation={selected_payload['validation']['net_return']:.2%}",
            f"confirmation={selected_payload['confirmation']['net_return']:.2%}",
        )


def candidate_grid(bars4h, bars1d, funding4h) -> list[Candidate]:
    candidates = []
    daily_starts = [bar.start_ms for bar in bars1d]
    for fast, slow in ((10, 50), (20, 100), (50, 200)):
        regime = ema_targets(bars1d, fast, slow, "long_short")
        for entry, exit_window in ((18, 6), (42, 12), (90, 30)):
            raw = donchian_targets(bars4h, entry, exit_window, "long_short")
            for direction in ("long_only", "long_short"):
                targets = []
                for index, bar in enumerate(bars4h):
                    daily_index = bisect.bisect_right(daily_starts, bar.start_ms) - 1
                    regime_value = regime[daily_index - 1] if daily_index > 0 else None
                    breakout = raw[index]
                    if breakout is None:
                        targets.append(None)
                    elif regime_value is None or regime_value == 0:
                        targets.append(0)
                    elif direction == "long_only":
                        targets.append(breakout if breakout == 1 and regime_value == 1 else 0)
                    else:
                        targets.append(breakout if breakout == regime_value else 0)
                candidates.append(
                    Candidate(
                        id=f"regime-{fast}-{slow}-donchian-{entry}-{exit_window}-{direction}",
                        parameters={
                            "regime_fast": fast,
                            "regime_slow": slow,
                            "entry_window": entry,
                            "exit_window": exit_window,
                            "direction": direction,
                        },
                        bars=bars4h,
                        funding=funding4h,
                        targets=tuple(targets),
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
    month_count = len(result.monthly_returns)
    return {
        "exposure": result.exposure,
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
        "bankrupt": result.bankrupt,
        "geometric_monthly_return": (
            (1 + result.net_return) ** (1 / month_count) - 1
            if month_count and result.net_return > -1
            else -1.0
        ),
        "monthly_returns": [
            {"month": label, "return": value} for label, value in result.monthly_returns
        ],
    }


def serialize(item: dict[str, Any], **extra: Any) -> dict[str, Any]:
    candidate = item["candidate"]
    return {
        "id": candidate.id,
        "parameters": candidate.parameters,
        "score": list(item["score"]),
        "train": item["train"],
        "validation": item["validation"],
        **extra,
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# BTCUSDT Regime-Filtered 4h Breakout",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        payload["definition"],
        "",
        "| Direction | Candidate | Train | Validation | Confirmation | Confirm DD | Trades |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["direction_winners"]:
        confirmation = row["confirmation"]
        lines.append(
            f"| {row['parameters']['direction']} | `{row['id']}` | "
            f"{row['train']['net_return']:.2%} | {row['validation']['net_return']:.2%} | "
            f"{confirmation['net_return']:.2%} | {confirmation['max_drawdown']:.2%} | "
            f"{confirmation['completed_trades']} |"
        )
    lines.extend(["", "## Selected Candidate", ""])
    selected = payload["selected"]
    if selected is None:
        lines.append("No candidate was positive in both development splits.")
    else:
        lines.extend(
            [
                f"`{selected['id']}`: train {selected['train']['net_return']:.2%}, validation "
                f"{selected['validation']['net_return']:.2%}, confirmation "
                f"{selected['confirmation']['net_return']:.2%}.",
                "",
                "| Exposure | Confirmation return | Monthly geometric | Max DD |",
                "|---:|---:|---:|---:|",
            ]
        )
        for row in payload["risk_ladder"]:
            lines.append(
                f"| {row['exposure']:.1f}x | {row['net_return']:.2%} | "
                f"{row['geometric_monthly_return']:.2%} | {row['max_drawdown']:.2%} |"
            )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"Status: `{payload['decision']['status']}`.",
            "",
            "The result is research-only and includes no Tick-level execution approval.",
        ]
    )
    return "\n".join(lines) + "\n"


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
