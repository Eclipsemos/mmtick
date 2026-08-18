#!/usr/bin/env python3
"""Evaluate one frozen SOXL volatility-spread candidate without parameter search."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from mastermind_tick.volatility_spread_forward import (
    evaluate_frozen_forward,
    load_forward_market,
    load_frozen_candidate,
    render_forward_markdown,
)


def main() -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("strategies/candidates/soxl_volatility_spread_true_range_v1.json"),
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/soxl_volatility_spread/forward"),
    )
    args = parser.parse_args()

    candidate = load_frozen_candidate(args.candidate)
    bars, funding_by_bar, executions = load_forward_market(args.database, candidate)
    payload = evaluate_frozen_forward(
        candidate,
        bars,
        funding_by_bar,
        executions,
        as_of_date=args.as_of,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"{candidate.id}-forward.json"
    markdown_path = args.output_dir / f"{candidate.id}-forward.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_forward_markdown(payload), encoding="utf-8")
    print(markdown_path)
    return payload


if __name__ == "__main__":
    main()
