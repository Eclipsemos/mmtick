#!/home/spaceaic/env/.venv/bin/python
"""Run strict single-factor regime and walk-forward stability research."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mastermind_tick.factor_stability import (
    FactorStabilityConfig,
    run_factor_stability_study,
    write_factor_stability_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument("--metrics", type=Path, default=Path("data/futures_metrics"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("reports/experiments/factor_stability"),
    )
    args = parser.parse_args()
    report = run_factor_stability_study(
        args.database,
        args.metrics,
        FactorStabilityConfig(),
    )
    json_path, markdown_path = write_factor_stability_report(report, args.output_root)
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
