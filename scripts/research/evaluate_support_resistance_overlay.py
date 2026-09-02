#!/usr/bin/env python3
"""Evaluate support/resistance as an overlay on the frozen BTC/ETH reversal factor.

This is an offline adapter. MMTICK owns bar aggregation, funding, and next-open replay;
the GPL-3.0 target project is imported only from its separate virtual environment to
produce causal H4 level states. No execution or paper configuration is changed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np


MMTICK_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = Path("/home/ldtdev/qt/Detect_support_and_resistance_levels")
DEFAULT_DATA = DEFAULT_TARGET / "data" / "mmtick_crypto"
DEFAULT_DATABASE = MMTICK_ROOT / "data" / "paper.db"
DEFAULT_OUTPUT = MMTICK_ROOT / "reports" / "experiments" / "support_resistance_crypto" / "overlay"
ASSETS = {"btc_perp": "BTCUSDT", "eth_perp": "ETHUSDT"}
WARMUP_BARS = 800
LEVEL_WIDTH_CAP_ATR = 1.5
MIN_LEVEL_DISTANCE_ATR = 0.5
MAX_LEVEL_DISTANCE_ATR = 5.0
FACTOR_LOOKBACK_BARS = 6
FEE_BPS = Decimal("5")
SLIPPAGE_BPS = Decimal("2")
STRESS_FEE_BPS = Decimal("10")
STRESS_SLIPPAGE_BPS = Decimal("5")


@dataclass(frozen=True)
class LevelState:
    support_distance_atr: float | None = None
    resistance_distance_atr: float | None = None
    support_edge_score: float | None = None
    resistance_edge_score: float | None = None
    support_width_atr: float | None = None
    resistance_width_atr: float | None = None


def _load_modules(target_root: Path):
    sys.path.insert(0, str(target_root))
    sys.path.insert(0, str(MMTICK_ROOT / "src"))
    from mastermind_tick.bar_research import (
        aggregate_bars,
        evaluate_targets,
        funding_by_bar,
    )
    from mastermind_tick.factor_mining import load_market
    from src.local_data_loader import load_kline  # type: ignore[import-not-found]
    from src.srlab.base import Ctx  # type: ignore[import-not-found]
    from src.srlab.data import to_symbol  # type: ignore[import-not-found]
    from src.srlab.detectors import V3Fusion  # type: ignore[import-not-found]
    from src.srlab.pivots import zigzag  # type: ignore[import-not-found]

    return (
        aggregate_bars,
        evaluate_targets,
        funding_by_bar,
        load_market,
        load_kline,
        Ctx,
        to_symbol,
        V3Fusion,
        zigzag,
    )


def _level_state(levels: list[Any]) -> LevelState:
    selected = {
        kind: sorted(
            (
                level
                for level in levels
                if level.kind == kind
                and level.width_atr <= LEVEL_WIDTH_CAP_ATR
                and MIN_LEVEL_DISTANCE_ATR
                <= abs(level.dist_atr)
                <= MAX_LEVEL_DISTANCE_ATR
            ),
            key=lambda level: (abs(level.dist_atr), -level.score),
        )
        for kind in ("support", "resistance")
    }
    support = selected["support"][0] if selected["support"] else None
    resistance = selected["resistance"][0] if selected["resistance"] else None
    return LevelState(
        support_distance_atr=abs(float(support.dist_atr)) if support else None,
        resistance_distance_atr=abs(float(resistance.dist_atr)) if resistance else None,
        support_edge_score=float(support.score) if support else None,
        resistance_edge_score=float(resistance.score) if resistance else None,
        support_width_atr=float(support.width_atr) if support else None,
        resistance_width_atr=float(resistance.width_atr) if resistance else None,
    )


def _build_states(
    target_root: Path, data_dir: Path, asset: str, bars: list[Any]
) -> list[LevelState | None]:
    (
        _aggregate_bars,
        _evaluate_targets,
        _funding_by_bar,
        _load_market,
        load_kline,
        Ctx,
        to_symbol,
        V3Fusion,
        zigzag,
    ) = _load_modules(target_root)
    code = ASSETS[asset]
    symbol = to_symbol(code, load_kline(str(data_dir), code, "H4"))
    if len(symbol) != len(bars):
        raise ValueError(
            f"{asset} target H4 bars ({len(symbol)}) do not match MMTICK ({len(bars)})"
        )
    for index, bar in enumerate(bars):
        if (
            float(symbol.open_[index]) != float(bar.open)
            or float(symbol.high[index]) != float(bar.high)
            or float(symbol.low[index]) != float(bar.low)
            or float(symbol.close[index]) != float(bar.close)
        ):
            raise ValueError(f"{asset} OHLC mismatch at H4 index {index}")

    pivots = zigzag(symbol.high, symbol.low, symbol.atr, k_atr=2.0)
    detector = V3Fusion()
    states: list[LevelState | None] = [None] * len(bars)
    for index in range(WARMUP_BARS, len(bars)):
        atr = float(symbol.atr[index])
        if not np.isfinite(atr) or atr <= 0:
            continue
        context = Ctx(
            code=code,
            t=index,
            open_=symbol.open_[: index + 1],
            high=symbol.high[: index + 1],
            low=symbol.low[: index + 1],
            close=symbol.close[: index + 1],
            volume=symbol.volume[: index + 1],
            atr=atr,
            tick=symbol.tick,
            atr_arr=symbol.atr[: index + 1],
            pivots=pivots.view_at(index, max_lookback=600),
        )
        states[index] = _level_state(detector.detect(context))
    return states


def _factor_scores(btc: list[Any]) -> list[Decimal | None]:
    source = btc
    scores: list[Decimal | None] = [None] * len(source)
    for index in range(FACTOR_LOOKBACK_BARS, len(source)):
        scores[index] = -(
            source[index].close
            / source[index - FACTOR_LOOKBACK_BARS].close
            - Decimal("1")
        )
    return scores


def _base_targets(scores: list[Decimal | None]) -> tuple[int | None, ...]:
    return tuple(
        None if score is None else 1 if score > 0 else -1 if score < 0 else 0
        for score in scores
    )


def _overlay_targets(
    base: tuple[int | None, ...],
    states: list[LevelState | None],
    mode: str,
    location_cap_atr: float,
    room_floor_atr: float,
) -> tuple[int | None, ...]:
    result: list[int | None] = []
    for target, state in zip(base, states, strict=True):
        if target is None or state is None:
            result.append(None)
            continue
        if target > 0:
            location_ok = (
                state.support_distance_atr is not None
                and state.support_distance_atr <= location_cap_atr
            )
            room_ok = (
                state.resistance_distance_atr is None
                or state.resistance_distance_atr >= room_floor_atr
            )
        elif target < 0:
            location_ok = (
                state.resistance_distance_atr is not None
                and state.resistance_distance_atr <= location_cap_atr
            )
            room_ok = (
                state.support_distance_atr is None
                or state.support_distance_atr >= room_floor_atr
            )
        else:
            result.append(0)
            continue
        allowed = (
            mode == "location" and location_ok
            or mode == "room" and room_ok
            or mode == "location_room" and location_ok and room_ok
        )
        result.append(target if allowed else 0)
    return tuple(result)


def _period_bounds(bars: list[Any]) -> dict[str, tuple[int, int]]:
    def utc_ms(value: str) -> int:
        return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)

    last = bars[-1].start_ms
    return {
        "development_2022_2025": (utc_ms("2022-01-01"), min(last, utc_ms("2025-12-31T23:59:59"))),
        "confirmation_2026": (utc_ms("2026-01-01"), last),
    }


def _evaluate(
    bars: list[Any],
    funding: list[list[Any]],
    targets: tuple[int | None, ...],
    bounds: tuple[int, int],
    evaluate_targets: Any,
    fee_bps: Decimal,
    slippage_bps: Decimal,
) -> dict[str, Any]:
    start_ms, end_ms = bounds
    start_index = next(index for index, bar in enumerate(bars) if bar.start_ms >= start_ms)
    isolated = list(targets)
    for index in range(start_index):
        isolated[index] = None
    result = evaluate_targets(
        bars,
        tuple(isolated),
        start_ms=start_ms,
        end_ms=end_ms,
        funding=funding,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        close_final_position=True,
    )
    return {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
        "final_equity": result.final_equity,
    }


def run(target_root: Path, data_dir: Path, database: Path) -> dict[str, Any]:
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise RuntimeError(
            "set PYTHONHASHSEED=0 for reproducible target detector output"
        )
    (
        aggregate_bars,
        evaluate_targets,
        funding_by_bar,
        load_market,
        _load_kline,
        _Ctx,
        _to_symbol,
        _V3Fusion,
        _zigzag,
    ) = _load_modules(target_root)
    loaded = {asset: load_market(database, asset) for asset in ASSETS}
    bars = {asset: aggregate_bars(loaded[asset][0], 240) for asset in ASSETS}
    if len(bars["btc_perp"]) != len(bars["eth_perp"]):
        raise ValueError("BTC and ETH H4 bars are not aligned")
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in ASSETS}
    states = {asset: _build_states(target_root, data_dir, asset, bars[asset]) for asset in ASSETS}
    scores = {asset: _factor_scores(bars["btc_perp"]) for asset in ASSETS}
    base = {asset: _base_targets(scores[asset]) for asset in ASSETS}
    configurations = {
        "baseline": ("baseline", 0.0, 0.0),
        "location_1p0": ("location", 1.0, 0.0),
        "location_1p5": ("location", 1.5, 0.0),
        "location_2p0": ("location", 2.0, 0.0),
        "room_0p75": ("room", 0.0, 0.75),
        "room_1p0": ("room", 0.0, 1.0),
        "room_1p5": ("room", 0.0, 1.5),
        "location_room_1p5_1p0": ("location_room", 1.5, 1.0),
        "location_room_2p0_1p0": ("location_room", 2.0, 1.0),
    }
    periods = _period_bounds(bars["btc_perp"])
    results: dict[str, Any] = {}
    for asset in ASSETS:
        results[asset] = {}
        for name, (mode, location_cap, room_floor) in configurations.items():
            targets = (
                base[asset]
                if mode == "baseline"
                else _overlay_targets(base[asset], states[asset], mode, location_cap, room_floor)
            )
            results[asset][name] = {
                period: {
                    "normal": _evaluate(
                        bars[asset],
                        funding[asset],
                        targets,
                        bounds,
                        evaluate_targets,
                        FEE_BPS,
                        SLIPPAGE_BPS,
                    ),
                    "stress": _evaluate(
                        bars[asset],
                        funding[asset],
                        targets,
                        bounds,
                        evaluate_targets,
                        STRESS_FEE_BPS,
                        STRESS_SLIPPAGE_BPS,
                    ),
                }
                for period, bounds in periods.items()
            }
    return {
        "experiment": {
            "id": "btc-eth-4h-reversal-support-resistance-overlay-v1",
            "target_project": str(target_root),
            "data_dir": str(data_dir),
            "database": str(database),
            "assets": list(ASSETS),
            "timeframe": "H4",
            "signal_timing": "closed H4 bar",
            "fill_timing": "next H4 bar open",
            "factor": "BTC prior 6-bar return reversed for BTC and ETH",
            "warmup_bars": WARMUP_BARS,
            "level_width_cap_atr": LEVEL_WIDTH_CAP_ATR,
            "level_distance_atr": [MIN_LEVEL_DISTANCE_ATR, MAX_LEVEL_DISTANCE_ATR],
            "normal_cost_bps_round_trip": 14.0,
            "stress_cost_bps_round_trip": 30.0,
            "funding_included": True,
            "liquidation_modeled": False,
            "python_hash_seed": 0,
        },
        "data": {
            asset: {
                "bars_15m": len(loaded[asset][0]),
                "bars_h4": len(bars[asset]),
                "first": datetime.fromtimestamp(bars[asset][0].start_ms / 1000, UTC).isoformat(),
                "last": datetime.fromtimestamp(bars[asset][-1].start_ms / 1000, UTC).isoformat(),
                "funding_events": len(loaded[asset][1]),
            }
            for asset in ASSETS
        },
        "configurations": {
            name: {"mode": mode, "location_cap_atr": location, "room_floor_atr": room}
            for name, (mode, location, room) in configurations.items()
        },
        "results": results,
        "decision": {
            "status": "research_diagnostic",
            "approved_for_trading": False,
            "promotion_gates": [
                "positive normal and stress net return in both development and fresh confirmation",
                "no material drawdown deterioration versus baseline",
                "improvement must persist across BTC and ETH without selecting on confirmation",
            ],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BTC/ETH Reversal + Support/Resistance Overlay",
        "",
        "Research-only causal H4 comparison. No paper/live execution integration.",
        "",
        f"- Data: `{json.dumps(report['data'], sort_keys=True)}`",
        f"- Signal: `{report['experiment']['signal_timing']}`; "
        f"fill: `{report['experiment']['fill_timing']}`.",
        "- Baseline: reverse BTC prior six H4-bar return; the same score drives BTC and ETH.",
        "- Funding is included. Normal cost is 14 bps round trip; stress cost is 30 bps.",
        "",
        "## Results",
        "",
        "| Asset | Configuration | Period | Normal return | Stress return | Normal DD | Trades |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for asset, configurations in report["results"].items():
        for configuration, periods in configurations.items():
            for period, values in periods.items():
                normal = values["normal"]
                stress = values["stress"]
                lines.append(
                    f"| {asset} | {configuration} | {period} | {normal['net_return']:.2%} | "
                    f"{stress['net_return']:.2%} | {normal['max_drawdown']:.2%} | "
                    f"{normal['completed_trades']} |"
                )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Configurations are diagnostic candidates, not a parameter-selected strategy. "
            "The development period is 2022-2025; 2026 is reported separately as confirmation. "
            "The target project's probability models are not used, and no target source is "
            "copied into MMTICK.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-project", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.target_project, args.data_dir, args.database)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "btc-eth-4h-reversal-support-resistance-overlay-v1.json"
    markdown_path = args.output_dir / "btc-eth-4h-reversal-support-resistance-overlay-v1.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
