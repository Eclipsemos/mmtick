#!/usr/bin/env python3
"""Audit a predeclared faster daily-SMA ensemble challenger for BTC."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import annualized_return, replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_sma_fast_ensemble_challenger/2026-09-02")
COMPONENTS = ((8, 40), (12, 40), (15, 40))
BULL = Decimal("1.5")
BEAR = Decimal("-0.1")
FUNDING_THRESHOLD = Decimal("0.0001")
OPEN_CAP = Decimal("2.5")
EFFECTIVE_CAP = 3.0
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
FREEZE_MS = int(datetime(2026, 9, 2, 8, tzinfo=UTC).timestamp() * 1000)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    daily, ends = aggregate_complete_periods(bars, "1d")
    dense = build_targets(daily)
    mapped = map_targets_to_source(len(bars), dense, ends)
    targets = apply_funding_gate(mapped, funding)
    metrics = {}
    full_result = None
    for name, (start, end) in splits.items():
        result = replay(bars, targets, funding, start, end, record=name == "full")
        metrics[name] = public(result, benchmarks[name])
        if name == "full":
            full_result = result
    logs, bh_logs = paired_daily_log_returns(
        bars, full_result.equity_curve, 100_000.0, start_ms=splits["full"][0]
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            logs, bh_logs, block_days=block, samples=10_000, seed=20261200 + block
        )
        for block in (7, 30, 90)
    }
    years = years_between(*splits["full"])
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": {
            "components": COMPONENTS,
            "bull_exposure": str(BULL),
            "bear_exposure": str(BEAR),
            "funding_threshold": str(FUNDING_THRESHOLD),
            "selection_note": "exploration challenger; not a clean OOS selection",
        },
        "protocol": {
            "signal": "completed UTC daily SMA values; next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical Funding",
            "open_cap": str(OPEN_CAP),
            "effective_cap": "<=3x including intrabar-low audit",
        },
        "data": {
            "bars": len(bars),
            "daily_bars": len(daily),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
            "evaluation_start": iso(splits["full"][0]),
            "evaluation_end": iso(splits["full"][1]),
        },
        "benchmarks": benchmarks,
        "metrics": metrics,
        "full_cagr": annualized_return(metrics["full"]["net_return"], years),
        "benchmark_cagr": annualized_return(benchmarks["full"]["net_return"], years),
        "hard_cap_passed": all(
            row["maximum_intrabar_leverage"] <= EFFECTIVE_CAP + 1e-9 for row in metrics.values()
        ),
        "rolling": rolling(bars, targets, funding, *splits["full"]),
        "yearly": yearly(bars, targets, funding, *splits["full"]),
        "bootstrap": bootstrap,
        "forward_observation": forward(bars, targets, funding, FREEZE_MS),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_targets(daily):
    streams = []
    for fast, slow in COMPONENTS:
        fast_sma = simple_moving_average(daily, fast)
        slow_sma = simple_moving_average(daily, slow)
        streams.append(
            tuple(
                None
                if fast_sma[i] is None or slow_sma[i] is None
                else BEAR
                if bar.close < slow_sma[i] and fast_sma[i] < slow_sma[i]
                else BULL
                for i, bar in enumerate(daily)
            )
        )
    return tuple(
        None if any(value is None for value in values) else sum(values, Decimal("0")) / 3
        for values in zip(*streams, strict=True)
    )


def apply_funding_gate(targets, funding):
    state = Decimal("0")
    latest = Decimal("0")
    output = []
    for target, events in zip(targets, funding, strict=True):
        if target is not None:
            state = Decimal(target)
        for event in events:
            latest = event.rate
        output.append(Decimal("1") if state > 1 and latest > FUNDING_THRESHOLD else state)
    return tuple(output)


def replay(bars, targets, funding, start, end, *, record=False):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        record_equity=record,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=OPEN_CAP,
    )


def public(result, baseline):
    return {
        "net_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "max_drawdown": result.max_drawdown,
        "benchmark_drawdown": baseline["max_drawdown"],
        "fees": result.total_fees,
        "funding": result.total_funding,
        "liquidated": result.liquidated,
        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
    }


def rolling(bars, targets, funding, start_ms, end_ms):
    first = datetime.fromtimestamp(start_ms / 1000, UTC)
    last = datetime.fromtimestamp(end_ms / 1000, UTC)
    output = {}
    for label, days in (("1y", 365), ("2y", 730), ("3y", 1095)):
        rows = []
        cursor = first
        while cursor + timedelta(days=days) <= last:
            stop = cursor + timedelta(days=days) - timedelta(milliseconds=1)
            left, right = int(cursor.timestamp() * 1000), int(stop.timestamp() * 1000)
            result = replay(bars, targets, funding, left, right)
            baseline = benchmark(bars, left, right)
            rows.append(
                (
                    result.net_return - baseline["net_return"],
                    result.net_return > baseline["net_return"],
                    result.net_return > baseline["net_return"]
                    and result.max_drawdown >= baseline["max_drawdown"],
                )
            )
            cursor += timedelta(days=30)
        excess = sorted(row[0] for row in rows)
        output[label] = {
            "windows": len(rows),
            "return_win_rate": ratio(row[1] for row in rows),
            "return_and_drawdown_win_rate": ratio(row[2] for row in rows),
            "median_excess": excess[len(excess) // 2] if excess else None,
            "worst_excess": excess[0] if excess else None,
        }
    return output


def yearly(bars, targets, funding, start_ms, end_ms):
    first_year = datetime.fromtimestamp(start_ms / 1000, UTC).year
    last_year = datetime.fromtimestamp(end_ms / 1000, UTC).year
    rows = []
    for year in range(first_year, last_year + 1):
        left = max(start_ms, int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000))
        right = min(end_ms, int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1)
        result = replay(bars, targets, funding, left, right)
        baseline = benchmark(bars, left, right)
        rows.append(
            {
                "year": year,
                "strategy_return": result.net_return,
                "benchmark_return": baseline["net_return"],
                "excess": result.net_return - baseline["net_return"],
            }
        )
    return rows


def forward(bars, targets, funding, freeze_ms):
    observed = [bar for bar in bars if bar.start_ms >= freeze_ms]
    if not observed:
        return {"status": "AWAITING_FORWARD_DATA", "bars": 0}
    start, end = observed[0].start_ms, observed[-1].start_ms
    result = replay(bars, targets, funding, start, end)
    baseline = benchmark(bars, start, end)
    return {
        "status": "FORWARD_OBSERVATION",
        "bars": len(observed),
        "strategy_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
    }


def ratio(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC Faster SMA Ensemble Challenger — Strict 3X",
        "",
        (
            "固定 SMA8/40、SMA12/40、SMA15/40 等权；熊市 -0.1X，牛市 1.5X；"
            "Funding >0.01% 时额外暴露降回 1X。"
        ),
        "该配置来自探索网格，OOS 曾被查看，不能视为干净盲测。",
        "",
        "| 区间 | 策略 | B&H | 超额 | 策略DD | 盘中最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = payload["metrics"][name]
        lines.append(
            f"| {name} | {pct(row['net_return'])} | {pct(row['benchmark_return'])} | "
            f"{pct(row['excess'])} | {pct(row['max_drawdown'])} | "
            f"{row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        f"Full CAGR：{pct(payload['full_cagr'])}；B&H CAGR：{pct(payload['benchmark_cagr'])}。",
        f"硬杠杆约束：{'通过' if payload['hard_cap_passed'] else '失败'}。",
        "",
        "## Bootstrap",
        "",
    ]
    for block, row in payload["bootstrap"].items():
        lines.append(
            f"- {block}: 超过 B&H {row['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += ["", "## Rolling windows", ""]
    for label, row in payload["rolling"].items():
        lines.append(
            f"- {label}: 超过 B&H {row['return_win_rate']:.2%}；"
            f"收益与 DD 同胜 {row['return_and_drawdown_win_rate']:.2%}；"
            f"最差超额 {pct(row['worst_excess'])}。"
        )
    lines += ["", "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
