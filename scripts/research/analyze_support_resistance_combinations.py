#!/usr/bin/env python3
"""Research combinations and causal enhancements for the support/resistance scan.

This file intentionally stays outside execution code.  It replays closed H4 signals
at the next H4 open, with realized funding, and tests only information available
before the signal bar.  The target project remains an external GPL-3.0 dependency.
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TARGET = Path("/home/ldtdev/qt/Detect_support_and_resistance_levels")
DATA = TARGET / "data" / "mmtick_crypto"
DATABASE = ROOT / "data" / "paper.db"
OUT = ROOT / "reports" / "experiments" / "support_resistance_crypto" / "scan"

NORMAL = (Decimal("5"), Decimal("2"))
STRESS = (Decimal("10"), Decimal("5"))
PERIOD_NAMES = (
    "predevelopment_2020_2021",
    "development_2022_2023",
    "validation_2024_2025",
    "confirmation_2026",
)


def _periods(bars: list[Any], forward_start_ms: int | None = None) -> dict[str, tuple[int, int]]:
    def stamp(value: str) -> int:
        return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)

    periods = {
        "predevelopment_2020_2021": (stamp("2020-01-01"), stamp("2021-12-31T23:59:59")),
        "development_2022_2023": (stamp("2022-01-01"), stamp("2023-12-31T23:59:59")),
        "validation_2024_2025": (stamp("2024-01-01"), stamp("2025-12-31T23:59:59")),
        "confirmation_2026": (stamp("2026-01-01"), bars[-1].start_ms),
    }
    if forward_start_ms is not None:
        periods["forward_after_cutoff"] = (forward_start_ms, bars[-1].start_ms)
    return periods


def _evaluate(evaluate_fn: Any, bars: list[Any], funding: list[list[Any]], targets: tuple[int | None, ...],
              bounds: tuple[int, int], costs: tuple[Decimal, Decimal], include_daily: bool = False) -> dict[str, Any]:
    start_ms, end_ms = bounds
    start = next(i for i, bar in enumerate(bars) if bar.start_ms >= start_ms)
    isolated = list(targets)
    isolated[:start] = [None] * start
    result = evaluate_fn(
        bars,
        tuple(isolated),
        start_ms=start_ms,
        end_ms=end_ms,
        funding=funding,
        fee_bps=costs[0],
        slippage_bps=costs[1],
        close_final_position=True,
    )
    result_payload = {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
    }
    if include_daily:
        result_payload["daily_returns"] = result.daily_returns
    return result_payload


def _own_factor(bars: list[Any], lookback: int = 6) -> list[Decimal | None]:
    values: list[Decimal | None] = [None] * len(bars)
    for i in range(lookback, len(bars)):
        values[i] = -(bars[i].close / bars[i - lookback].close - Decimal("1"))
    return values


def _map_closed_daily_states(h4: list[Any], d1: list[Any], states: list[Any | None]) -> list[Any | None]:
    """Map the last *completed* D1 state to each H4 bar.

    A D1 state is never applied to H4 bars from that same UTC day, because its
    daily close is not known yet.  This is the important leakage guard for MTF tests.
    """
    starts = [bar.start_ms for bar in d1]
    mapped: list[Any | None] = []
    for bar in h4:
        day_index = bisect.bisect_left(starts, bar.start_ms)
        mapped.append(states[day_index - 1] if day_index > 0 else None)
    return mapped


def _build_states_tf(target_root: Path, data_dir: Path, asset: str, bars: list[Any], timeframe: str,
                     modules: tuple[Any, ...]) -> list[Any | None]:
    """Run V3 against a causal H4 or D1 sequence while checking target OHLC exactly."""
    import explore_support_resistance_strategies as sr

    _aggregate, _evaluate, _funding, _load_market, load_kline, Ctx, to_symbol, V3Fusion, zigzag = modules
    code = {"btc_perp": "BTCUSDT", "eth_perp": "ETHUSDT"}[asset]
    symbol = to_symbol(code, load_kline(str(data_dir), code, timeframe))
    if len(symbol) != len(bars):
        raise ValueError(f"{asset} {timeframe}: target count differs from MMTICK count")
    for index, bar in enumerate(bars):
        source = (symbol.open_[index], symbol.high[index], symbol.low[index], symbol.close[index])
        target = (float(bar.open), float(bar.high), float(bar.low), float(bar.close))
        if source != target:
            raise ValueError(f"{asset} {timeframe}: OHLC mismatch at index {index}")
    pivots = zigzag(symbol.high, symbol.low, symbol.atr, k_atr=2.0)
    detector = V3Fusion()
    states: list[Any | None] = [None] * len(bars)
    for index in range(sr.WARMUP_BARS, len(bars)):
        atr = float(symbol.atr[index])
        if np.isfinite(atr) and atr > 0:
            context = Ctx(code=code, t=index, open_=symbol.open_[: index + 1], high=symbol.high[: index + 1],
                          low=symbol.low[: index + 1], close=symbol.close[: index + 1], volume=symbol.volume[: index + 1],
                          atr=atr, tick=symbol.tick, atr_arr=symbol.atr[: index + 1],
                          pivots=pivots.view_at(index, max_lookback=600))
            states[index] = sr._state(detector.detect(context), atr)
    return states


def _same_side_confluence(entries: list[int], h4_states: list[Any | None], d1_states: list[Any | None],
                          d1_cap: float, edge_floor: float = 0.0) -> list[int]:
    result: list[int] = []
    for entry, h4, d1 in zip(entries, h4_states, d1_states, strict=True):
        if not entry or h4 is None or d1 is None:
            result.append(0)
            continue
        if entry > 0:
            near = d1.support_distance_atr
            edge = d1.support_edge
        else:
            near = d1.resistance_distance_atr
            edge = d1.resistance_edge
        result.append(entry if near is not None and near <= d1_cap and edge is not None and edge >= edge_floor else 0)
    return result


def _causal_volatility_filter(bars: list[Any], states: list[Any | None], entries: list[int],
                              multiplier: float, mode: str) -> list[int]:
    """Filter entries using ATR/close against a trailing median (including prior bars only)."""
    result: list[int] = []
    ratios: list[float | None] = []
    for bar, state in zip(bars, states, strict=True):
        ratios.append(state.atr / float(bar.close) if state is not None and state.atr else None)
    for i, entry in enumerate(entries):
        if not entry or ratios[i] is None:
            result.append(0)
            continue
        history = [value for value in ratios[max(0, i - 96):i] if value is not None]
        if len(history) < 24:
            result.append(0)
            continue
        history.sort()
        median = history[len(history) // 2]
        accepted = ratios[i] <= median * multiplier if mode == "low" else ratios[i] >= median * multiplier
        result.append(entry if accepted else 0)
    return result


def _combine_daily_returns(left: tuple[tuple[str, float], ...], right: tuple[tuple[str, float], ...]) -> dict[str, Any]:
    """Combine two independently replayed 50/50 legs and report a simple equity curve."""
    a = dict(left)
    b = dict(right)
    labels = sorted(set(a) | set(b))
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for label in labels:
        ra = a.get(label, 0.0)
        rb = b.get(label, 0.0)
        equity *= 1.0 + 0.5 * ra + 0.5 * rb
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)
    return {"net_return": equity - 1.0, "max_drawdown": max_dd, "days": len(labels)}


def run(target_root: Path, data_dir: Path, database: Path, forward_start_ms: int | None = None) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "scripts" / "research"))
    import explore_support_resistance_strategies as sr

    aggregate, evaluate, funding_by_bar, load_market, load_kline, Ctx, to_symbol, V3Fusion, zigzag = sr._modules(target_root)
    loaded = {asset: load_market(database, asset) for asset in sr.ASSETS}
    h4 = {asset: aggregate(loaded[asset][0], 240) for asset in sr.ASSETS}
    d1 = {asset: aggregate(loaded[asset][0], 1440) for asset in sr.ASSETS}
    funding = {asset: funding_by_bar(h4[asset], loaded[asset][1]) for asset in sr.ASSETS}
    h4_states = {asset: sr._build_states(target_root, data_dir, asset, h4[asset]) for asset in sr.ASSETS}
    d1_states = {asset: _build_states_tf(target_root, data_dir, asset, d1[asset], "D1", (aggregate, evaluate, funding_by_bar, load_market, load_kline, Ctx, to_symbol, V3Fusion, zigzag)) for asset in sr.ASSETS}
    closed_d1 = {asset: _map_closed_daily_states(h4[asset], d1[asset], d1_states[asset]) for asset in sr.ASSETS}
    periods = _periods(h4["btc_perp"], forward_start_ms)

    candidates: dict[str, tuple[str, list[int | None], str]] = {}
    # Rebuild the existing leaders using each asset's own momentum as a separate test.
    own_factor = {asset: _own_factor(h4[asset]) for asset in sr.ASSETS}
    for asset in sr.ASSETS:
        for hold in (6, 12, 18, 24):
            for location in (1.0, 1.5, 2.0):
                params = {"asset": asset, "location_cap": location, "edge_floor": 0.0,
                          "hold_bars": hold, "factor_confirm": True, "threshold": 0.0}
                entries = sr._fade_entries(h4_states[asset], location, 0.0)
                entries = sr._combine_entries(entries, sr._direction_targets(own_factor[asset], 0.0))
                targets = sr._fixed_hold_targets(entries, hold)
                candidates[f"fade_own_{asset}_l{location:g}_h{hold}"] = ("fade_own", targets, asset)
        for hold in (6, 12, 18, 24):
            entries = sr._breakout_entries(h4[asset], h4_states[asset], 0.0)
            entries = sr._combine_entries(entries, sr._direction_targets(own_factor[asset], 0.0))
            targets = sr._fixed_hold_targets(entries, hold)
            candidates[f"breakout_own_{asset}_h{hold}"] = ("breakout_own", targets, asset)

    # Multi-timeframe confluence and volatility variants of the existing level entries.
    for asset in sr.ASSETS:
        for hold in (6, 12, 18, 24):
            base = sr._fade_entries(h4_states[asset], 1.5, 0.0)
            for cap in (1.5, 2.5, 4.0):
                entries = _same_side_confluence(base, h4_states[asset], closed_d1[asset], cap)
                candidates[f"fade_mtf_{asset}_d{cap:g}_h{hold}"] = ("fade_mtf", sr._fixed_hold_targets(entries, hold), asset)
            for multiplier in (0.75, 1.0, 1.25):
                entries = _causal_volatility_filter(h4[asset], h4_states[asset], base, multiplier, "low")
                candidates[f"fade_lowvol_{asset}_m{multiplier:g}_h{hold}"] = ("fade_lowvol", sr._fixed_hold_targets(entries, hold), asset)
        for hold in (6, 12, 18, 24):
            base = sr._breakout_entries(h4[asset], h4_states[asset], 0.0)
            for cap in (2.5, 4.0, 6.0):
                entries = _same_side_confluence(base, h4_states[asset], closed_d1[asset], cap)
                candidates[f"breakout_mtf_{asset}_d{cap:g}_h{hold}"] = ("breakout_mtf", sr._fixed_hold_targets(entries, hold), asset)
            for multiplier in (0.75, 1.0, 1.25):
                entries = _causal_volatility_filter(h4[asset], h4_states[asset], base, multiplier, "high")
                candidates[f"breakout_highvol_{asset}_m{multiplier:g}_h{hold}"] = ("breakout_highvol", sr._fixed_hold_targets(entries, hold), asset)

    rows: dict[str, Any] = {}
    for name, (family, targets, asset) in candidates.items():
        rows[name] = {"family": family, "asset": asset, "nonzero_targets": sum(bool(v) for v in targets), "results": {}}
        for period, bounds in periods.items():
            rows[name]["results"][period] = {
                "normal": _evaluate(evaluate, h4[asset], funding[asset], tuple(targets), bounds, NORMAL),
                "stress": _evaluate(evaluate, h4[asset], funding[asset], tuple(targets), bounds, STRESS),
            }

    # Select purely on validation normal return for diagnostics, then show confirmation separately.
    leaders = sorted(rows, key=lambda name: rows[name]["results"]["validation_2024_2025"]["normal"]["net_return"], reverse=True)[:20]
    pair_names = [
        ("fade_btc_perp_l1_e0_h12", "breakout_eth_perp_b0_h12"),
        ("fade_btc_perp_l2_e2.5_h12", "breakout_eth_perp_b0_h18"),
        ("fade_own_btc_perp_l1_h12", "breakout_own_eth_perp_h12"),
    ]
    # Existing candidates are replayed from the original scanner specs; new candidates are above.
    original = sr._specs(h4_states, {asset: sr._factor_scores(h4["btc_perp"]) for asset in sr.ASSETS}, h4)
    original_by_name = {spec.name: spec for spec in original}
    target_cache: dict[str, tuple[int | None, ...]] = {name: tuple(spec.build_targets(spec.parameters)) for name, spec in original_by_name.items()}
    target_cache.update({name: tuple(item[1]) for name, item in candidates.items()})
    portfolios: dict[str, Any] = {}
    for left, right in pair_names:
        if left not in target_cache or right not in target_cache:
            continue
        assets = (original_by_name[left].parameters["asset"] if left in original_by_name else rows[left]["asset"],
                  original_by_name[right].parameters["asset"] if right in original_by_name else rows[right]["asset"])
        portfolios[f"50_50_{left}__{right}"] = {"legs": [left, right], "assets": list(assets), "results": {}}
        for period, bounds in periods.items():
            period_result: dict[str, Any] = {}
            for cost_name, costs in (("normal", NORMAL), ("stress", STRESS)):
                leg_results = []
                for leg, asset in zip((left, right), assets, strict=True):
                    replay = _evaluate(evaluate, h4[asset], funding[asset], target_cache[leg], bounds, costs, include_daily=True)
                    leg_results.append(replay)
                combined = _combine_daily_returns(tuple(leg_results[0]["daily_returns"]), tuple(leg_results[1]["daily_returns"]))
                period_result[cost_name] = combined
                period_result[f"{cost_name}_legs"] = [{k: v for k, v in item.items() if k != "daily_returns"} for item in leg_results]
            portfolios[f"50_50_{left}__{right}"]["results"][period] = period_result

    return {
        "experiment": {
            "id": "btc-eth-support-resistance-combinations-v1",
            "signal_timing": "closed H4 bar; D1 variants use the last completed UTC D1 state",
            "fill_timing": "next H4 bar open",
            "funding_included": True,
            "normal_cost_bps_round_trip": 14.0,
            "stress_cost_bps_round_trip": 30.0,
            "selection_rule": "leaders are ranked on 2024-2025 validation normal cost; 2026 is confirmation only",
            "forward_start_ms": forward_start_ms,
        },
        "data": {asset: {"h4_bars": len(h4[asset]), "d1_bars": len(d1[asset]),
                           "first": datetime.fromtimestamp(h4[asset][0].start_ms / 1000, UTC).isoformat(),
                           "last": datetime.fromtimestamp(h4[asset][-1].start_ms / 1000, UTC).isoformat()} for asset in sr.ASSETS},
        "grid": {"new_strategy_count": len(rows), "families": sorted({row["family"] for row in rows.values()})},
        "strategies": rows,
        "validation_leaders": [{"name": name, "family": rows[name]["family"], "asset": rows[name]["asset"],
                                "validation": rows[name]["results"]["validation_2024_2025"],
                                "confirmation": rows[name]["results"]["confirmation_2026"],
                                "forward": rows[name]["results"].get("forward_after_cutoff")} for name in leaders],
        "portfolios": portfolios,
        "decision": {"status": "research_diagnostic", "approved_for_trading": False,
                     "reason": "New configurations require fresh forward data and independent implementation review."},
    }


def render(report: dict[str, Any]) -> str:
    lines = ["# BTC/ETH Support/Resistance Combinations", "", "Research-only diagnostic; no live or paper execution integration.", "",
             f"- New configurations: `{report['grid']['new_strategy_count']}` across `{', '.join(report['grid']['families'])}`.",
             "- D1 states are lagged to the last completed UTC day; costs include funding, 14 bps normal and 30 bps stress.",
             "- Ranking uses 2024–2025 validation only; 2026 is confirmation, not a selection holdout.", "", "## Validation Leaders", "",
             "| Name | Family | Asset | Validation | Trades | PF | DD | 2026 | 2026 stress |", "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in report["validation_leaders"]:
        v = row["validation"]["normal"]
        c = row["confirmation"]["normal"]
        cs = row["confirmation"]["stress"]
        lines.append(f"| {row['name']} | {row['family']} | {row['asset']} | {v['net_return']:.2%} | {v['completed_trades']} | {v['profit_factor'] or 0:.2f} | {v['max_drawdown']:.2%} | {c['net_return']:.2%} | {cs['net_return']:.2%} |")
    if any(row.get("forward") for row in report["validation_leaders"]):
        lines.extend(["", "## Fresh Forward Observation", "", "| Name | Asset | Forward | Stress | Trades | PF | DD |", "|---|---|---:|---:|---:|---:|---:|"])
        for row in report["validation_leaders"]:
            forward = row.get("forward")
            if not forward:
                continue
            normal = forward["normal"]
            stress = forward["stress"]
            lines.append(f"| {row['name']} | {row['asset']} | {normal['net_return']:.2%} | {stress['net_return']:.2%} | {normal['completed_trades']} | {normal['profit_factor'] or 0:.2f} | {normal['max_drawdown']:.2%} |")
    lines.extend(["", "## 50/50 Portfolios", "", "| Portfolio | Validation | Validation stress | Validation DD | 2026 | 2026 stress | 2026 DD |", "|---|---:|---:|---:|---:|---:|---:|"])
    for name, value in report["portfolios"].items():
        v = value["results"]["validation_2024_2025"]["normal"]
        vs = value["results"]["validation_2024_2025"]["stress"]
        c = value["results"]["confirmation_2026"]["normal"]
        cs = value["results"]["confirmation_2026"]["stress"]
        lines.append(f"| {name} | {v['net_return']:.2%} | {vs['net_return']:.2%} | {v['max_drawdown']:.2%} | {c['net_return']:.2%} | {cs['net_return']:.2%} | {c['max_drawdown']:.2%} |")
    if report["experiment"].get("forward_start_ms") is not None:
        lines.extend(["", "## Fresh Forward Portfolios", "", "| Portfolio | Forward | Forward stress | DD |", "|---|---:|---:|---:|"])
        for name, value in report["portfolios"].items():
            forward = value["results"].get("forward_after_cutoff")
            if forward:
                lines.append(f"| {name} | {forward['normal']['net_return']:.2%} | {forward['stress']['net_return']:.2%} | {forward['normal']['max_drawdown']:.2%} |")
    lines.extend(["", "A positive validation result is not sufficient for promotion. Review full JSON for stress results, trade counts, funding, and phase stability; candidates remain research-only.", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-project", type=Path, default=TARGET)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--forward-start-ms", type=int, default=None)
    parser.add_argument("--output-prefix", default="btc-eth-support-resistance-combinations-v1")
    args = parser.parse_args()
    report = run(args.target_project, args.data_dir, args.database, args.forward_start_ms)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"{args.output_prefix}.json"
    md_path = args.output_dir / f"{args.output_prefix}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=list) + "\n", encoding="utf-8")
    md_path.write_text(render(report) + "\n", encoding="utf-8")
    print(json_path)
    print(md_path)


if __name__ == "__main__":
    main()
