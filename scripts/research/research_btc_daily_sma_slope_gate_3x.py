#!/usr/bin/env python3
"""Test a causal SMA40-slope gate on the frozen BTC daily SMA strategy."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_slope_gate_3x/2026-09-02")
SLOPE_LOOKBACKS = tuple(range(1, 10))
FAST = 8
SLOW = 40
BEAR_EXPOSURE = Decimal("-0.1")
BULL_EXPOSURE = Decimal("1.5")
SPOT_CAP = Decimal("0.5")
MAX_LEVERAGE = Decimal("3")
MAINTENANCE = Decimal("0.02")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
HOLDOUT_START = 1571356800000  # 2019-10-18 23:45 UTC
HOLDOUT_END = 1577836799999  # 2019-12-31 23:59:59.999 UTC


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding_rates = load_funding("BTCUSDT", bars)
    funding = funding_by_bar(bars, funding_rates)
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    daily, ends = aggregate_complete_periods(bars, "1d")
    fast = simple_moving_average(daily, FAST)
    slow = simple_moving_average(daily, SLOW)
    rows = []
    for lookback in SLOPE_LOOKBACKS:
        targets = build_targets(daily, ends, len(bars), fast, slow, lookback)
        metrics = {}
        for name, (start, end) in splits.items():
            result = replay(
                bars,
                targets,
                funding,
                start,
                end,
            )
            metrics[name] = {
                **asdict(result),
                "excess": result.net_return - benchmarks[name]["net_return"],
            }
            metrics[name].pop("equity_curve", None)
        holdout = replay(bars, targets, funding, HOLDOUT_START, HOLDOUT_END)
        rows.append(
            {
                "id": f"daily-sma{FAST}-{SLOW}-sma{SLOW}-slope-{lookback}d",
                "lookback_days": lookback,
                "metrics": metrics,
                "holdout": {
                    **asdict(holdout),
                    "benchmark_return": benchmark(bars, HOLDOUT_START, HOLDOUT_END)["net_return"],
                },
            }
        )
    rows.sort(key=development_score, reverse=True)
    selected = rows[0]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "selected_by": "maximum minimum excess across research and validation",
        "selected": public(selected),
        "protocol": {
            "signal": (
                "completed UTC daily SMA 8/40; bear state additionally requires "
                "SMA40 below its value N completed days earlier"
            ),
            "execution": "next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "wallets": "50% spot and 50% USD-M collateral",
            "bull_exposure": str(BULL_EXPOSURE),
            "bear_exposure": str(BEAR_EXPOSURE),
            "hard_leverage": "maximum observed 15m-open and intrabar-low futures leverage <=3x",
            "selection": "lookback 1..9 days; OOS and 2019 holdout excluded from selection",
        },
        "data": {
            "bars": len(bars),
            "funding_events": len(funding_rates),
            "last": bars[-1].end_ms,
            "evaluation_years": years_between(*splits["full"]),
        },
        "benchmarks": benchmarks,
        "candidates": [public(row) for row in rows],
        "limitations": [
            "The OOS period is historical data already visible during prior research.",
            "The 2019 holdout is short and is not a guarantee of future performance.",
            "Bootstrap and fresh post-freeze forward observation remain required.",
        ],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_targets(daily, ends, source_count, fast, slow, lookback):
    dense = []
    for index, bar in enumerate(daily):
        if (
            fast[index] is None
            or slow[index] is None
            or index < lookback
            or slow[index - lookback] is None
        ):
            dense.append(None)
            continue
        bearish = (
            bar.close < slow[index]
            and fast[index] < slow[index]
            and slow[index] < slow[index - lookback]
        )
        dense.append(BEAR_EXPOSURE if bearish else BULL_EXPOSURE)
    return map_targets_to_source(source_count, tuple(dense), ends)


def replay(bars, targets, funding, start, end):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=SPOT_CAP,
        maintenance_rate=MAINTENANCE,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=MAX_LEVERAGE,
    )


def development_score(row):
    return min(row["metrics"][name]["excess"] for name in ("research", "validation"))


def public(row):
    return {
        "id": row["id"],
        "lookback_days": row["lookback_days"],
        "research": row["metrics"]["research"],
        "validation": row["metrics"]["validation"],
        "oos": row["metrics"]["oos"],
        "full": row["metrics"]["full"],
        "holdout_2019": row["holdout"],
    }


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC Daily SMA40 Slope Gate (Hard 3X)",
        "",
        "在日线 SMA 8/40 策略中，只有 SMA40 相对 N 个已完成日之前下降时才确认熊市低敞口。",
        "所有交易使用下一根 15m 开盘，计入压力成本与 Funding。",
        "",
        f"数据：{payload['data']['bars']:,} 根 15m，最后一根 {payload['data']['last']}；"
        f"网格 N=1..9，选择结果为 `{payload['selected']['id']}`。",
        "",
        "## 结果",
        "",
        (
            "| N | Research超额 | Validation超额 | OOS超额 | Full CAGR | Full DD | "
            "2019留出超额 | 盘中最高杠杆 |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["candidates"]:
        full, hold = row["full"], row["holdout_2019"]
        cagr = (1 + full["net_return"]) ** (1 / payload["data"]["evaluation_years"]) - 1
        obs = max(
            row[name]["maximum_observed_futures_leverage"]
            for name in ("research", "validation", "oos", "full")
        )
        holdout_excess = hold["net_return"] - hold["benchmark_return"]
        lines.append(
            f"| {row['lookback_days']} | {pct(row['research']['excess'])} | "
            f"{pct(row['validation']['excess'])} | {pct(row['oos']['excess'])} | "
            f"{pct(cagr)} | {pct(full['max_drawdown'])} | "
            f"{pct(holdout_excess)} | {obs:.3f}X |"
        )
    lines += [
        "",
        "## 解释",
        "",
        "选择只使用 Research 与 Validation；OOS 和 2019 留出用于事后审计。"
        "结果超过 B&H 不代表统计显著，且必须继续进行滚动窗口、区块 Bootstrap 和冻结后的前向观察。",
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
