#!/usr/bin/env python3
"""Update the frozen BTC SMA10/40 hysteresis forward-observation ledger."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_sma12_40 as base
from research_btc_collateral_architecture import replay_segregated

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

FREEZE_MS = int(datetime(2026, 9, 2, 8, tzinfo=UTC).timestamp() * 1000)
OUTPUT = Path("reports/experiments/btc_hysteresis_1p25_strict3x/2026-09-02-forward")


def main() -> None:
    futures_15m = base.load_market("BTCUSDT")
    daily, ends = aggregate_complete_periods(futures_15m, "1d")
    targets = map_targets_to_source(len(futures_15m), build_targets(daily), ends)
    bars = futures_15m
    funding = funding_by_bar(bars, base.load_funding("BTCUSDT", futures_15m))
    observed = [bar for bar in bars if bar.start_ms >= FREEZE_MS]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "freeze_timestamp": iso(FREEZE_MS),
        "candidate": "SMA10/40 hysteresis; enter bear after 2 days; exit after 1; active 1.25X",
        "data_last_complete": iso(bars[-1].end_ms),
        "forward_bars": len(observed),
        "hard_effective_leverage_cap": "3X",
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
            maximum_futures_leverage=Decimal("3"),
        )
        benchmark = base.benchmark(bars, start, end)
        payload["status"] = "FORWARD_OBSERVATION"
        payload["period"] = [iso(start), iso(end)]
        payload["strategy_return"] = result.net_return
        payload["benchmark_return"] = benchmark["net_return"]
        payload["excess"] = result.net_return - benchmark["net_return"]
        payload["strategy_drawdown"] = result.max_drawdown
        payload["benchmark_drawdown"] = benchmark["max_drawdown"]
        payload["maximum_intrabar_leverage"] = result.maximum_observed_futures_leverage
        payload["liquidated"] = result.liquidated
    else:
        payload["status"] = "AWAITING_FORWARD_DATA"

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    append_ledger(payload)
    print(OUTPUT / "README.md")


def iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


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
    existing: set[str] = set()
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            existing = {row["period_end"] for row in csv.DictReader(handle)}
    period_end = payload.get("period", [None, None])[1]
    if period_end is None or period_end in existing:
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
        "# BTC SMA10/40 迟滞策略冻结后前向观察",
        "",
        f"冻结时间：`{payload['freeze_timestamp']}`；参数和杠杆保持不变。",
        f"最新完整数据：`{payload['data_last_complete']}`；"
        f"冻结后完整 15m K 线：{payload['forward_bars']}。",
        "",
    ]
    if payload["status"] == "FORWARD_OBSERVATION":
        lines += [
            "| 指标 | 策略 | B&H |",
            "|---|---:|---:|",
            f"| 收益 | {payload['strategy_return']:.2%} | {payload['benchmark_return']:.2%} |",
            f"| 超额 | {payload['excess']:.2%} | - |",
            f"| 最大回撤 | {payload['strategy_drawdown']:.2%} | "
            f"{payload['benchmark_drawdown']:.2%} |",
            f"| 最高盘中有效杠杆 | {payload['maximum_intrabar_leverage']:.3f}X | - |",
            "",
            f"状态：**{payload['status']}**；清算：`{payload['liquidated']}`。",
        ]
    else:
        lines += ["状态：**AWAITING_FORWARD_DATA**。"]
    return "\n".join(lines) + "\n"


def build_targets(bars):
    fast = simple_moving_average(bars, 10)
    slow = simple_moving_average(bars, 40)
    state = None
    bear_count = recovery_count = 0
    output = []
    for index, bar in enumerate(bars):
        if fast[index] is None or slow[index] is None:
            output.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= 2:
            state = "bear"
        elif state == "bear" and recovery_count >= 1:
            state = "active"
        output.append(Decimal("0") if state == "bear" else Decimal("1.25"))
    return tuple(output)


if __name__ == "__main__":
    main()
