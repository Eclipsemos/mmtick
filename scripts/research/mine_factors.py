#!/usr/bin/env python3
"""Run research-only causal factor mining for BTCUSDT, ETHUSDT, and SOXLUSDT."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mastermind_tick.config import load_settings
from mastermind_tick.factor_mining import FactorMiningConfig, load_market, mine_instrument

DEFAULT_OUTPUT = Path("reports/experiments/factor_mining")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--instruments",
        default="btc_perp,eth_perp,soxl_perp",
        help="Comma-separated subset of btc_perp, eth_perp, and soxl_perp",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--overview-only",
        action="store_true",
        help="Rebuild the daily overview from existing per-instrument JSON results",
    )
    args = parser.parse_args()

    requested = tuple(item.strip() for item in args.instruments.split(",") if item.strip())
    allowed = {"btc_perp", "eth_perp", "soxl_perp"}
    unknown = set(requested) - allowed
    if not requested or unknown:
        raise ValueError(f"unknown or empty instruments: {', '.join(sorted(unknown))}")

    settings = load_settings(args.config)
    database = args.database or settings.database_path
    generated_at = datetime.now(UTC)
    output_dir = args.output_root / generated_at.date().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    overview: list[dict[str, Any]] = []
    if args.overview_only:
        for instrument_id in requested:
            path = output_dir / f"{instrument_id}.json"
            if not path.exists():
                raise ValueError(f"missing existing factor-mining result: {path}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            overview.append(
                {
                    "instrument_id": instrument_id,
                    "status": payload["decision"]["status"],
                    "candidate_count": payload["candidate_count"],
                    "selected": payload["selected"]["id"] if payload["selected"] else None,
                }
            )
        overview_path = output_dir / "README.md"
        overview_path.write_text(overview_markdown(generated_at, overview), encoding="utf-8")
        print(overview_path)
        return
    for instrument_id in requested:
        bars, funding = load_market(database, instrument_id)
        config = _config_for(instrument_id)
        payload = mine_instrument(bars, funding, config)
        payload["generated_at"] = generated_at.isoformat()
        json_path = output_dir / f"{instrument_id}.json"
        markdown_path = output_dir / f"{instrument_id}.md"
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        markdown_path.write_text(markdown(payload), encoding="utf-8")
        overview.append(
            {
                "instrument_id": instrument_id,
                "status": payload["decision"]["status"],
                "candidate_count": payload["candidate_count"],
                "selected": payload["selected"]["id"] if payload["selected"] else None,
            }
        )
        print(json_path)
        print(markdown_path)
    overview_path = output_dir / "README.md"
    overview_path.write_text(overview_markdown(generated_at, overview), encoding="utf-8")
    print(overview_path)


def _config_for(instrument_id: str) -> FactorMiningConfig:
    if instrument_id == "soxl_perp":
        return FactorMiningConfig(instrument_id=instrument_id, direction_options=("long_only",))
    return FactorMiningConfig(
        instrument_id=instrument_id,
        direction_options=("long_only", "long_short"),
    )


def markdown(payload: dict[str, Any]) -> str:
    selected = payload["selected"]
    language = payload["formula_language"]
    features = ", ".join(f"`{item}`" for item in language["features"])
    unary = ", ".join(f"`{item}`" for item in language["unary_operators"])
    binary = ", ".join(f"`{item}`" for item in language["binary_operators"])
    lines = [
        f"# {payload['instrument_id']} Causal Factor Mining",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        (
            "This is a research-only implementation inspired by AlphaGPT's formula discovery "
            "concept. It uses a constrained causal formula DSL, not AlphaGPT's model, data "
            "pipeline, or execution stack."
        ),
        "",
        "## Data And Execution",
        "",
        f"- Coverage: {payload['data']['first_bar']} to {payload['data']['last_bar']}.",
        (
            f"- Complete source bars: {payload['data']['source_bars_15m']:,}; "
            f"funding events: {payload['data']['funding_events']:,}."
        ),
        (
            f"- Candidate count: {payload['candidate_count']:,}; "
            f"development eligible: {payload['development_eligible_count']:,}."
        ),
        (
            f"- Costs: {payload['execution']['fee_bps_per_fill']:g} bps fee plus "
            f"{payload['execution']['slippage_bps_per_fill']:g} bps slippage per fill; "
            "historical funding included."
        ),
        "- Signals: closed bar; execution: next bar open; exposure: 1.0x; liquidation not modeled.",
        "",
        "## Causal Formula Language",
        "",
        f"- Features: {features}.",
        f"- Unary operators: {unary}.",
        f"- Binary operators: {binary}.",
        f"- Causality: {language['causality']}",
        "",
        "## Selected Development Formula",
        "",
    ]
    if selected is None:
        lines.append("No valid candidate was available for this data range.")
    else:
        lines.extend(
            [
                f"- ID: `{selected['id']}`",
                f"- Formula: `{selected['formula']['display']}`",
                f"- Postfix tokens: `{', '.join(selected['formula']['tokens'])}`",
                f"- Bar interval / direction / threshold: {selected['interval_minutes']}m / "
                f"{selected['direction']} / {selected['threshold']:g}.",
                "",
                "| Split | Return | Max drawdown | Trades | Positive months |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for name in ("train", "validation", "confirmation"):
            result = selected[name]
            lines.append(
                f"| {name} | {result['net_return']:.2%} | {result['max_drawdown']:.2%} | "
                f"{result['completed_trades']} | {result['positive_month_rate']:.0%} |"
            )
        lines.extend(
            [
                "",
                (
                    "Top-ten development-neighbor confirmation pass rate: "
                    f"{payload['neighbor_confirmation_pass_rate']:.0%}."
                ),
                "",
                "| Stability gate | Pass |",
                "|---|---|",
            ]
        )
        for name, passed in payload["stability_gates"].items():
            lines.append(f"| {name} | {'yes' if passed else 'no'} |")
        if payload["stress"]:
            stress = payload["stress"]["confirmation"]
            lines.extend(
                [
                    "",
                    (
                        "Stress confirmation at 10 bps fee plus 5 bps slippage per fill: "
                        f"{stress['net_return']:.2%}, max drawdown {stress['max_drawdown']:.2%}."
                    ),
                ]
            )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"Status: `{payload['decision']['status']}`.",
            "",
            payload["decision"]["reason"],
            "",
            "No formula in this report is authorized for paper or live execution.",
            "",
        ]
    )
    return "\n".join(lines)


def overview_markdown(generated_at: datetime, rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Causal Factor Mining",
        "",
        f"Generated: {generated_at.isoformat()}",
        "",
        "| Instrument | Candidates | Selected development formula | Status |",
        "|---|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['instrument_id']} | {row['candidate_count']:,} | "
            f"`{row['selected'] or '-'}` | `{row['status']}` |"
        )
    lines.extend(
        [
            "",
            "Formula search is isolated to research and cannot create orders or modify "
            "live settings.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
