#!/usr/bin/env python3
"""Search defensive and bullish BTC exposure schedules capped at 3x."""

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

PERIODS = ((16, 32, 64, 128), (20, 40, 80, 160), (25, 50, 100, 200))
INACTIVE = tuple(Decimal(value) for value in ("0", "0.25", "0.5", "0.75", "1"))
ACTIVE = tuple(Decimal(value) for value in ("1.5", "1.75", "2", "2.25", "2.5", "2.75", "3"))


def main() -> None:
    output_dir = Path("reports/experiments/btc_regime_exposure_grid/2026-09-02")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    splits = split_periods(bars)
    benchmarks = {split: benchmark(bars, start, end) for split, (start, end) in splits.items()}
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    rows = []
    for periods in PERIODS:
        signal = map_targets_to_source(len(bars), four_sma_targets(aggregate, periods), ends)
        for inactive in INACTIVE:
            for active in ACTIVE:
                targets = regime_exposure_targets(signal, active, inactive)
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
                        "excess_return": base.net_return - benchmarks[split]["net_return"],
                    }
                rows.append(
                    {
                        "id": (
                            f"4h-{'-'.join(map(str, periods))}-inactive{inactive}x-active{active}x"
                        ),
                        "periods": periods,
                        "inactive_exposure": str(inactive),
                        "active_exposure": str(active),
                        "metrics": metrics,
                    }
                )
    qualifying = [row for row in rows if qualifies(row, benchmarks)]
    qualifying.sort(key=lambda row: row["metrics"]["full"]["base"]["max_drawdown"], reverse=True)
    rows.sort(key=rank_score, reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "strategy": "4h four-SMA regime controls exposure; no shorting",
            "inactive_exposure": [str(value) for value in INACTIVE],
            "active_exposure": [str(value) for value in ACTIVE],
            "maximum_exposure": "3x",
            "execution": "completed 4h candle, next 15m open",
            "costs": "base 5+2 bps; stress 10+5 bps per fill",
            "qualification": (
                "full return above B&H, OOS return at least B&H, full drawdown no worse "
                "than B&H, no bankruptcy"
            ),
        },
        "benchmark": benchmarks,
        "qualifying": qualifying,
        "results": rows,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def qualifies(row, benchmarks):
    full = row["metrics"]["full"]["base"]
    oos = row["metrics"]["oos"]["base"]
    return (
        not full["bankrupt"]
        and full["net_return"] > benchmarks["full"]["net_return"]
        and oos["net_return"] >= benchmarks["oos"]["net_return"]
        and full["max_drawdown"] >= benchmarks["full"]["max_drawdown"]
    )


def rank_score(row):
    metrics = row["metrics"]
    return (
        metrics["research"]["excess_return"]
        + metrics["validation"]["excess_return"]
        + metrics["oos"]["excess_return"]
        + metrics["full"]["excess_return"] / 4
    )


def markdown(payload):
    lines = [
        "# BTC 双档动态暴露研究",
        "",
        "4h 四 SMA 趋势成立时采用高暴露，否则采用防御暴露；不做空，最高 3X。",
        "",
        "## 同时通过收益与回撤条件的配置",
        "",
        "| 配置 | 全样本收益 | 全样本超额 | DD | OOS收益 | OOS超额 | 压力全样本 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["qualifying"]:
        full = row["metrics"]["full"]
        oos = row["metrics"]["oos"]
        lines.append(
            f"| `{row['id']}` | {pct(full['base']['net_return'])} | "
            f"{pct(full['excess_return'])} | {pct(full['base']['max_drawdown'])} | "
            f"{pct(oos['base']['net_return'])} | {pct(oos['excess_return'])} | "
            f"{pct(full['stress']['net_return'])} |"
        )
    if not payload["qualifying"]:
        lines.append("| 无 | - | - | - | - | - | - |")
    lines += ["", "## 排名前 20", ""]
    lines += [
        "| 配置 | 全样本收益 | DD | OOS收益 | OOS超额 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in payload["results"][:20]:
        full = row["metrics"]["full"]
        oos = row["metrics"]["oos"]
        lines.append(
            f"| `{row['id']}` | {pct(full['base']['net_return'])} | "
            f"{pct(full['base']['max_drawdown'])} | {pct(oos['base']['net_return'])} | "
            f"{pct(oos['excess_return'])} |"
        )
    return "\n".join(lines) + "\n"


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
