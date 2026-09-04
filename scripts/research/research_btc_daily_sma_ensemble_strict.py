#!/usr/bin/env python3
"""Audit a fixed equal-weight daily-SMA ensemble under a strict 3x effective cap."""

from __future__ import annotations

import json
from dataclasses import asdict
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

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_ensemble_strict/2026-09-02")
COMPONENTS = (
    {"id": "sma7-35-short", "fast": 7, "slow": 35, "bear": Decimal("-0.1")},
    {"id": "sma8-40-short", "fast": 8, "slow": 40, "bear": Decimal("-0.1")},
    {"id": "sma12-40-flat", "fast": 12, "slow": 40, "bear": Decimal("0")},
)
BULL = Decimal("1.5")
SPOT_CAP = Decimal("0.5")
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
    component_dense = {
        item["id"]: build_dense_targets(daily, item["fast"], item["slow"], item["bear"])
        for item in COMPONENTS
    }
    ensemble_dense = equal_weight_targets(tuple(component_dense.values()))
    target_sets = {
        **{
            name: map_targets_to_source(len(bars), targets, ends)
            for name, targets in component_dense.items()
        },
        "equal_weight_ensemble": map_targets_to_source(len(bars), ensemble_dense, ends),
    }
    metrics = {}
    full_results = {}
    for candidate_id, targets in target_sets.items():
        metrics[candidate_id] = {}
        for name, bounds in splits.items():
            result = replay(
                bars,
                targets,
                funding,
                *bounds,
                record_equity=name == "full",
            )
            metrics[candidate_id][name] = public(result, benchmarks[name])
            if name == "full":
                full_results[candidate_id] = result

    rolling = evaluate_rolling(
        bars,
        target_sets["equal_weight_ensemble"],
        funding,
        splits["full"][0],
        splits["full"][1],
    )
    yearly = evaluate_yearly(
        bars,
        target_sets["equal_weight_ensemble"],
        funding,
        splits["full"][0],
        splits["full"][1],
    )

    full = full_results["equal_weight_ensemble"]
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=splits["full"][0]
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20261002 + block,
        )
        for block in (7, 30, 90)
    }
    years = years_between(*splits["full"])
    ensemble = metrics["equal_weight_ensemble"]
    hard_cap_passed = all(
        row["maximum_intrabar_leverage"] <= EFFECTIVE_CAP + 1e-9 for row in ensemble.values()
    )
    forward = forward_observation(bars, target_sets["equal_weight_ensemble"], funding, FREEZE_MS)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "construction": "fixed one-third weight per component; no weight search",
            "components": [
                {
                    "id": item["id"],
                    "fast": item["fast"],
                    "slow": item["slow"],
                    "bear_exposure": str(item["bear"]),
                    "bull_exposure": str(BULL),
                }
                for item in COMPONENTS
            ],
            "signal": "completed UTC daily bar; execute at next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "wallets": "50% spot cap with separately modelled USD-M collateral",
            "open_cap": str(OPEN_CAP),
            "effective_cap": "<=3x including 15m intrabar-low audit",
        },
        "data": {
            "bars": len(bars),
            "daily_bars": len(daily),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
        },
        "benchmarks": benchmarks,
        "metrics": metrics,
        "annualized": {
            candidate_id: annualized_return(rows["full"]["net_return"], years)
            for candidate_id, rows in metrics.items()
        },
        "hard_cap_passed": hard_cap_passed,
        "all_splits_beat_bh": all(row["excess"] > 0 for row in ensemble.values()),
        "rolling": rolling,
        "yearly": yearly,
        "forward_observation": forward,
        "bootstrap": bootstrap,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_dense_targets(bars, fast_period, slow_period, bear_exposure):
    fast = simple_moving_average(bars, fast_period)
    slow = simple_moving_average(bars, slow_period)
    output = []
    for index, bar in enumerate(bars):
        if fast[index] is None or slow[index] is None:
            output.append(None)
        elif bar.close < slow[index] and fast[index] < slow[index]:
            output.append(bear_exposure)
        else:
            output.append(BULL)
    return tuple(output)


def equal_weight_targets(target_streams):
    if not target_streams or len({len(stream) for stream in target_streams}) != 1:
        raise ValueError("target streams must be non-empty and equally sized")
    output = []
    count = Decimal(len(target_streams))
    for values in zip(*target_streams, strict=True):
        known = [Decimal(value) for value in values if value is not None]
        output.append(None if len(known) != len(values) else sum(known) / count)
    return tuple(output)


def replay(bars, targets, funding, start, end, *, record_equity=False):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=SPOT_CAP,
        maintenance_rate=Decimal("0.02"),
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=OPEN_CAP,
    )


def public(result, baseline):
    raw = asdict(result)
    return {
        "net_return": raw["net_return"],
        "benchmark_return": baseline["net_return"],
        "excess": raw["net_return"] - baseline["net_return"],
        "max_drawdown": raw["max_drawdown"],
        "benchmark_drawdown": baseline["max_drawdown"],
        "fees": raw["total_fees"],
        "funding": raw["total_funding"],
        "liquidated": raw["liquidated"],
        "maximum_open_leverage": raw["maximum_controlled_open_futures_leverage"],
        "maximum_intrabar_leverage": raw["maximum_observed_futures_leverage"],
    }


