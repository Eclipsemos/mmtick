#!/usr/bin/env python3
"""Evaluate the frozen BTC three-state candidate on post-selection data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import as_dict, benchmark, replay_dynamic
from research_btc_sma_trend import load_market
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

FROZEN_PERIODS = (16, 32, 64, 128)
FROZEN_BEAR_EXPOSURE = Decimal("0")
FROZEN_NEUTRAL_EXPOSURE = Decimal("1")
FROZEN_BULL_EXPOSURE = Decimal("2.5")
FORWARD_START_MS = int(datetime(2026, 8, 29, tzinfo=UTC).timestamp() * 1000)


def main() -> None:
    output_dir = Path("reports/experiments/btc_three_state_forward/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    period_targets = three_state_targets(
        aggregate,
        FROZEN_PERIODS,
        FROZEN_BEAR_EXPOSURE,
        FROZEN_BULL_EXPOSURE,
    )
    targets = map_targets_to_source(len(bars), period_targets, ends)
    end_ms = bars[-1].end_ms
    result = replay_dynamic(bars, targets, None, FORWARD_START_MS, end_ms)
    stress = replay_dynamic(
        bars,
        targets,
        None,
        FORWARD_START_MS,
        end_ms,
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
    )
    baseline = benchmark(bars, FORWARD_START_MS, end_ms)
    current_period_index = max(
        index for index, end_index in enumerate(ends) if end_index < len(bars)
    )
    current_target = period_targets[current_period_index]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "frozen_before_data_start": True,
        "frozen_config": {
            "timeframe": "4h",
            "sma_periods": FROZEN_PERIODS,
            "bear_exposure": str(FROZEN_BEAR_EXPOSURE),
            "neutral_exposure": str(FROZEN_NEUTRAL_EXPOSURE),
            "bull_exposure": str(FROZEN_BULL_EXPOSURE),
            "bear_rule": "close below SMA128 and SMA16 below SMA128",
            "bull_rule": "SMA16>SMA32>SMA64>SMA128",
            "neutral_rule": "all other states",
        },
        "forward_period": {
            "start": iso(FORWARD_START_MS),
            "end": iso(end_ms),
            "source_bars": sum(bar.start_ms >= FORWARD_START_MS for bar in bars),
        },
        "strategy": as_dict(result),
        "stress": as_dict(stress),
        "buy_and_hold": baseline,
        "excess_return": result.net_return - baseline["net_return"],
        "latest_completed_4h": iso(aggregate[current_period_index].end_ms),
        "current_target_exposure": str(current_target),
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def markdown(payload):
    strategy = payload["strategy"]
    baseline = payload["buy_and_hold"]
    return "\n".join(
        [
            "# BTC 三档策略冻结后前向观察",
            "",
            "参数在 2026-08-28 数据截止后冻结，本报告只使用 2026-08-29 之后数据，"
            "不根据前向结果修改参数。",
            "",
            f"观察区间：{payload['forward_period']['start']} 至 "
            f"{payload['forward_period']['end']}。",
            "",
            "| 指标 | 冻结策略 | BTC B&H |",
            "|---|---:|---:|",
            f"| 收益 | {pct(strategy['net_return'])} | {pct(baseline['net_return'])} |",
            f"| 最大回撤 | {pct(strategy['max_drawdown'])} | {pct(baseline['max_drawdown'])} |",
            f"| 压力成本收益 | {pct(payload['stress']['net_return'])} | - |",
            "",
            f"当前目标暴露：{payload['current_target_exposure']}X；"
            f"最新完整 4h：{payload['latest_completed_4h']}。",
            "",
            f"当前超额收益：{pct(payload['excess_return'])}。观察窗口仍非常短，"
            "不用于判断策略有效或无效。",
            "",
        ]
    )


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
