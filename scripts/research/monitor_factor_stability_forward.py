#!/home/spaceaic/env/.venv/bin/python
"""Evaluate the frozen BTC/ETH reversal factors on post-lock data only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mastermind_tick.factor_stability_forward import (
    evaluate_frozen_factor_forward,
    load_factor_forward_market,
    load_frozen_factor_monitor,
    write_factor_forward_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("strategies/candidates/btc_eth_4h_reversal_factor_forward_v1.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/factor_stability/forward"),
    )
    args = parser.parse_args()
    monitor = load_frozen_factor_monitor(args.candidate)
    bars, funding = load_factor_forward_market(args.database, monitor)
    payload = evaluate_frozen_factor_forward(monitor, bars, funding)
    json_path, markdown_path = write_factor_forward_report(payload, args.output_dir)
    print(json_path)
    print(markdown_path)
    print(f"status={payload['status']} days={payload['complete_forward_days']}")


if __name__ == "__main__":
    main()
