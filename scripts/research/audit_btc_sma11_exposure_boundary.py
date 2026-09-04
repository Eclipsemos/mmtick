#!/usr/bin/env python3
"""Map the strict 3x feasibility boundary for fixed SMA11/40 active exposure."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from research_btc_collateral_architecture import replay_segregated

OUTPUT = Path("reports/experiments/btc_sma11_exposure_boundary/2026-09-03")
ACTIVE_EXPOSURES = tuple(
    Decimal(value)
    for value in (
        "1.50",
        "1.51",
        "1.52",
        "1.53",
        "1.54",
        "1.55",
        "1.60",
        "1.65",
        "1.70",
        "1.75",
    )
)
FUTURES_CAP = Decimal("2.5")


def main() -> None:
    spot, futures, daily, target_indices, funding = base.load_hybrid_inputs()
    bars = spot + futures
    bounds = base.periods(bars[-1].end_ms, spot[-1].end_ms)
    benchmarks = {name: base.benchmark(bars, *period) for name, period in bounds.items()}
    rows = []
    for active in ACTIVE_EXPOSURES:
        targets = base.map_targets(
            len(bars),
            target_indices,
            base.build_targets(daily, fast_period=11, enter_bear_days=2, active=active),
        )
        results = {}
        for name, period in bounds.items():
            result = replay(bars, targets, funding, *period)
            results[name] = base.public(result, benchmarks[name], *period)
        rows.append(
            {
                "active_exposure": str(active),
                "results": results,
                "strict_3x_passed": all(
                    row["maximum_intrabar_leverage"] <= 3 and not row["liquidated"]
                    for row in results.values()
                ),
                "development_min_excess": min(
                    results[name]["excess"] for name in ("research", "validation")
                ),
            }
        )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / NOT_A_SELECTION_GRID",
        "protocol": {
            "candidate": "fixed SMA11/40 enter2-exit1, bear 0X",
            "purpose": "exposure-cap feasibility audit; no selection and no OOS-based tuning",
            "execution": "completed UTC daily signal; next 15m open",
            "wallets": "50% spot and 50% isolated USD-M collateral",
            "opening_control": "2.5X futures-wallet leverage",
            "hard_cap": "all segments must keep observed intrabar futures leverage <=3X",
            "costs": "10 bps fee + 5 bps slippage per side; historical Funding",
        },
        "data": {"bars": len(bars), "last": base.iso(bars[-1].end_ms)},
        "results": rows,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def replay(bars, targets, funding, start, end):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=FUTURES_CAP,
    )


def render(payload):
    lines = [
        "# BTC SMA11/40 Active-Exposure Strict-3X Boundary",
        "",
        "固定 SMA11/40 和确认规则，仅映射主动暴露在盘中严格 3X 约束下的可行边界。"
        "这不是参数选择，也不据 OOS 调整暴露。",
        "",
        "| Active | R超额 | V超额 | OOS超额 | Full CAGR | Full DD | 峰值杠杆 | 严格3X |",
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["results"]:
        metrics = row["results"]
        full = metrics["full"]
        lines.append(
            f"| {row['active_exposure']}X | {metrics['research']['excess']:.2%} | "
            f"{metrics['validation']['excess']:.2%} | {metrics['oos']['excess']:.2%} | "
            f"{full['strategy_cagr']:.2%} | {full['strategy_drawdown']:.2%} | "
            f"{full['maximum_intrabar_leverage']:.3f}X | "
            f"{'通过' if row['strict_3x_passed'] else '失败'} |"
        )
    lines += [
        "",
        "盘中杠杆先于保护性缩仓被观测；因此超过 3X 即视为失败，不能以随后被裁剪为由通过。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
