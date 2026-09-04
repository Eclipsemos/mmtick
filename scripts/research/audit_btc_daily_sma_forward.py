#!/usr/bin/env python3
"""Record untouched forward observations for the frozen BTC SMA candidate."""

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

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_forward/2026-09-02")
FREEZE_MS = int(datetime(2026, 9, 2, 8, tzinfo=UTC).timestamp() * 1000)


def build_targets(bars):
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
    return map_targets_to_source(len(bars), tuple(dense), ends)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    targets = build_targets(bars)
    observed = [bar for bar in bars if bar.start_ms >= FREEZE_MS]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "freeze": {
            "timestamp": iso(FREEZE_MS),
            "candidate": "daily SMA 8/40; bull +1.5X; bear -0.1X",
            "spot_cap": "0.5",
            "leverage_cap": "2.0X (below the allowed 3X maximum)",
            "selection": "all parameters frozen before this timestamp",
        },
        "data": {
            "last_complete_bar": iso(bars[-1].end_ms),
            "total_bars": len(bars),
            "forward_bars": len(observed),
        },
    }
    if not observed:
        payload["status"] = "AWAITING_FORWARD_DATA"
        payload["message"] = (
            "No complete 15m bar exists after the freeze timestamp; "
            "this run records no performance."
        )
    else:
        start_ms = observed[0].start_ms
        end_ms = observed[-1].start_ms
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
        payload["status"] = "FORWARD_OBSERVATION"
        payload["observation"] = {
            "period": [iso(start_ms), iso(end_ms)],
            "strategy_return": result.net_return,
            "benchmark_return": baseline["net_return"],
            "excess": result.net_return - baseline["net_return"],
            "strategy_drawdown": result.max_drawdown,
            "benchmark_drawdown": baseline["max_drawdown"],
            "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
            "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
            "liquidated": result.liquidated,
        }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload))
    print(OUTPUT_DIR / "README.md")


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def markdown(payload: dict) -> str:
    lines = [
        "# BTC SMA 8/40 严格前向观察",
        "",
        f"冻结时间：{payload['freeze']['timestamp']}。参数、杠杆和资金结构在此时间后不得修改。",
        "",
        f"最新完整数据：{payload['data']['last_complete_bar']}；"
        f"冻结后完整 15m K 线：{payload['data']['forward_bars']}。",
        "",
    ]
    if payload["status"] == "AWAITING_FORWARD_DATA":
        lines += [
            "状态：**AWAITING_FORWARD_DATA**",
            "",
            payload["message"],
        ]
    else:
        row = payload["observation"]
        lines += [
            "状态：**FORWARD_OBSERVATION**",
            "",
            "| 指标 | 策略 | B&H |",
            "|---|---:|---:|",
            f"| 收益 | {row['strategy_return']:.2%} | {row['benchmark_return']:.2%} |",
            f"| 超额 | {row['excess']:.2%} | - |",
            f"| 最大回撤 | {row['strategy_drawdown']:.2%} | {row['benchmark_drawdown']:.2%} |",
            f"| 最高盘中杠杆 | {row['maximum_intrabar_leverage']:.3f}X | - |",
        ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
