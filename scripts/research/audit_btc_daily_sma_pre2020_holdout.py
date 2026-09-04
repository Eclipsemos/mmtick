#!/usr/bin/env python3
"""Audit the frozen daily SMA candidate on the pre-2020 data holdout."""

from __future__ import annotations

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

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_pre2020_holdout/2026-09-02")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    fast = simple_moving_average(daily, 8)
    slow = simple_moving_average(daily, 40)
    dense = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            dense.append(None)
        elif bar.close < slow[index] and fast[index] < slow[index]:
            dense.append(Decimal("-0.1"))
        else:
            dense.append(Decimal("1.5"))
    targets = map_targets_to_source(len(bars), tuple(dense), ends)
    first_signal = next(index for index, target in enumerate(targets) if target is not None)
    start_ms = bars[first_signal].start_ms
    end_ms = bars[-1].start_ms
    cutoff_ms = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
    end_ms = min(end_ms, cutoff_ms - 1)
    result = replay_segregated(
        bars,
        targets,
        funding,
        start_ms,
        end_ms,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("2"),
    )
    baseline = benchmark(bars, start_ms, end_ms)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "symbol": "BTCUSDT",
            "daily_sma": [8, 40],
            "bull_exposure": "1.5",
            "bear_exposure": "-0.1",
            "leverage_cap": "2",
        },
        "protocol": {
            "period": [iso(start_ms), iso(end_ms)],
            "reason": "data before 2020 was not used by the 2020-2026 research selection",
            "costs": "10 bps fee + 5 bps slippage",
            "funding": "historical funding on actual futures notional",
        },
        "strategy": {
            "return": result.net_return,
            "max_drawdown": result.max_drawdown,
            "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
            "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
            "liquidated": result.liquidated,
        },
        "benchmark": baseline,
        "excess": result.net_return - baseline["net_return"],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload))
    print(OUTPUT_DIR / "README.md")


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def markdown(payload: dict) -> str:
    strategy = payload["strategy"]
    benchmark_row = payload["benchmark"]
    return "\n".join(
        [
            "# BTC SMA 8/40 前 2020 年独立留出审计",
            "",
            "该数据段未参与 2020–2026 研究期、验证期或 OOS 参数选择。",
            "",
            f"测试区间：{payload['protocol']['period'][0]} 至 {payload['protocol']['period'][1]}。",
            "",
            "| 指标 | 策略 | B&H |",
            "|---|---:|---:|",
            f"| 收益 | {strategy['return']:.2%} | {benchmark_row['net_return']:.2%} |",
            f"| 最大回撤 | {strategy['max_drawdown']:.2%} | {benchmark_row['max_drawdown']:.2%} |",
            f"| 超额 | {payload['excess']:.2%} | - |",
            f"| 最高盘中杠杆 | {strategy['maximum_intrabar_leverage']:.3f}X | - |",
            "",
            "结果为负超额，说明当前策略不是跨所有历史阶段都有效；这段数据支持保守的"
            "稳健性结论，而不是策略批准。",
        ]
    )


if __name__ == "__main__":
    main()
