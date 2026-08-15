#!/usr/bin/env python3
"""Evaluate the frozen BTC daily EMA candidate without parameter search."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from explore_btc_strategy_families import load_market

from mastermind_tick.bar_research import (
    aggregate_bars,
    ema_targets,
    evaluate_targets,
    funding_by_bar,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("strategies/candidates/btc_daily_ema_10_50_long_short_v1.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/btc_strategy_families/forward"),
    )
    args = parser.parse_args()
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    validate_candidate(candidate)
    source_bars, rates = load_market(args.database)
    bars = aggregate_bars(source_bars, candidate["parameters"]["bar_interval_minutes"])
    grouped_funding = funding_by_bar(bars, rates)
    forward_start = date.fromisoformat(candidate["forward_evidence_start_date"])
    selected = [bar for bar in bars if _utc_date(bar.start_ms) >= forward_start]
    report_id = f"{candidate['id']}-forward"
    if not selected:
        payload = base_payload(candidate, report_id)
        payload.update(
            {
                "status": "awaiting_forward_data",
                "complete_days": 0,
                "parameter_search": False,
                "result": None,
            }
        )
    else:
        parameters = candidate["parameters"]
        targets = ema_targets(
            bars,
            parameters["fast_period"],
            parameters["slow_period"],
            parameters["direction"],
        )
        start_ms = selected[0].start_ms
        end_ms = selected[-1].start_ms
        result = evaluate_targets(
            bars,
            targets,
            start_ms=start_ms,
            end_ms=end_ms,
            funding=grouped_funding,
            exposure=parameters["exposure"],
            fee_bps=Decimal(str(parameters["fee_bps"])),
            slippage_bps=Decimal(str(parameters["slippage_bps"])),
            close_final_position=False,
        )
        reject_drawdown = candidate["forward_gates"]["reject_at_forward_drawdown"]
        if result.max_drawdown <= reject_drawdown:
            status = "rejected_forward_drawdown"
        elif len(selected) < candidate["forward_gates"]["minimum_complete_days_for_interim_review"]:
            status = "monitoring_insufficient_forward_evidence"
        elif (
            result.completed_trades
            < candidate["forward_gates"]["minimum_completed_trades_for_interim_review"]
        ):
            status = "monitoring_insufficient_forward_trades"
        else:
            status = "interim_forward_pass" if result.net_return > 0 else "interim_forward_review"
        payload = base_payload(candidate, report_id)
        payload.update(
            {
                "status": status,
                "complete_days": len(selected),
                "first_day": _utc_date(selected[0].start_ms).isoformat(),
                "last_day": _utc_date(selected[-1].start_ms).isoformat(),
                "parameter_search": False,
                "result": {
                    "initial_equity": result.initial_equity,
                    "final_equity": result.final_equity,
                    "net_return": result.net_return,
                    "max_drawdown": result.max_drawdown,
                    "completed_trades": result.completed_trades,
                    "win_rate": result.win_rate,
                    "profit_factor": result.profit_factor,
                    "total_fees": result.total_fees,
                    "total_funding": result.total_funding,
                    "ending_position": result.ending_position,
                    "daily_returns": [
                        {"date": label, "return": value}
                        for label, value in result.daily_returns
                    ],
                },
            }
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"{report_id}.json"
    markdown_path = args.output_dir / f"{report_id}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(markdown(payload), encoding="utf-8")
    print(json_path)
    print(markdown_path)
    print(json.dumps({"status": payload["status"], "complete_days": payload["complete_days"]}))


def validate_candidate(candidate: dict[str, Any]) -> None:
    if candidate.get("id") != "btc-daily-ema-10-50-long-short-v1":
        raise ValueError("unexpected BTC EMA candidate id")
    if candidate.get("status") != "provisional_forward_candidate":
        raise ValueError("candidate is not eligible for forward monitoring")
    parameters = candidate.get("parameters")
    encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    if digest != candidate.get("parameter_hash"):
        raise ValueError("candidate parameter hash mismatch")
    expected = {
        "bar_interval_minutes": 1440,
        "direction": "long_short",
        "exposure": 1.0,
        "fast_period": 10,
        "fee_bps": 5.0,
        "slippage_bps": 2.0,
        "slow_period": 50,
        "strategy_family": "ema_trend",
        "symbol": "BTCUSDT",
    }
    if parameters != expected:
        raise ValueError("candidate parameters differ from frozen BTC EMA v1")


def base_payload(candidate: dict[str, Any], report_id: str) -> dict[str, Any]:
    return {
        "id": report_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate_id": candidate["id"],
        "candidate_status": candidate["status"],
        "approved_for_trading": False,
        "parameter_hash": candidate["parameter_hash"],
        "evidence_lock_date": candidate["evidence_lock_date"],
        "forward_evidence_start_date": candidate["forward_evidence_start_date"],
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# BTC Daily EMA(10,50) Frozen Forward Monitor",
        "",
        f"Generated: {payload['generated_at']}",
        f"Candidate: `{payload['candidate_id']}`",
        f"Parameter hash: `{payload['parameter_hash']}`",
        f"Evidence lock: `{payload['evidence_lock_date']} UTC`",
        f"Status: `{payload['status']}`",
        f"Complete forward days: {payload['complete_days']}",
        "Parameter search: no",
        "Trading approved: no",
        "",
    ]
    result = payload.get("result")
    if result is None:
        lines.append("No complete UTC day is available after the evidence lock.")
    else:
        lines.extend(
            [
                "| Return | Max drawdown | Trades | Fees | Funding | Ending position |",
                "|---:|---:|---:|---:|---:|---|",
                (
                    f"| {result['net_return']:.2%} | {result['max_drawdown']:.2%} | "
                    f"{result['completed_trades']} | {result['total_fees']:,.2f} | "
                    f"{result['total_funding']:,.2f} | {result['ending_position']} |"
                ),
                "",
                "| Date | Return |",
                "|---|---:|",
            ]
        )
        for row in result["daily_returns"]:
            lines.append(f"| {row['date']} | {row['return']:.2%} |")
    lines.extend(
        [
            "",
            "This deterministic report does not change parameters and is not a trading approval.",
            "",
        ]
    )
    return "\n".join(lines)


def _utc_date(timestamp_ms: int) -> date:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).date()


if __name__ == "__main__":
    main()
