#!/usr/bin/env python3
"""Audit BTC daily SMA candidates on the independent pre-perpetual spot history."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark

from mastermind_tick.bar_research import ResearchBar
from mastermind_tick.sma_weekly import simple_moving_average

DATA_DIR = Path("data/history_btc_spot_daily")
OUTPUT_DIR = Path("reports/experiments/btc_spot_pre2020/2026-09-02")
START_MS = int(datetime(2017, 10, 1, tzinfo=UTC).timestamp() * 1000)
END_MS = int(datetime(2019, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000)
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
MAINTENANCE = Decimal("0.02")
SPOT_CAP = Decimal("0.5")
# A 2.5x order cap leaves room for daily-bar path uncertainty while keeping
# the observed futures leverage below the user's hard 3x limit.
LEVERAGE_CAP = Decimal("2.5")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_spot_bars()
    validate_daily_continuity(bars)
    fast = simple_moving_average(bars, 8)
    slow = simple_moving_average(bars, 40)
    variants = {
        "sma8-40-bear-minus0.1": targets(bars, fast, slow, 0, Decimal("-0.1")),
        "sma8-40-bear-flat": targets(bars, fast, slow, 0, Decimal("0")),
        "sma8-40-slope5-bear-minus0.1": targets(bars, fast, slow, 5, Decimal("-0.1")),
        "sma8-40-slope5-bear-flat": targets(bars, fast, slow, 5, Decimal("0")),
    }
    end_2017 = int(datetime(2017, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000)
    start_2018 = int(datetime(2018, 1, 1, tzinfo=UTC).timestamp() * 1000)
    end_2018 = int(datetime(2018, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000)
    start_2019 = int(datetime(2019, 1, 1, tzinfo=UTC).timestamp() * 1000)
    periods = {
        "full_pre2020": (START_MS, END_MS),
        "2017Q4": (START_MS, end_2017),
        "2018": (start_2018, end_2018),
        "2019": (start_2019, END_MS),
    }
    results = {}
    for name, variant_targets in variants.items():
        results[name] = {}
        for period, (start, end) in periods.items():
            result = replay_segregated(
                bars,
                variant_targets,
                [[] for _ in bars],
                start,
                end,
                spot_cap=SPOT_CAP,
                maintenance_rate=MAINTENANCE,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                enforce_effective_leverage_cap=True,
                maximum_futures_leverage=LEVERAGE_CAP,
            )
            bh = benchmark(bars, start, end)
            results[name][period] = {
                "strategy_return": result.net_return,
                "benchmark_return": bh["net_return"],
                "excess": result.net_return - bh["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": bh["max_drawdown"],
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                "liquidated": result.liquidated,
                "fees": result.total_fees,
                "rebalances": result.rebalances,
            }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "data": {
            "files": len(list(DATA_DIR.glob("BTCUSDT-1d-*.zip"))),
            "bars": len(bars),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
            "continuity": "one  UTC daily bar per day; no duplicate or missing open timestamps",
        },
        "protocol": {
            "signal": "completed daily SMA; next daily open execution",
            "sma": "fast 8, slow 40",
            "slope_variant": (
                "bear state additionally requires SMA40 below its value 5 days earlier"
            ),
            "pre_perpetual_funding": (
                "no funding applied; funding-gated short is represented by bear-flat"
            ),
            "costs": "10 bps fee + 5 bps slippage",
            "capital": (
                "50% spot and 50% futures collateral; 2.5x order cap used as a buffer "
                "for the hard 3x effective-leverage limit"
            ),
        },
        "periods": {name: [iso(start), iso(end)] for name, (start, end) in periods.items()},
        "results": results,
        "conclusion": {
            "status": "RESEARCH_ONLY",
            "reason": (
                "The pre-2020 spot history is an independent path check without perpetual funding; "
                "it can reject fragile price timing but cannot prove current futures profitability."
            ),
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def load_spot_bars() -> list[ResearchBar]:
    rows = []
    for path in sorted(DATA_DIR.glob("BTCUSDT-1d-*.zip")):
        with zipfile.ZipFile(path) as archive:
            member = archive.namelist()[0]
            with archive.open(member) as handle:
                for raw in io.TextIOWrapper(handle, encoding="utf-8"):
                    values = raw.strip().split(",")
                    if not values or not values[0].isdigit():
                        continue
                    rows.append(values)
    rows.sort(key=lambda value: int(value[0]))
    return [
        ResearchBar(
            start_ms=int(row[0]),
            end_ms=int(row[6]),
            open=Decimal(row[1]),
            high=Decimal(row[2]),
            low=Decimal(row[3]),
            close=Decimal(row[4]),
            volume=Decimal(row[5]),
        )
        for row in rows
    ]


def validate_daily_continuity(bars: list[ResearchBar]) -> None:
    if len({bar.start_ms for bar in bars}) != len(bars):
        raise ValueError("duplicate spot daily bars")
    gaps = [
        (left.start_ms, right.start_ms)
        for left, right in zip(bars, bars[1:], strict=False)
        if right.start_ms - left.start_ms != 86_400_000
    ]
    if gaps:
        raise ValueError(f"spot daily gaps: {gaps[:3]}")


def targets(bars, fast, slow, slope_lookback, bear_exposure):
    output = []
    for index, bar in enumerate(bars):
        if fast[index] is None or slow[index] is None:
            output.append(None)
            continue
        if slope_lookback and (index < slope_lookback or slow[index - slope_lookback] is None):
            output.append(None)
            continue
        bearish = (
            bar.close < slow[index]
            and fast[index] < slow[index]
            and (not slope_lookback or slow[index] < slow[index - slope_lookback])
        )
        output.append(bear_exposure if bearish else Decimal("1.5"))
    return tuple(output)


def iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value: float) -> str:
    return f"{value:.2%}"


def render(payload: dict) -> str:
    lines = [
        "# BTC Spot Pre-2020 Independent Audit",
        "",
        "使用 Binance BTCUSDT 现货日线 2017-08 至 2019-12 的独立价格路径。没有永续 Funding，"
        "因此同时报告原始空头版本和空头禁用（bear-flat）版本。",
        "",
        (
            f"数据：{payload['data']['bars']} 根日线，"
            f"{payload['data']['first']} 至 {payload['data']['last']}；"
            "连续性检查无缺口。"
        ),
        "",
        "| 版本 | 区间 | 策略收益 | B&H | 超额 | 策略DD | B&H DD | 盘中杠杆 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, periods in payload["results"].items():
        for period, row in periods.items():
            lines.append(
                f"| `{variant}` | {period} | {pct(row['strategy_return'])} | "
                f"{pct(row['benchmark_return'])} | {pct(row['excess'])} | "
                f"{pct(row['strategy_drawdown'])} | {pct(row['benchmark_drawdown'])} | "
                f"{row['maximum_intrabar_leverage']:.3f}X |"
            )
    lines += [
        "",
        "## 解释",
        "",
        "这是当前永续回测之外的独立价格路径检查，不把现货结果与含 Funding 的永续结果混合。"
        "如果一个机制在此处失败，不能宣称它跨市场阶段稳健；如果通过，也仍需新的永续前向数据。",
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
