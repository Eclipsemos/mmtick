#!/usr/bin/env python3
"""Test a 1x BTC core with 4h SMA-regime exposure capped at 3x."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import (
    as_dict,
    benchmark,
    regime_exposure_targets,
    replay_dynamic,
)
from research_btc_sma_trend import load_market, split_periods

from mastermind_tick.sma_trend import (
    aggregate_complete_periods,
    four_sma_targets,
    map_targets_to_source,
)

PERIODS = (
    (16, 32, 64, 128),
    (18, 36, 72, 144),
    (20, 40, 80, 160),
    (22, 44, 88, 176),
    (24, 48, 96, 192),
    (25, 50, 100, 200),
    (26, 52, 104, 208),
    (28, 56, 112, 224),
    (30, 60, 120, 240),
)
EXPOSURES = tuple(
    Decimal(value) for value in ("1.25", "1.5", "1.75", "2", "2.25", "2.5", "2.75", "3")
)


def main() -> None:
    output_dir = Path("reports/experiments/btc_core_trend_grid/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    splits = split_periods(bars)
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    benchmark_rows = {split: benchmark(bars, start, end) for split, (start, end) in splits.items()}
    rows = []
    for periods in PERIODS:
        signal = map_targets_to_source(len(bars), four_sma_targets(aggregate, periods), ends)
        for exposure in EXPOSURES:
            targets = regime_exposure_targets(signal, exposure)
            metrics = {}
            for split, (start, end) in splits.items():
                base = replay_dynamic(bars, targets, None, start, end)
                stress = replay_dynamic(
                    bars,
                    targets,
                    None,
                    start,
                    end,
                    fee_bps=Decimal("10"),
                    slippage_bps=Decimal("5"),
                )
                metrics[split] = {
                    "base": as_dict(base),
                    "stress": as_dict(stress),
                    "excess_return": base.net_return - benchmark_rows[split]["net_return"],
                }
            yearly = yearly_results(bars, targets)
            rows.append(
                {
                    "id": f"4h-{'-'.join(map(str, periods))}-{exposure}x",
                    "periods": periods,
                    "active_exposure": str(exposure),
                    "metrics": metrics,
                    "yearly": yearly,
                    "years_beating_benchmark": sum(
                        value["excess_return"] > 0 for value in yearly.values()
                    ),
                }
            )
    rows.sort(key=lambda row: development_score(row), reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "strategy": "1x BTC core; raise exposure only while 4h four-SMA ordering is bullish",
            "maximum_exposure": "3x",
            "execution": "completed 4h candle, rebalance next 15m open",
            "base_cost": "5 bps fee and 2 bps slippage per fill",
            "stress_cost": "10 bps fee and 5 bps slippage per fill",
            "funding": (
                "excluded for spot B&H comparability; perpetual funding is a separate "
                "implementation question"
            ),
            "ranking": "research and validation only; OOS excluded from development score",
        },
        "data": {
            "bars": len(bars),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
        },
        "benchmark": benchmark_rows,
        "results": rows,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def yearly_results(bars, targets):
    output = {}
    last_year = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).year
    for year in range(2020, last_year + 1):
        start = utc_ms(year, 1, 1)
        end = min(utc_ms(year + 1, 1, 1) - 1, bars[-1].end_ms)
        if start > bars[-1].end_ms:
            continue
        result = replay_dynamic(bars, targets, None, start, end)
        baseline = benchmark(bars, start, end)
        output[str(year)] = {
            "net_return": result.net_return,
            "max_drawdown": result.max_drawdown,
            "benchmark_return": baseline["net_return"],
            "excess_return": result.net_return - baseline["net_return"],
        }
    return output


def development_score(row):
    research = row["metrics"]["research"]
    validation = row["metrics"]["validation"]
    return (
        research["excess_return"]
        + validation["excess_return"]
        + min(0.0, research["stress"]["net_return"]) * 2
        + min(0.0, validation["stress"]["net_return"]) * 2
    )


def markdown(payload):
    lines = [
        "# BTC 1X 核心 + 4h 趋势加仓研究",
        "",
        "保留 1X BTC 核心仓位，只有 4h 四 SMA 严格排列时提高暴露，最高 3X。"
        "参数按研究期与验证期排序，OOS 不参与评分。",
        "",
        "## 开发期排名与 OOS",
        "",
        "| 配置 | Research超额 | Validation超额 | OOS超额 | 全样本超额 | "
        "全样本收益 | DD | 胜过B&H年份 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"][:30]:
        metrics = row["metrics"]
        full = metrics["full"]["base"]
        lines.append(
            f"| `{row['id']}` | {pct(metrics['research']['excess_return'])} | "
            f"{pct(metrics['validation']['excess_return'])} | "
            f"{pct(metrics['oos']['excess_return'])} | {pct(metrics['full']['excess_return'])} | "
            f"{pct(full['net_return'])} | {pct(full['max_drawdown'])} | "
            f"{row['years_beating_benchmark']}/7 |"
        )
    lines += ["", "## BTC B&H", ""]
    for split, value in payload["benchmark"].items():
        lines.append(f"- {split}: {pct(value['net_return'])}, DD {pct(value['max_drawdown'])}")
    return "\n".join(lines) + "\n"


def utc_ms(year, month, day):
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