def evaluate_rolling(bars, targets, funding, start_ms, end_ms):
    first = datetime.fromtimestamp(start_ms / 1000, UTC)
    last = datetime.fromtimestamp(end_ms / 1000, UTC)
    output = {}
    for label, days in (("1y", 365), ("2y", 730), ("3y", 1095)):
        rows = []
        cursor = first
        while cursor + timedelta(days=days) <= last:
            stop = cursor + timedelta(days=days) - timedelta(milliseconds=1)
            left = int(cursor.timestamp() * 1000)
            right = int(stop.timestamp() * 1000)
            result = replay(bars, targets, funding, left, right)
            baseline = benchmark(bars, left, right)
            rows.append(
                {
                    "start": cursor.isoformat(),
                    "end": stop.isoformat(),
                    "strategy_return": result.net_return,
                    "benchmark_return": baseline["net_return"],
                    "excess": result.net_return - baseline["net_return"],
                    "strategy_drawdown": result.max_drawdown,
                    "benchmark_drawdown": baseline["max_drawdown"],
                    "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                    "beats_return": result.net_return > baseline["net_return"],
                    "beats_return_and_drawdown": (
                        result.net_return > baseline["net_return"]
                        and result.max_drawdown >= baseline["max_drawdown"]
                    ),
                }
            )
            cursor += timedelta(days=30)
        excess = sorted(row["excess"] for row in rows)
        output[label] = {
            "windows": len(rows),
            "return_win_rate": ratio(row["beats_return"] for row in rows),
            "return_and_drawdown_win_rate": ratio(row["beats_return_and_drawdown"] for row in rows),
            "median_excess": excess[len(excess) // 2] if excess else None,
            "worst_excess": excess[0] if excess else None,
            "maximum_intrabar_leverage": max(
                (row["maximum_intrabar_leverage"] for row in rows), default=0.0
            ),
        }
    return output


def evaluate_yearly(bars, targets, funding, start_ms, end_ms):
    first_year = datetime.fromtimestamp(start_ms / 1000, UTC).year
    last_year = datetime.fromtimestamp(end_ms / 1000, UTC).year
    rows = []
    for year in range(first_year, last_year + 1):
        left = max(start_ms, int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000))
        right = min(
            end_ms,
            int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1,
        )
        if left > right:
            continue
        result = replay(bars, targets, funding, left, right)
        baseline = benchmark(bars, left, right)
        rows.append(
            {
                "year": year,
                "strategy_return": result.net_return,
                "benchmark_return": baseline["net_return"],
                "excess": result.net_return - baseline["net_return"],
                "strategy_drawdown": result.max_drawdown,
                "benchmark_drawdown": baseline["max_drawdown"],
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
            }
        )
    return rows


def ratio(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def forward_observation(bars, targets, funding, freeze_ms):
    observed = [bar for bar in bars if bar.start_ms >= freeze_ms]
    if not observed:
        return {"status": "AWAITING_FORWARD_DATA", "bars": 0}
    start = observed[0].start_ms
    end = observed[-1].start_ms
    result = replay(bars, targets, funding, start, end)
    baseline = benchmark(bars, start, end)
    return {
        "status": "FORWARD_OBSERVATION",
        "freeze": iso(freeze_ms),
        "period": [iso(start), iso(end)],
        "bars": len(observed),
        "strategy_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "strategy_drawdown": result.max_drawdown,
        "benchmark_drawdown": baseline["max_drawdown"],
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
    }


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC Daily SMA Equal-Weight Ensemble — Strict 3X",
        "",
        "固定等权组合 SMA7/35、SMA8/40 与 SMA12/40；不搜索组合权重。",
        "信号只使用完整日线，下一根 15m 开盘执行，并计入压力成本与 Funding。",
        "",
        (
            "| 方案 | Research超额 | Validation超额 | OOS超额 | Full收益 | "
            "Full CAGR | DD | 最高盘中杠杆 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for candidate_id, rows in payload["metrics"].items():
        lines.append(
            f"| `{candidate_id}` | {pct(rows['research']['excess'])} | "
            f"{pct(rows['validation']['excess'])} | {pct(rows['oos']['excess'])} | "
            f"{pct(rows['full']['net_return'])} | "
            f"{pct(payload['annualized'][candidate_id])} | "
            f"{pct(rows['full']['max_drawdown'])} | "
            f"{rows['full']['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        f"硬杠杆约束：{'通过' if payload['hard_cap_passed'] else '失败'}；"
        f"所有分段收益超过 B&H：{'是' if payload['all_splits_beat_bh'] else '否'}。",
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
    lines += ["", "## Yearly", "", "| 年份 | 策略 | B&H | 超额 |", "|---:|---:|---:|---:|"]
    for row in payload["yearly"]:
        lines.append(
            f"| {row['year']} | {pct(row['strategy_return'])} | "
            f"{pct(row['benchmark_return'])} | {pct(row['excess'])} |"
        )
    lines += [
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    forward = payload["forward_observation"]
    if forward["status"] == "FORWARD_OBSERVATION":
        lines[-1:-1] = [
            "## Forward observation",
            "",
            f"冻结后 {forward['bars']} 根 15m：策略 {pct(forward['strategy_return'])}，"
            f"B&H {pct(forward['benchmark_return'])}，超额 {pct(forward['excess'])}；"
            f"最高盘中杠杆 {forward['maximum_intrabar_leverage']:.3f}X。",
            "",
        ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
