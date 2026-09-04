#!/usr/bin/env python3
"""Check the stitched strict-15m SMA10 candidate's parameter neighborhood."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base

OUTPUT = Path("reports/experiments/btc_stitched_strict15m_sma_grid/2026-09-03")
FAST_PERIODS = (8, 9, 10, 11, 12)
ENTER_DAYS = (1, 2, 3)
ACTIVE_EXPOSURES = (Decimal("1.25"), Decimal("1.5"))
COSTS = (
    ("default", Decimal("10"), Decimal("5")),
    ("moderate", Decimal("20"), Decimal("10")),
)


def main() -> None:
    spot, futures, daily, target_indices, funding = base.load_hybrid_inputs()
    bars = spot + futures
    bounds = base.periods(bars[-1].end_ms, spot[-1].end_ms)
    benchmarks = {name: base.benchmark(bars, *period) for name, period in bounds.items()}
    rows = []
    for fast in FAST_PERIODS:
        for enter in ENTER_DAYS:
            for active in ACTIVE_EXPOSURES:
                sparse = base.build_targets(
                    daily,
                    fast_period=fast,
                    enter_bear_days=enter,
                    active=active,
                )
                targets = base.map_targets(len(bars), target_indices, sparse)
                metrics = {}
                for name in ("research", "validation", "oos", "full"):
                    start, end = bounds[name]
                    metrics[name] = {}
                    for label, fee_bps, slippage_bps in COSTS:
                        result = base.replay(
                            bars,
                            targets,
                            funding,
                            start,
                            end,
                            fee_bps=fee_bps,
                            slippage_bps=slippage_bps,
                        )
                        metrics[name][label] = base.public(result, benchmarks[name], start, end)
                development = [
                    metrics[name][cost]["excess"]
                    for name in ("research", "validation")
                    for cost, _fee, _slippage in COSTS
                ]
                rows.append(
                    {
                        "id": f"sma{fast}/40-enter{enter}-exit1-active{active}x",
                        "fast": fast,
                        "enter": enter,
                        "active": str(active),
                        "metrics": metrics,
                        "development_worst_excess": min(development),
                    }
                )
    rows.sort(
        key=lambda row: (
            row["development_worst_excess"],
            row["metrics"]["validation"]["default"]["excess"],
        ),
        reverse=True,
    )
    passing = [
        row
        for row in rows
        if row["development_worst_excess"] > 0
        and row["metrics"]["oos"]["default"]["excess"] > 0
        and row["metrics"]["full"]["default"]["maximum_intrabar_leverage"] <= 3
    ]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "execution": "spot daily before 2020; perpetual 15m from 2020",
            "selection": "worst Research/Validation excess over default and moderate costs",
            "oos": "2025-latest not used in selection",
            "grid": {
                "fast_sma": FAST_PERIODS,
                "enter_bear_days": ENTER_DAYS,
                "active_exposure": [str(value) for value in ACTIVE_EXPOSURES],
            },
        },
        "data": {
            "spot_daily_bars": len(spot),
            "perpetual_15m_bars": len(futures),
            "last": base.iso(bars[-1].end_ms),
        },
        "candidate_count": len(rows),
        "passing_count": len(passing),
        "results": rows,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload, passing), encoding="utf-8")
    print(OUTPUT / "README.md")


def render(payload, passing):
    lines = [
        "# BTC Stitched Strict-15m SMA Neighborhood",
        "",
        (
            "按 Research/Validation 的默认与 20+10 bps 中度成本最差超额排序；"
            "2025 至最新 OOS 不参与选择。"
        ),
        "",
        "| 配置 | 开发最差超额 | Validation 默认 | OOS 默认 | Full CAGR | Full DD | 峰值杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        metrics = row["metrics"]
        full = metrics["full"]["default"]
        lines.append(
            f"| `{row['id']}` | {row['development_worst_excess']:.2%} | "
            f"{metrics['validation']['default']['excess']:.2%} | "
            f"{metrics['oos']['default']['excess']:.2%} | {full['strategy_cagr']:.2%} | "
            f"{full['strategy_drawdown']:.2%} | {full['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        (
            f"{payload['passing_count']}/{payload['candidate_count']} 个配置同时通过"
            "开发期两档成本、"
            "默认成本 OOS 和 3X 有效杠杆门槛。"
        ),
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
