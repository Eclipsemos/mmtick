#!/usr/bin/env python3
"""Scan causal BTC/ETH support-resistance strategy families.

The scan is deliberately offline. MMTICK supplies the 15m-to-H4 bars, funding, and
next-open replay; the GPL-3.0 project is used only as a separate causal level engine.
Parameters are declared below before validation/confirmation results are calculated.
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
from typing import Any, Callable

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = Path("/home/ldtdev/qt/Detect_support_and_resistance_levels")
DEFAULT_DATA = DEFAULT_TARGET / "data" / "mmtick_crypto"
DEFAULT_DATABASE = ROOT / "data" / "paper.db"
DEFAULT_OUTPUT = ROOT / "reports" / "experiments" / "support_resistance_crypto" / "scan"
ASSETS = {"btc_perp": "BTCUSDT", "eth_perp": "ETHUSDT"}
WARMUP_BARS = 800
LOOKBACK_BARS = 6
LEVEL_WIDTH_CAP_ATR = 1.5
MIN_LEVEL_DISTANCE_ATR = 0.5
MAX_LEVEL_DISTANCE_ATR = 5.0
NORMAL_FEE_BPS = Decimal("5")
NORMAL_SLIPPAGE_BPS = Decimal("2")
STRESS_FEE_BPS = Decimal("10")
STRESS_SLIPPAGE_BPS = Decimal("5")


@dataclass(frozen=True)
class LevelState:
    support_distance_atr: float | None = None
    resistance_distance_atr: float | None = None
    support_edge: float | None = None
    resistance_edge: float | None = None
    support_low: float | None = None
    support_high: float | None = None
    resistance_low: float | None = None
    resistance_high: float | None = None
    atr: float | None = None


@dataclass(frozen=True)
class StrategySpec:
    name: str
    family: str
    parameters: dict[str, Any]
    build_targets: Callable[[dict[str, Any]], tuple[int | None, ...]]


def _modules(target_root: Path):
    sys.path.insert(0, str(target_root))
    sys.path.insert(0, str(ROOT / "src"))
    from mastermind_tick.bar_research import aggregate_bars, evaluate_targets, funding_by_bar
    from mastermind_tick.factor_mining import load_market
    from src.local_data_loader import load_kline  # type: ignore[import-not-found]
    from src.srlab.base import Ctx  # type: ignore[import-not-found]
    from src.srlab.data import to_symbol  # type: ignore[import-not-found]
    from src.srlab.detectors import V3Fusion  # type: ignore[import-not-found]
    from src.srlab.pivots import zigzag  # type: ignore[import-not-found]

    return aggregate_bars, evaluate_targets, funding_by_bar, load_market, load_kline, Ctx, to_symbol, V3Fusion, zigzag


def _select_level(levels: list[Any], kind: str) -> Any | None:
    candidates = [
        level
        for level in levels
        if level.kind == kind
        and level.width_atr <= LEVEL_WIDTH_CAP_ATR
        and MIN_LEVEL_DISTANCE_ATR <= abs(level.dist_atr) <= MAX_LEVEL_DISTANCE_ATR
    ]
    return min(candidates, key=lambda level: (abs(level.dist_atr), -level.score), default=None)


def _state(levels: list[Any], atr: float) -> LevelState:
    support = _select_level(levels, "support")
    resistance = _select_level(levels, "resistance")
    return LevelState(
        support_distance_atr=abs(float(support.dist_atr)) if support else None,
        resistance_distance_atr=abs(float(resistance.dist_atr)) if resistance else None,
        support_edge=float(support.score) if support else None,
        resistance_edge=float(resistance.score) if resistance else None,
        support_low=float(support.low) if support else None,
        support_high=float(support.high) if support else None,
        resistance_low=float(resistance.low) if resistance else None,
        resistance_high=float(resistance.high) if resistance else None,
        atr=atr,
    )


def _build_states(target_root: Path, data_dir: Path, asset: str, bars: list[Any]) -> list[LevelState | None]:
    _aggregate, _evaluate, _funding, _load_market, load_kline, Ctx, to_symbol, V3Fusion, zigzag = _modules(
        target_root
    )
    code = ASSETS[asset]
    symbol = to_symbol(code, load_kline(str(data_dir), code, "H4"))
    if len(symbol) != len(bars):
        raise ValueError(f"{asset}: target H4 count differs from MMTICK H4 count")
    for index, bar in enumerate(bars):
        source = (symbol.open_[index], symbol.high[index], symbol.low[index], symbol.close[index])
        target = (float(bar.open), float(bar.high), float(bar.low), float(bar.close))
        if source != target:
            raise ValueError(f"{asset}: OHLC mismatch at H4 index {index}")
    pivots = zigzag(symbol.high, symbol.low, symbol.atr, k_atr=2.0)
    detector = V3Fusion()
    states: list[LevelState | None] = [None] * len(bars)
    for index in range(WARMUP_BARS, len(bars)):
        atr = float(symbol.atr[index])
        if np.isfinite(atr) and atr > 0:
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
            states[index] = _state(detector.detect(context), atr)
    return states


def _factor_scores(btc_bars: list[Any]) -> list[Decimal | None]:
    values: list[Decimal | None] = [None] * len(btc_bars)
    for index in range(LOOKBACK_BARS, len(btc_bars)):
        values[index] = -(
            btc_bars[index].close / btc_bars[index - LOOKBACK_BARS].close - Decimal("1")
        )
    return values


def _direction_targets(scores: list[Decimal | None], threshold: float = 0.0) -> tuple[int | None, ...]:
    level = Decimal(str(threshold))
    return tuple(
        None
        if value is None
        else 1
        if value > level
        else -1
        if value < -level
        else 0
        for value in scores
    )


def _location_filter(
    base: tuple[int | None, ...],
    states: list[LevelState | None],
    location_cap: float,
    room_floor: float = 0.0,
    edge_floor: float = -float("inf"),
) -> tuple[int | None, ...]:
    result: list[int | None] = []
    for target, state in zip(base, states, strict=True):
        if target is None or state is None:
            result.append(None)
            continue
        if target > 0:
            near = state.support_distance_atr
            edge = state.support_edge
            room = state.resistance_distance_atr
        elif target < 0:
            near = state.resistance_distance_atr
            edge = state.resistance_edge
            room = state.support_distance_atr
        else:
            result.append(0)
            continue
        accepted = (
            near is not None
            and near <= location_cap
            and (edge is not None and edge >= edge_floor)
            and (room is None or room >= room_floor)
        )
        result.append(target if accepted else 0)
    return tuple(result)


def _fade_entries(
    states: list[LevelState | None], location_cap: float, edge_floor: float
) -> list[int]:
    entries: list[int] = []
    for state in states:
        options: list[tuple[float, float, int]] = []
        if (
            state is not None
            and state.support_distance_atr is not None
            and state.support_edge is not None
            and state.support_distance_atr <= location_cap
            and state.support_edge >= edge_floor
        ):
            options.append((state.support_distance_atr, -state.support_edge, 1))
        if (
            state is not None
            and state.resistance_distance_atr is not None
            and state.resistance_edge is not None
            and state.resistance_distance_atr <= location_cap
            and state.resistance_edge >= edge_floor
        ):
            options.append((state.resistance_distance_atr, -state.resistance_edge, -1))
        entries.append(min(options)[2] if options else 0)
    return entries


def _fixed_hold_targets(entries: list[int], hold_bars: int, cooldown: bool = True) -> tuple[int, ...]:
    result: list[int] = []
    active = 0
    age = 0
    blocked = False
    for entry in entries:
        if active:
            age += 1
            if age >= hold_bars:
                active = 0
                age = 0
                blocked = cooldown
                result.append(0)
                continue
            result.append(active)
            continue
        if blocked:
            if entry == 0:
                blocked = False
            result.append(0)
            continue
        if entry:
            active = entry
            age = 0
            result.append(active)
        else:
            result.append(0)
    return tuple(result)


def _breakout_entries(
    bars: list[Any], states: list[LevelState | None], buffer_atr: float
) -> list[int]:
    entries = [0] * len(bars)
    for index in range(1, len(bars)):
        state = states[index - 1]
        if state is None or state.atr is None:
            continue
        previous_close = float(bars[index - 1].close)
        close = float(bars[index].close)
        resistance_trigger = (
            state.resistance_high + buffer_atr * state.atr
            if state.resistance_high is not None
            else None
        )
        support_trigger = (
            state.support_low - buffer_atr * state.atr
            if state.support_low is not None
            else None
        )
        long_break = resistance_trigger is not None and previous_close <= resistance_trigger < close
        short_break = support_trigger is not None and previous_close >= support_trigger > close
        entries[index] = 1 if long_break and not short_break else -1 if short_break and not long_break else 0
    return entries


def _combine_entries(
    entries: list[int], factor_targets: tuple[int | None, ...]
) -> list[int]:
    return [
        entry if entry and factor == entry else 0
        for entry, factor in zip(entries, factor_targets, strict=True)
    ]


def _periods(bars: list[Any]) -> dict[str, tuple[int, int]]:
    def ms(value: str) -> int:
        return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)

    last = bars[-1].start_ms
    return {
        "predevelopment_2020_2021": (ms("2020-01-01"), ms("2021-12-31T23:59:59")),
        "development_2022_2023": (ms("2022-01-01"), ms("2023-12-31T23:59:59")),
        "validation_2024_2025": (ms("2024-01-01"), ms("2025-12-31T23:59:59")),
        "confirmation_2026": (ms("2026-01-01"), last),
    }


def _evaluate(
    bars: list[Any],
    funding: list[list[Any]],
    targets: tuple[int | None, ...],
    bounds: tuple[int, int],
    evaluate_targets: Any,
    fee: Decimal,
    slippage: Decimal,
) -> dict[str, Any]:
    start_ms, end_ms = bounds
    start = next(index for index, bar in enumerate(bars) if bar.start_ms >= start_ms)
    isolated = list(targets)
    isolated[:start] = [None] * start
    result = evaluate_targets(
        bars,
        tuple(isolated),
        start_ms=start_ms,
        end_ms=end_ms,
        funding=funding,
        fee_bps=fee,
        slippage_bps=slippage,
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


def _specs(
    states: dict[str, list[LevelState | None]],
    factor: dict[str, tuple[int | None, ...]],
    bars: dict[str, list[Any]],
) -> list[StrategySpec]:
    specs: list[StrategySpec] = []

    def factor_targets(params: dict[str, Any]) -> tuple[int | None, ...]:
        return _direction_targets(factor[params["asset"]], params["threshold"])

    def location_targets(params: dict[str, Any]) -> tuple[int | None, ...]:
        asset = params["asset"]
        base = _direction_targets(factor[asset], params["threshold"])
        return _location_filter(
            base,
            states[asset],
            params["location_cap"],
            params["room_floor"],
            params["edge_floor"],
        )

    def fade_targets(params: dict[str, Any]) -> tuple[int | None, ...]:
        asset = params["asset"]
        entries = _fade_entries(states[asset], params["location_cap"], params["edge_floor"])
        if params["factor_confirm"]:
            entries = _combine_entries(entries, _direction_targets(factor[asset], params["threshold"]))
        held = _fixed_hold_targets(entries, params["hold_bars"])
        return tuple(held)

    def breakout_targets(params: dict[str, Any]) -> tuple[int | None, ...]:
        asset = params["asset"]
        entries = _breakout_entries(bars[asset], states[asset], params["buffer_atr"])
        if params["factor_confirm"]:
            entries = _combine_entries(entries, _direction_targets(factor[asset], params["threshold"]))
        return _fixed_hold_targets(entries, params["hold_bars"])

    for asset in ASSETS:
        for threshold in (0.0, 0.0025, 0.005, 0.01):
            name = f"factor_{asset}_th{str(threshold).replace('.', 'p')}"
            params = {"asset": asset, "threshold": threshold}
            specs.append(StrategySpec(name, "factor", params, factor_targets))
        for location in (0.75, 1.0, 1.5, 2.0, 2.5):
            for edge in (0.0, 2.0, 2.5):
                params = {
                    "asset": asset,
                    "threshold": 0.0,
                    "location_cap": location,
                    "room_floor": 0.0,
                    "edge_floor": edge,
                }
                name = f"factor_location_{asset}_l{location:g}_e{edge:g}"
                specs.append(StrategySpec(name, "factor_location", params, location_targets))
        for location in (0.75, 1.0, 1.5, 2.0):
            for edge in (0.0, 2.0, 2.5):
                for hold in (3, 6, 12, 18, 24):
                    params = {
                        "asset": asset,
                        "location_cap": location,
                        "edge_floor": edge,
                        "hold_bars": hold,
                        "factor_confirm": False,
                        "threshold": 0.0,
                    }
                    name = f"fade_{asset}_l{location:g}_e{edge:g}_h{hold}"
                    specs.append(StrategySpec(name, "fade", params, fade_targets))
        for location in (1.0, 1.5, 2.0):
            for hold in (3, 6, 12, 18, 24):
                for threshold in (0.0, 0.0025):
                    params = {
                        "asset": asset,
                        "location_cap": location,
                        "edge_floor": 0.0,
                        "hold_bars": hold,
                        "factor_confirm": True,
                        "threshold": threshold,
                    }
                    name = f"fade_confirm_{asset}_l{location:g}_h{hold}_th{threshold:g}"
                    specs.append(StrategySpec(name, "fade_confirm", params, fade_targets))
        for buffer in (0.0, 0.25, 0.5, 1.0):
            for hold in (3, 6, 12, 18, 24):
                params = {
                    "asset": asset,
                    "buffer_atr": buffer,
                    "hold_bars": hold,
                    "factor_confirm": False,
                    "threshold": 0.0,
                }
                name = f"breakout_{asset}_b{buffer:g}_h{hold}"
                specs.append(StrategySpec(name, "breakout", params, breakout_targets))
        for buffer in (0.0, 0.5):
            for hold in (3, 6, 12, 18, 24):
                for threshold in (0.0, 0.0025):
                    params = {
                        "asset": asset,
                        "buffer_atr": buffer,
                        "hold_bars": hold,
                        "factor_confirm": True,
                        "threshold": threshold,
                    }
                    name = f"breakout_confirm_{asset}_b{buffer:g}_h{hold}_th{threshold:g}"
                    specs.append(StrategySpec(name, "breakout_confirm", params, breakout_targets))
    return specs


def _score(report: dict[str, Any], family: str, period: str, stress: bool = False) -> list[dict[str, Any]]:
    rows = []
    key = "stress" if stress else "normal"
    for name, value in report["strategies"].items():
        if value["family"] != family:
            continue
        asset = value["parameters"]["asset"]
        asset_return = value["results"][period][key]["net_return"]
        rows.append(
            {
                "name": name,
                "asset": asset,
                "asset_return": asset_return,
                "parameters": value["parameters"],
            }
        )
    return sorted(rows, key=lambda row: (row["asset_return"], row["name"]), reverse=True)


def run(target_root: Path, data_dir: Path, database: Path) -> dict[str, Any]:
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise RuntimeError("set PYTHONHASHSEED=0 for reproducible target detector output")
    aggregate_bars, evaluate_targets, funding_by_bar, load_market, *_ = _modules(target_root)
    loaded = {asset: load_market(database, asset) for asset in ASSETS}
    bars = {asset: aggregate_bars(loaded[asset][0], 240) for asset in ASSETS}
    if len(bars["btc_perp"]) != len(bars["eth_perp"]):
        raise ValueError("BTC and ETH H4 bars are not aligned")
    funding = {asset: funding_by_bar(bars[asset], loaded[asset][1]) for asset in ASSETS}
    states = {asset: _build_states(target_root, data_dir, asset, bars[asset]) for asset in ASSETS}
    factor_scores = _factor_scores(bars["btc_perp"])
    factor = {asset: factor_scores for asset in ASSETS}
    specs = _specs(states, factor, bars)
    periods = _periods(bars["btc_perp"])
    strategies: dict[str, Any] = {}
    for index, spec in enumerate(specs, start=1):
        targets = spec.build_targets(spec.parameters)
        asset = spec.parameters["asset"]
        strategies[spec.name] = {
            "family": spec.family,
            "parameters": spec.parameters,
            "results": {
                period: {
                    "normal": _evaluate(
                        bars[asset],
                        funding[asset],
                        targets,
                        bounds,
                        evaluate_targets,
                        NORMAL_FEE_BPS,
                        NORMAL_SLIPPAGE_BPS,
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
            },
            "nonzero_targets": sum(value not in (None, 0) for value in targets),
        }
        if index % 25 == 0:
            print(f"evaluated {index}/{len(specs)}", file=sys.stderr)
    report: dict[str, Any] = {
        "experiment": {
            "id": "btc-eth-support-resistance-strategy-scan-v1",
            "target_project": str(target_root),
            "data_dir": str(data_dir),
            "database": str(database),
            "signal_timing": "closed H4 bar",
            "fill_timing": "next H4 bar open",
            "funding_included": True,
            "normal_cost_bps_round_trip": 14.0,
            "stress_cost_bps_round_trip": 30.0,
            "warmup_bars": WARMUP_BARS,
            "level_width_cap_atr": LEVEL_WIDTH_CAP_ATR,
            "level_distance_atr": [MIN_LEVEL_DISTANCE_ATR, MAX_LEVEL_DISTANCE_ATR],
            "python_hash_seed": 0,
            "selection_rule": "rank on validation 2024-2025; confirmation 2026 is not used for selection",
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
        "grid": {
            "families": sorted({spec.family for spec in specs}),
            "strategy_count": len(specs),
            "assets": list(ASSETS),
        },
        "strategies": strategies,
    }
    report["rankings"] = {
        f"{family}_{period}": _score(report, family, period)[:10]
        for family in sorted({spec.family for spec in specs})
        for period in ("development_2022_2023", "validation_2024_2025", "confirmation_2026")
    }
    report["rankings"]["all_validation_normal"] = sorted(
        (
            {
                "name": name,
                "family": value["family"],
                "asset": value["parameters"]["asset"],
                "parameters": value["parameters"],
                "asset_return": value["results"]["validation_2024_2025"]["normal"][
                    "net_return"
                ],
            }
            for name, value in strategies.items()
        ),
        key=lambda row: (row["asset_return"], row["name"]),
        reverse=True,
    )[:30]
    report["decision"] = {
        "status": "research_diagnostic",
        "approved_for_trading": False,
        "promotion_rule": (
            "A candidate must beat the family baseline on both validation assets and both normal/stress "
            "costs, then retain positive improvement in the separate confirmation period."
        ),
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BTC/ETH Support/Resistance Strategy Scan",
        "",
        "Research-only causal H4 scan; no paper/live execution integration.",
        "",
        f"- Data: `{json.dumps(report['data'], sort_keys=True)}`",
        f"- Strategies: `{report['grid']['strategy_count']}` across `{', '.join(report['grid']['families'])}`.",
        "- Selection uses 2024–2025 validation; 2026 confirmation is reported separately.",
        "- Funding included; normal costs are 14 bps round trip and stress costs are 30 bps.",
        "",
        "## Validation Leaders",
        "",
        "| Name | Asset | Family | Validation return | Parameters |",
        "|---|---|---|---:|---|",
    ]
    for row in report["rankings"]["all_validation_normal"][:20]:
        lines.append(
            f"| {row['name']} | {row['asset']} | {row['family']} | "
            f"{row['asset_return']:.2%} | "
            f"`{json.dumps(row['parameters'], sort_keys=True)}` |"
        )
    lines.extend(["", "## Confirmation of Validation Leaders", ""])
    lines.extend(["| Name | Asset | 2026 return | 2026 stress |", "|---|---|---:|---:|"])
    for row in report["rankings"]["all_validation_normal"][:20]:
        value = report["strategies"][row["name"]]
        asset = row["asset"]
        lines.append(
            f"| {row['name']} | {asset} | "
            f"{value['results']['confirmation_2026']['normal']['net_return']:.2%} | "
            f"{value['results']['confirmation_2026']['stress']['net_return']:.2%} |"
        )
    lines.extend(
        [
            "",
            "The confirmation table above is intentionally limited to candidates ranked on validation. "
            "No result grants trading approval; the full per-asset results and stress metrics are in JSON.",
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
    json_path = args.output_dir / "btc-eth-support-resistance-strategy-scan-v1.json"
    markdown_path = args.output_dir / "btc-eth-support-resistance-strategy-scan-v1.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
