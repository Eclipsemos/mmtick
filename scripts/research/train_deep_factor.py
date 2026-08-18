#!/home/spaceaic/env/.venv/bin/python
"""GPU worker for research-only causal Transformer factor mining."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mastermind_tick.deep_factor import DeepFactorConfig, run_deep_factor_mining


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report-root", type=Path)
    parser.add_argument("--instruments", default="btc_perp,eth_perp")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--sequence-length", type=int, default=96)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    instruments = tuple(item.strip() for item in args.instruments.split(",") if item.strip())
    config = DeepFactorConfig(
        instruments=instruments,
        epochs=args.epochs,
        sequence_length=args.sequence_length,
        seed=args.seed,
    )

    def progress(stage: str, value: float) -> None:
        print(
            json.dumps(
                {"event": "progress", "stage": stage, "progress": value}, ensure_ascii=False
            ),
            flush=True,
        )

    try:
        report = run_deep_factor_mining(
            args.database, args.output_root, config, progress, report_root=args.report_root
        )
    except Exception as exc:
        print(json.dumps({"event": "error", "error": str(exc)}, ensure_ascii=False), flush=True)
        raise
    print(json.dumps({"event": "completed", "report": report}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
