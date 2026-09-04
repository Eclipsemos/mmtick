#!/usr/bin/env python3
"""Update the frozen BTC SMA12/40 three-state forward ledger."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma12_three_state import build_dense_targets
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

FREEZE_MS = int(datetime(2026, 9, 3, 1, tzinfo=UTC).timestamp() * 1000)
OUTPUT = Path("reports/experiments/btc_sma12_three_state/2026-09-03-forward")


def main() -> None:
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    targets = map_targets_to_source(
        len(bars),
        build_dense_targets(daily, Decimal("1.25"), Decimal("1.5")),
        ends,
    )
    observed = [bar for bar in bars if bar.start_ms >= FREEZE_MS]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "freeze_timestamp": iso(FREEZE_MS),
        "candidate": "SMA12/40: bear 0X, neutral 1.25X, bull 1.5X",
        "data_last_complete": iso(bars[-1].end_ms),
        "forward_bars": len(observed),
        "protocol": {
            "signal": "completed UTC daily candle; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "futures_opening_cap": "2.5X",
            "hard_effective_leverage_cap": "3X",
        },
    }
    if observed:
        start, end = observed[0].start_ms, observed[-1].start_ms
        result = replay_segregated(
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
            maximum_futures_leverage=Decimal("2.5"),
        )
        baseline = benchmark(bars, start, end)
        payload.update(
            {
                "status": "FORWARD_OBSERVATION",
                "period": [iso(start), iso(end)],
                "strategy_return": result.net_return,
                "benchmark_return": baseline["net_return"],
                "excess": result.net_return - baseline["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": baseline["max_drawdown"],
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                "liquidated": result.liquidated,
            }
        )
    else:
        payload["status"] = "AWAITING_FORWARD_DATA"

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    append_ledger(payload)
    print(OUTPUT / "README.md")


def append_ledger(payload):
    period_end = payload.get("period", [None, None])[1]
    if period_end is None:
        return
    path = OUTPUT / "ledger.csv"
    fields = (
        "generated_at",
        "period_end",
        "forward_bars",
        "strategy_return",
        "benchmark_return",
        "excess",
        "strategy_drawdown",
        "benchmark_drawdown",
        "maximum_intrabar_leverage",
        "liquidated",
    )
    existing = set()
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            existing = {row["period_end"] for row in csv.DictReader(handle)}
    if period_end in existing:
        return
    row = {field: payload.get(field, "") for field in fields}
    row["period_end"] = period_end
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if path.stat().st_size == 0:
            writer.writeheader()
        writer.writerow(row)


def render(payload):
    lines = [
        "# BTC SMA12/40 三状态冻结后观察",
        "",
        f"冻结时间：`{payload['freeze_timestamp']}`；冻结后不得修改参数。",
        f"最新完整数据：`{payload['data_last_complete']}`；"
        f"前向完整 15m K 线：{payload['forward_bars']}。",
        "",
    ]
    if payload["status"] == "AWAITING_FORWARD_DATA":
        return "\n".join(lines + ["状态：**AWAITING_FORWARD_DATA**。", ""])
    lines += [
        "| 指标 | 策略 | B&H |",
        "|---|---:|---:|",
        f"| 收益 | {payload['strategy_return']:.2%} | {payload['benchmark_return']:.2%} |",
        f"| 超额 | {payload['excess']:.2%} | - |",
        f"| 最大回撤 | {payload['strategy_drawdown']:.2%} | {payload['benchmark_drawdown']:.2%} |",
        f"| 最高盘中有效杠杆 | {payload['maximum_intrabar_leverage']:.3f}X | - |",
        "",
        f"状态：**FORWARD_OBSERVATION**；清算：`{payload['liquidated']}`。",
        "",
    ]
    return "\n".join(lines)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
