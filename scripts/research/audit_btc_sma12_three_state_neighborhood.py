#!/usr/bin/env python3
"""Audit the fixed BTC three-state exposure across the SMA12/40 neighborhood."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import annualized_return, replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma12_three_state import build_dense_targets
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT = Path("reports/experiments/btc_sma12_three_state_neighborhood/2026-09-03")
FAST_PERIODS = (10, 11, 12, 13, 14)
SLOW_PERIODS = (36, 38, 40, 42, 44)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    rows = []
    for fast in FAST_PERIODS:
        for slow in SLOW_PERIODS:
            dense = build_dense_targets(
                daily,
                Decimal("1.25"),
                Decimal("1.5"),
                fast_period=fast,
                slow_period=slow,
            )
            targets = map_targets_to_source(len(bars), dense, ends)
            metrics = {
                name: evaluate(bars, targets, funding, bounds, benchmarks[name])
                for name, bounds in splits.items()
            }
            development_pass = all(
                metrics[name]["excess"] > 0 and metrics[name]["hard_3x_passed"]
                for name in ("research", "validation")
            )
            rows.append(
                {
                    "id": f"sma{fast}-{slow}",
                    "fast": fast,
                    "slow": slow,
                    "development_pass": development_pass,
                    "oos_pass": metrics["oos"]["excess"] > 0,
                    "full_pass": metrics["full"]["excess"] > 0,
                    "development_score": min(
                        annualized_excess(metrics[name], benchmarks[name], splits[name])
                        for name in ("research", "validation")
                    ),
                    "metrics": metrics,
                }
            )
    summary = summarize(rows)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": (
            "NEIGHBORHOOD_PASS / FORWARD_OBSERVATION_REQUIRED"
            if summary["plateau_passed"]
            else "NEIGHBORHOOD_FAIL / RESEARCH_ONLY"
        ),
        "protocol": {
            "center": "SMA12/40 remains frozen; neighborhood does not select a replacement",
            "exposures": "bear 0X; neutral 1.25X; bull 1.5X",
            "selection": "none; report all predeclared 5x5 neighbors",
            "execution": "completed UTC daily signal; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "hard_cap": "2.5X futures opening cap and <=3X intrabar leverage",
            "plateau_gate": "at least 60% pass development and 60% pass OOS",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "summary": summary,
        "rows": rows,
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def evaluate(bars, targets, funding, bounds, baseline):
    result = replay_segregated(
        bars,
        targets,
        funding,
        *bounds,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("2.5"),
    )
    return {
        "net_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "max_drawdown": result.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "hard_3x_passed": result.maximum_observed_futures_leverage <= 3,
        "liquidated": result.liquidated,
    }


def annualized_excess(metrics, baseline, bounds):
    years = years_between(*bounds)
    return annualized_return(metrics["net_return"], years) - annualized_return(
        baseline["net_return"], years
    )


def summarize(rows):
    development = [row for row in rows if row["development_pass"]]
    oos_excess = sorted(row["metrics"]["oos"]["excess"] for row in rows)
    center = next(row for row in rows if row["fast"] == 12 and row["slow"] == 40)
    development_rate = len(development) / len(rows)
    oos_rate = sum(row["oos_pass"] for row in rows) / len(rows)
    return {
        "neighbors": len(rows),
        "development_pass_count": len(development),
        "development_pass_rate": development_rate,
        "oos_pass_count": sum(row["oos_pass"] for row in rows),
        "oos_pass_rate": oos_rate,
        "full_pass_count": sum(row["full_pass"] for row in rows),
        "hard_3x_pass_count": sum(
            all(metrics["hard_3x_passed"] for metrics in row["metrics"].values()) for row in rows
        ),
        "median_oos_excess": oos_excess[len(oos_excess) // 2],
        "worst_oos_excess": min(oos_excess),
        "best_oos_excess": max(oos_excess),
        "center": center,
        "plateau_passed": development_rate >= 0.6 and oos_rate >= 0.6,
    }


def render(payload):
    summary = payload["summary"]
    lines = [
        "# BTC SMA12/40 Three-State Neighborhood Audit",
        "",
        "中心 SMA12/40 保持冻结；该报告只判断周围参数是否形成稳定平台。",
        "",
        f"- 开发期通过：{summary['development_pass_count']}/{summary['neighbors']} "
        f"({summary['development_pass_rate']:.2%})",
        f"- OOS 跑赢：{summary['oos_pass_count']}/{summary['neighbors']} "
        f"({summary['oos_pass_rate']:.2%})",
        f"- 全样本跑赢：{summary['full_pass_count']}/{summary['neighbors']}",
        f"- 全分段 3X 合规：{summary['hard_3x_pass_count']}/{summary['neighbors']}",
        f"- OOS 超额中位数：{summary['median_oos_excess']:.2%}",
        f"- OOS 最差/最佳：{summary['worst_oos_excess']:.2%} / {summary['best_oos_excess']:.2%}",
        "",
        "| SMA | 开发通过 | OOS超额 | Full CAGR | Full DD | 最高杠杆 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in sorted(payload["rows"], key=lambda item: (item["fast"], item["slow"])):
        full = row["metrics"]["full"]
        oos = row["metrics"]["oos"]
        lines.append(
            f"| {row['fast']}/{row['slow']} | {'是' if row['development_pass'] else '否'} | "
            f"{oos['excess']:.2%} | "
            f"{annualized_return(full['net_return'], years_between_full(payload)):.2%} | "
            f"{full['max_drawdown']:.2%} | {full['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        f"平台门槛：{'通过' if summary['plateau_passed'] else '未通过'}。",
        f"状态：**{payload['status']}**。",
        "",
    ]
    return "\n".join(lines)


def years_between_full(payload):
    center = payload["summary"]["center"]["metrics"]["full"]
    # All rows share the same fixed full interval. Recover years from the center CAGR inputs.
    del center
    first = datetime(2020, 1, 1, tzinfo=UTC)
    last = datetime.fromisoformat(payload["data"]["last"])
    return (last - first).total_seconds() / (365.2425 * 86_400)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
