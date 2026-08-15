#!/home/spaceaic/env/.venv/bin/python
"""GPU worker for cross-asset, multi-horizon Transformer factor research."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mastermind_tick.deep_factor_v2 import DeepFactorV2Config, run_deep_factor_v2_mining


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report-root", type=Path)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ensemble-seeds", default="11,23,42")
    args = parser.parse_args()
    seeds = tuple(int(item.strip()) for item in args.ensemble_seeds.split(",") if item.strip())
    if not seeds:
        raise ValueError("at least one ensemble seed is required")
    config = DeepFactorV2Config(epochs=args.epochs, seed=args.seed, ensemble_seeds=seeds)

    def progress(stage: str, value: float) -> None:
        print(
            json.dumps(
                {"event": "progress", "stage": stage, "progress": value}, ensure_ascii=False
            ),
            flush=True,
        )

    try:
        report = run_deep_factor_v2_mining(
            args.database,
            args.output_root,
            config,
            progress,
            report_root=args.report_root,
        )
    except Exception as exc:
        print(json.dumps({"event": "error", "error": str(exc)}, ensure_ascii=False), flush=True)
        raise
    print(json.dumps({"event": "completed", "report": report}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
