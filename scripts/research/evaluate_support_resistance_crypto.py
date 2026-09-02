#!/usr/bin/env python3
"""Evaluate the support/resistance project's V3 detector on BTC/ETH.

This is an offline research adapter. It runs with the target project's virtual
environment because the target detector depends on pandas/numpy/scipy. The
detector is evaluated causally at closed 4h bars; outcomes only inspect future
bars. No orders or paper/live settings are changed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_TARGET = Path("/home/ldtdev/qt/Detect_support_and_resistance_levels")
DEFAULT_DATA = DEFAULT_TARGET / "data" / "mmtick_crypto"
DEFAULT_OUTPUT = Path("reports/experiments/support_resistance_crypto/2026-08-26")
ASSETS = ("BTCUSDT", "ETHUSDT")
TIMEFRAME = "H4"
DETECTORS = ("V3_fusion", "placebo_fixed", "V3_shift")
ROUND_TRIP_COST_PCT = 0.14  # 2 * (5 bps fee + 2 bps slippage)


def _native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if hasattr(value, "item"):
        return _native(value.item())
    if value is None or isinstance(value, (str, bool)):
        return value
    missing = pd.isna(value)
    if isinstance(missing, bool) and missing:
        return None
    return value


def _summary(frame: pd.DataFrame, width_cap: float = 1.5) -> dict[str, Any]:
    selected = frame[frame["width_atr"] <= width_cap].copy()
    touched = selected[selected["touched"]]
    decided = touched[touched["result"].isin(("hold", "break"))]
    finite = touched[pd.notna(touched["fwd_ret_pct"])].copy()
    net = finite["fwd_ret_pct"] - ROUND_TRIP_COST_PCT
    return _native(
        {
            "levels": len(selected),
            "touched": len(touched),
            "decided": len(decided),
            "touch_rate": len(touched) / len(selected) if len(selected) else None,
            "hold_rate": (
                float((decided["result"] == "hold").mean()) if len(decided) else None
            ),
            "undecided_rate": (
                float((touched["result"] == "undecided").mean()) if len(touched) else None
            ),
            "gross_fwd_ret_pct_mean": (
                float(finite["fwd_ret_pct"].mean()) if len(finite) else None
            ),
            "net_fwd_ret_pct_mean_after_14bps": float(net.mean()) if len(net) else None,
            "net_fwd_ret_pct_median_after_14bps": float(net.median()) if len(net) else None,
            "net_positive_rate_after_14bps": float((net > 0).mean()) if len(net) else None,
            "edge_atr_mean": float(finite["edge_atr"].mean()) if len(finite) else None,
            "avg_distance_atr": float(selected["dist_atr"].abs().mean())
            if len(selected)
            else None,
            "avg_width_atr": float(selected["width_atr"].mean()) if len(selected) else None,
        }
    )


def _load_modules(target_root: Path):
    sys.path.insert(0, str(target_root))
    from src.local_data_loader import load_kline  # type: ignore[import-not-found]
    from src.srlab.data import to_symbol  # type: ignore[import-not-found]
    from src.srlab.detectors import (  # type: ignore[import-not-found]
        FixedPlacebo,
        ShiftPlacebo,
        V3Fusion,
    )
    from src.srlab.walkforward import run_walkforward  # type: ignore[import-not-found]

    return load_kline, to_symbol, FixedPlacebo, ShiftPlacebo, V3Fusion, run_walkforward


def run(target_root: Path, data_dir: Path) -> dict[str, Any]:
    load_kline, to_symbol, FixedPlacebo, ShiftPlacebo, V3Fusion, run_walkforward = _load_modules(
        target_root
    )
    symbols = [
        to_symbol(asset, load_kline(str(data_dir), asset, TIMEFRAME)) for asset in ASSETS
    ]
    detectors = [
        V3Fusion(),
        FixedPlacebo(),
        ShiftPlacebo(V3Fusion(), name="V3_shift"),
    ]
    result = run_walkforward(
        symbols,
        detectors,
        warmup=800,
        rebalance_every=6,
        horizon=18,
        hold_bars=6,
        break_atr=0.5,
        target_atr=1.0,
        min_dist_atr=0.5,
        max_dist_atr=5.0,
        max_out_per_side=3,
        geo_top_k=3,
        verbose=False,
    )
    levels = result["levels"].copy()
    geo = result["geo"].copy()
    levels["date"] = pd.to_datetime(levels["date"], utc=True)
    overall = {
        detector: _summary(levels[levels["detector"] == detector]) for detector in DETECTORS
    }
    by_asset_year: dict[str, dict[str, dict[str, Any]]] = {}
    levels["year"] = levels["date"].dt.year
    for asset in ASSETS:
        by_asset_year[asset] = {}
        for year in sorted(levels.loc[levels["code"] == asset, "year"].unique()):
            subset = levels[(levels["code"] == asset) & (levels["year"] == year)]
            by_asset_year[asset][str(year)] = {
                detector: _summary(subset[subset["detector"] == detector])
                for detector in DETECTORS
            }
    geo_summary = {}
    if not geo.empty:
        geo_summary = _native(
            geo.groupby("detector")[
                ["recall_hit_05", "recall_hit_10", "median_err_atr", "precision_hit_05"]
            ]
            .mean()
            .to_dict(orient="index")
        )
    from src.srlab.metrics import (  # type: ignore[import-not-found]
        compare,
        compare_distance_neutral,
    )

    comparisons = [
        compare(levels, "placebo_fixed", "V3_fusion", width_cap=1.5),
        compare(levels, "V3_shift", "V3_fusion", width_cap=1.5),
    ]
    distance_neutral = compare_distance_neutral(
        levels, "V3_shift", "V3_fusion", width_cap=1.5
    )
    # The helper includes a private per-stratum DataFrame for interactive use;
    # reports should remain JSON serializable and stable.
    distance_neutral.pop("_strata", None)
    return _native(
        {
            "experiment": {
                "id": "btc-eth-support-resistance-v3-walkforward-v1",
                "target_project": str(target_root),
                "data_dir": str(data_dir),
                "assets": list(ASSETS),
                "timeframe": TIMEFRAME,
                "signal_timing": "closed H4 bar",
                "evaluation_timing": "future H4 bars only",
                "warmup_bars": 800,
                "rebalance_every_bars": 6,
                "horizon_bars": 18,
                "hold_bars": 6,
                "break_atr": 0.5,
                "target_atr": 1.0,
                "width_cap_atr": 1.5,
                "round_trip_cost_bps": 14.0,
                "funding_included": False,
                "liquidation_modeled": False,
                "detectors": list(DETECTORS),
            },
            "data": {
                asset: {
                    "bars": len(symbol),
                    "first": str(symbol.date[0]),
                    "last": str(symbol.date[-1]),
                }
                for asset, symbol in zip(ASSETS, symbols, strict=True)
            },
            "overall": overall,
            "by_asset_year": by_asset_year,
            "comparisons": comparisons,
            "distance_neutral_comparison": distance_neutral,
            "geometry": geo_summary,
            "counts": {"level_rows": len(levels), "geometry_rows": len(geo)},
            "decision": {
                "status": "research_diagnostic",
                "approved_for_trading": False,
                "conclusion": (
                    "V3 support/resistance levels did not show statistically significant "
                    "incremental hold-rate improvement over a distance-preserving placebo; "
                    "net forward returns remain negative before funding."
                ),
            },
        }
    )


def markdown(report: dict[str, Any]) -> str:
    exp = report["experiment"]
    lines = [
        "# BTC/ETH Support/Resistance V3 Walk-Forward",
        "",
        "Research-only diagnostic; no execution integration or trading approval.",
        "",
        f"- Data: `{json.dumps(report['data'], ensure_ascii=False, sort_keys=True)}`",
        f"- Signal: closed `{exp['timeframe']}` bar; evaluation reads future bars only.",
        f"- Costs: `{exp['round_trip_cost_bps']:.0f}` bps round trip; funding not included.",
        f"- Decision points: every `{exp['rebalance_every_bars']}` bars after `{exp['warmup_bars']}` warmup.",
        "",
        "## Overall (width <= 1.5 ATR)",
        "",
        "| Detector | Levels | Touch | Decided | Hold | Net forward return | Positive net rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for detector, row in report["overall"].items():
        lines.append(
            f"| {detector} | {row['levels']:,} | {row['touch_rate']:.1%} | "
            f"{row['decided']:,} | {row['hold_rate']:.1%} | "
            f"{row['net_fwd_ret_pct_mean_after_14bps']:.3f}% | "
            f"{row['net_positive_rate_after_14bps']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## Placebo Comparisons",
            "",
            "```json",
            json.dumps(report["comparisons"], indent=2),
            "```",
            "",
            "## Distance-Neutral Comparison",
            "",
            "The V3-vs-shift comparison controls for distance with six quantile strata; CMH p-values are diagnostic and do not account for overlapping events.",
            "",
            "```json",
            json.dumps(report["distance_neutral_comparison"], indent=2),
            "```",
            "",
            "## Geometry",
            "",
            "```json",
            json.dumps(report["geometry"], indent=2),
            "```",
            "",
        ]
    )
    lines.extend(
        [
            "## Limitations",
            "",
            "- The target project's bundled probability models are not used.",
            "- This is signal-quality evaluation, not a capital-aware portfolio replay.",
            "- Funding, liquidation, market impact, and overlapping-level position netting are not modeled.",
            "- The 14 bps adjustment covers two entry/exit fills only.",
            "",
            report["decision"]["conclusion"],
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-project", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if os.environ.get("PYTHONHASHSEED") != "0":
        parser.error(
            "set PYTHONHASHSEED=0 for reproducible ShiftPlacebo results "
            "(example: PYTHONHASHSEED=0 <target>/.venv/bin/python ...)."
        )
    report = run(args.target_project, args.data_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "btc-eth-support-resistance-v3-walkforward-v1.json"
    md_path = args.output_dir / "btc-eth-support-resistance-v3-walkforward-v1.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    print(json_path)
    print(md_path)


if __name__ == "__main__":
    main()
