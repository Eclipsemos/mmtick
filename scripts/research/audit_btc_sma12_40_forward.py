#!/usr/bin/env python3
"""Update the frozen BTC daily SMA12/40 forward-observation ledger."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

FREEZE_MS = int(datetime(2026, 9, 2, 8, tzinfo=UTC).timestamp() * 1000)
OUTPUT = Path("reports/experiments/btc_sma12_40_forward/2026-09-02")


def build_targets(bars):
    daily, ends = aggregate_complete_periods(bars, "1d")
    fast = simple_moving_average(daily, 12)
    slow = simple_moving_average(daily, 40)
    dense = tuple(
        None
        if fast[index] is None or slow[index] is None
        else Decimal("0")
        if bar.close < slow[index] and fast[index] < slow[index]
        else Decimal("1.5")
        for index, bar in enumerate(daily)
    )
    return map_targets_to_source(len(bars), dense, ends)


def main() -> None:
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    targets = build_targets(bars)
    observed = [bar for bar in bars if bar.start_ms >= FREEZE_MS]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "freeze_timestamp": iso(FREEZE_MS),
        "candidate": "daily SMA12/40 bear-flat; active 1.5X; bear 0X",
        "protocol": {
            "signal": "completed UTC daily candle; next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical funding above 1X",
            "collateral": "50% spot; 50% isolated USD-M margin",
            "futures_opening_cap": "2.5X",
            "hard_effective_leverage_cap": "3X",
            "selection": "parameters frozen before the observation start",
        },
        "data_last_complete": iso(bars[-1].end_ms),
        "forward_bars": len(observed),
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


def append_ledger(payload: dict) -> None:
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
    period_end = payload.get("period", [None, None])[1]
    if period_end is None:
        return
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


def render(payload: dict) -> str:
    lines = [
        "# BTC SMA12/40 冻结后前向观察",
        "",
        f"冻结时间：`{payload['freeze_timestamp']}`；参数、成本和杠杆限制保持不变。",
        f"最新完整数据：`{payload['data_last_complete']}`；"
        f"冻结后完整 15m K 线：{payload['forward_bars']}。",
        "",
    ]
    if payload["status"] != "FORWARD_OBSERVATION":
        return "\n".join(lines + ["状态：**AWAITING_FORWARD_DATA**。", ""])
    lines.extend(
        [
            "| 指标 | 策略 | B&H |",
            "|---|---:|---:|",
            f"| 收益 | {payload['strategy_return']:.2%} | {payload['benchmark_return']:.2%} |",
            f"| 超额 | {payload['excess']:.2%} | - |",
            f"| 最大回撤 | {payload['strategy_drawdown']:.2%} | "
            f"{payload['benchmark_drawdown']:.2%} |",
            f"| 最高盘中有效杠杆 | {payload['maximum_intrabar_leverage']:.3f}X | - |",
            "",
            f"状态：**FORWARD_OBSERVATION**；清算：`{payload['liquidated']}`。",
            "",
            "该样本只用于冻结后观察，不用于修改 SMA、暴露或成本参数。",
            "",
        ]
    )
    return "\n".join(lines)


def iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
