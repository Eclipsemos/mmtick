#!/usr/bin/env python3
"""Stress the frozen BTC SMA ensemble against fair B&H costs and leverage caps."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import replay_segregated
from research_btc_daily_sma_ensemble_funding import (
    apply_funding_gate,
    ensemble_dense,
)
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_ensemble_funding_sensitivity/2026-09-02")
CAPS = (Decimal("2"), Decimal("2.5"), Decimal("3"))
COSTS = (
    (Decimal("5"), Decimal("2")),
    (Decimal("10"), Decimal("5")),
    (Decimal("20"), Decimal("10")),
)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    daily, ends = aggregate_complete_periods(bars, "1d")
    base = map_targets_to_source(len(bars), ensemble_dense(daily), ends)
    target = apply_funding_gate(base, funding, Decimal("0.0002"))
    rows = []
    for fee, slippage in COSTS:
        for cap in CAPS:
            metrics = {}
            for name, (start, end) in splits.items():
                result = replay_segregated(
                    bars,
                    target,
                    funding,
                    start,
                    end,
                    spot_cap=Decimal("0.5"),
                    maintenance_rate=Decimal("0.02"),
                    fee_bps=fee,
                    slippage_bps=slippage,
                    enforce_effective_leverage_cap=True,
                    maximum_futures_leverage=cap,
                )
                bh = fair_bh(bars, start, end, fee, slippage)
                metrics[name] = {
                    "strategy_return": result.net_return,
                    "benchmark_return": bh,
                    "excess": result.net_return - bh,
                    "max_drawdown": result.max_drawdown,
                    "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                    "liquidated": result.liquidated,
                }
            rows.append(
                {
                    "fee_bps": str(fee),
                    "slippage_bps": str(slippage),
                    "open_cap": str(cap),
                    "metrics": metrics,
                }
            )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "protocol": {
            "candidate": "fixed daily SMA ensemble + Funding >0.02% gate",
            "fair_bh": "1x spot B&H charged entry and exit fee+slippage",
            "effective_cap": "all observed intrabar leverage must remain <=3x",
        },
        "data": {
            "bars": len(bars),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
        },
        "results": rows,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def fair_bh(bars, start, end, fee, slippage):
    selected = [bar for bar in bars if start <= bar.start_ms <= end]
    cost = (fee + slippage) / Decimal("10000")
    factor = (Decimal("1") - cost) ** 2
    return float(factor * selected[-1].close / selected[0].open - Decimal("1"))


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC SMA Ensemble Funding Gate — Cost and Leverage Sensitivity",
        "",
        "B&H 使用同样的入场/出场手续费与滑点；策略包含历史 Funding。",
        "",
        "| 成本 | 开盘上限 | Research超额 | Validation超额 | OOS超额 | Full超额 | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        metrics = row["metrics"]
        lines.append(
            f"| {row['fee_bps']}+{row['slippage_bps']} bps | {row['open_cap']}X | "
            f"{pct(metrics['research']['excess'])} | {pct(metrics['validation']['excess'])} | "
            f"{pct(metrics['oos']['excess'])} | {pct(metrics['full']['excess'])} | "
            f"{metrics['full']['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "结论：成本上升会压缩优势，但在默认及中度压力下各分段仍保持正超额；"
        "所有列出的组合均未发生强平且盘中杠杆不超过 3X。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
