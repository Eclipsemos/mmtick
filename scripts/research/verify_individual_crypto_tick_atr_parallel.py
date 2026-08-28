#!/usr/bin/env python3
"""Parallel, checkpointed runner for independent BTC/ETH tick ATR verification.

Candidate shards replay the complete development and validation periods.  The
parent process merges those results, selects the winner using only those two
periods, then runs confirmation and forward for that winner.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from mastermind_tick.config import load_settings
from mastermind_tick.research import research_presets


ROOT = Path(__file__).resolve().parents[2]
LEGACY_PATH = Path(__file__).with_name("verify_individual_crypto_tick_atr.py")


def _legacy():
    spec = importlib.util.spec_from_file_location("verify_individual_crypto_tick_atr", LEGACY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {LEGACY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker(task: dict) -> str:
    legacy = _legacy()
    instrument_id = task["instrument"]
    symbol, default_database = legacy.ASSETS[instrument_id]
    database = Path(task["database"] or default_database).resolve()
    settings = replace(load_settings(task["config"]), database_path=database)
    instrument = research_presets(settings)[instrument_id].instrument
    parameters = legacy.frozen_parameters()
    shard = parameters[task["shard"] :: task["shards"]]
    out_dir = Path(task["checkpoint_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{instrument_id}-shard-{task['shard']}.json"
    phase_paths = {
        label: out_dir / f"{instrument_id}-shard-{task['shard']}-{label}.json"
        for label in ("development_2024", "validation_2025")
    }

    phases = {}
    for label in ("development_2024", "validation_2025"):
        if phase_paths[label].exists() and not task["force"]:
            phases[label] = json.loads(phase_paths[label].read_text(encoding="utf-8"))
            print(f"{symbol}: using checkpoint {phase_paths[label]}", flush=True)
            continue
        start, end = legacy.PERIODS[label]
        print(
            f"{symbol}: shard {task['shard'] + 1}/{task['shards']} "
            f"replaying {label} ({len(shard)} candidates)",
            flush=True,
        )

        def progress(value: float, *, _label=label) -> None:
            bucket = int(value * 20)
            if bucket != progress.last.get(_label):
                progress.last[_label] = bucket
                print(
                    f"{symbol}: shard {task['shard'] + 1}/{task['shards']} "
                    f"{_label} {bucket * 5}%",
                    flush=True,
                )

        progress.last = {}
        metadata, results = legacy.run_parameter_grid(
            settings,
            instrument,
            shard,
            start_ms=legacy.timestamp_ms(start),
            end_ms=legacy.timestamp_ms(end) if end else None,
            direction="long_short",
            progress_callback=progress,
        )
        phases[label] = {"metadata": metadata, "results": [asdict(item) for item in results]}
        phase_paths[label].write_text(
            json.dumps(phases[label], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{symbol}: wrote phase checkpoint {phase_paths[label]}", flush=True)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "instrument_id": instrument_id,
        "symbol": symbol,
        "database": str(database),
        "shard": task["shard"],
        "shards": task["shards"],
        "scope": "parallel checkpoint shard; frozen BTC/ETH portfolio unchanged",
        "development_2024": phases["development_2024"],
        "validation_2025": phases["validation_2025"],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{symbol}: wrote checkpoint {path}", flush=True)
    return str(path)


def _load_results(paths: list[str], phase: str) -> dict:
    merged = {}
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in payload[phase]["results"]:
            key = (item["atr_period"], item["atr_multiplier"])
            merged[key] = item
    return merged


def _run_final(instrument_id: str, database: Path, config: str, checkpoint_dir: Path, winner_key):
    legacy = _legacy()
    symbol, _ = legacy.ASSETS[instrument_id]
    settings = replace(load_settings(config), database_path=database.resolve())
    instrument = research_presets(settings)[instrument_id].instrument
    parameters = [legacy.parameter_for_key(winner_key)]
    result = {}
    for label in ("confirmation_2026", "forward_observation"):
        start, end = legacy.PERIODS[label]
        print(f"{symbol}: winner ATR{winner_key} replaying {label}", flush=True)
        progress_bucket = [-1]

        def progress(value: float) -> None:
            bucket = int(value * 10)
            if bucket != progress_bucket[0]:
                progress_bucket[0] = bucket
                print(f"{symbol}: {label} {min(bucket * 10, 100)}%", flush=True)

        metadata, values = legacy.run_parameter_grid(
            settings,
            instrument,
            parameters,
            start_ms=legacy.timestamp_ms(start),
            end_ms=legacy.timestamp_ms(end) if end else None,
            direction="long_short",
            progress_callback=progress,
        )
        result[label] = {"metadata": metadata, "results": [asdict(item) for item in values]}
    path = checkpoint_dir / f"{instrument_id}-final.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--instrument", choices=("btc_perp", "eth_perp"), required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--shards", type=int, default=3)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.shards < 1 or args.workers < 1:
        parser.error("--shards and --workers must be positive")

    legacy = _legacy()
    _, default_database = legacy.ASSETS[args.instrument]
    database = (args.database or default_database).resolve()
    checkpoint_dir = (args.checkpoint_dir or args.output.parent / "checkpoints").resolve()
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {
            "config": args.config,
            "instrument": args.instrument,
            "database": str(database),
            "checkpoint_dir": str(checkpoint_dir),
            "shard": index,
            "shards": args.shards,
            "force": args.force,
        }
        for index in range(args.shards)
    ]
    with ProcessPoolExecutor(max_workers=min(args.workers, args.shards)) as pool:
        paths = list(pool.map(_worker, tasks))

    development = _load_results(paths, "development_2024")
    validation = _load_results(paths, "validation_2025")
    winner_key = legacy.select_development_winner(
        {key: legacy.ReplayResult(**value) for key, value in development.items()},
        {key: legacy.ReplayResult(**value) for key, value in validation.items()},
    )
    final = _run_final(args.instrument, database, args.config, checkpoint_dir, winner_key)
    winner_dev = legacy.ReplayResult(**development[winner_key])
    winner_val = legacy.ReplayResult(**validation[winner_key])
    winner_conf = legacy.ReplayResult(**final["confirmation_2026"]["results"][0])
    winner_forward = legacy.ReplayResult(**final["forward_observation"]["results"][0])
    gates = legacy.verification_gates(winner_dev, winner_val, winner_conf)
    failed = [name for name, passed in gates.items() if not passed]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "instrument_id": args.instrument,
        "symbol": legacy.ASSETS[args.instrument][0],
        "database": str(database),
        "scope": "independent Tick ATR verification; parallel candidate shards; frozen portfolio unchanged",
        "protocol": {
            "grid": "ATR periods 14/21/28 x multipliers 2/2.5/3",
            "candidate_shards": args.shards,
            "workers": min(args.workers, args.shards),
            "winner_inputs": ["development_2024", "validation_2025"],
            "confirmation_used_for_selection": False,
            "forward_used_for_selection": False,
        },
        "winner": {
            "atr_period": winner_key[0],
            "atr_multiplier": winner_key[1],
            "development": asdict(winner_dev),
            "validation": asdict(winner_val),
            "confirmation": asdict(winner_conf),
            "forward_observation": asdict(winner_forward),
            "gates": gates,
        },
        "development": {"results": list(development.values())},
        "validation": {"results": list(validation.values())},
        "confirmation": final["confirmation_2026"],
        "forward_observation": final["forward_observation"],
        "decision": {
            "status": "forward_candidate" if all(gates.values()) else "rejected",
            "approved_for_trading": False,
            "reason": (
                "The development-selected candidate passed all pre-forward tick gates."
                if not failed
                else "The development-selected candidate failed: " + ", ".join(failed) + "."
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(legacy.markdown(payload), encoding="utf-8")
    print(args.output)
    print(args.output.with_suffix(".md"))


if __name__ == "__main__":
    main()
