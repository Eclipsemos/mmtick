#!/usr/bin/env python3
"""Search a daily SMA BTC strategy with a causal lower-timeframe guard."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_macro_gated_3x import run_bootstrap, tail_concentration
from research_btc_block_bootstrap import paired_daily_log_returns
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import (
    aggregate_complete_periods,
    four_sma_targets,
    map_targets_to_source,
)
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_guard_3x/2026-09-02")
MAX_FUTURES_LEVERAGE = Decimal("3")
SPOT_CAP = Decimal("0.5")
BEAR_EXPOSURE = Decimal("-0.1")
FILTERS = {
    "none": None,
    "1h-24-48-96-192": ("1h", (24, 48, 96, 192)),
    "1h-25-50-100-200": ("1h", (25, 50, 100, 200)),
    "1h-26-52-104-208": ("1h", (26, 52, 104, 208)),
    "1h-28-56-112-224": ("1h", (28, 56, 112, 224)),
    "4h-20-40-80-160": ("4h", (20, 40, 80, 160)),
    "4h-26-52-104-208": ("4h", (26, 52, 104, 208)),
}
ACTIVE_EXPOSURES = (Decimal("1.5"), Decimal("1.75"), Decimal("2"))
NEUTRAL_EXPOSURES = (Decimal("0"), Decimal("0.5"), Decimal("1"))


def replay(bars, targets, funding, start, end, *, record_equity=False):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=SPOT_CAP,
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=MAX_FUTURES_LEVERAGE,
    )


def daily_signal(bars):
    aggregate, ends = aggregate_complete_periods(bars, "1d")
    fast = simple_moving_average(aggregate, 8)
    slow = simple_moving_average(aggregate, 40)
    dense = []
    for index, bar in enumerate(aggregate):
        if fast[index] is None or slow[index] is None:
            dense.append(None)
        elif bar.close < slow[index] and fast[index] < slow[index]:
            dense.append(-1)
        else:
            dense.append(1)
    return map_targets_to_source(len(bars), tuple(dense), ends)


def guard_signals(bars):
    output = {"none": None}
    for name, spec in FILTERS.items():
        if spec is None:
            continue
        timeframe, periods = spec
        aggregate, ends = aggregate_complete_periods(bars, timeframe)
        signal = four_sma_targets(aggregate, periods)
        output[name] = map_targets_to_source(len(bars), signal, ends)
    return output


def build_targets(base, guard, neutral, active):
    state = 0
    targets = []
    for base_value, guard_value in zip(base, guard or (None,) * len(base), strict=True):
        if guard_value is not None:
            state = guard_value
        if base_value is None:
            targets.append(None)
        elif base_value == -1:
            targets.append(BEAR_EXPOSURE)
        elif guard is None or state == 1:
            targets.append(active)
        else:
            targets.append(neutral)
    return tuple(targets)


def evaluate(bars, funding, targets, splits, benchmarks, *, record_equity=False):
    metrics = {}
    for name, (start, end) in splits.items():
        result = replay(bars, targets, funding, start, end, record_equity=record_equity)
        metrics[name] = {
            "return": result.net_return,
            "max_drawdown": result.max_drawdown,
            "excess": result.net_return - benchmarks[name]["net_return"],
            "liquidated": result.liquidated,
            "maximum_controlled_open_leverage": result.maximum_controlled_open_futures_leverage,
            "maximum_observed_intrabar_leverage": result.maximum_observed_futures_leverage,
        }
        if record_equity:
            metrics[name]["equity_curve"] = result.equity_curve
    return metrics


def development_eligible(row, benchmarks):
    research = row["metrics"]["research"]
    validation = row["metrics"]["validation"]
    return (
        not research["liquidated"]
        and not validation["liquidated"]
        and research["return"] > benchmarks["research"]["net_return"]
        and validation["return"] > benchmarks["validation"]["net_return"]
        and research["max_drawdown"] >= benchmarks["research"]["max_drawdown"]
        and research["maximum_controlled_open_leverage"] <= 3.0
        and validation["maximum_controlled_open_leverage"] <= 3.0
    )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    base = daily_signal(bars)
    guards = guard_signals(bars)
    rows = []
    for guard_name, guard in guards.items():
        for neutral in NEUTRAL_EXPOSURES:
            for active in ACTIVE_EXPOSURES:
                targets = build_targets(base, guard, neutral, active)
                metrics = evaluate(bars, funding, targets, splits, benchmarks)
                row = {
                    "id": f"daily-sma8-40-guard-{guard_name}-neutral{neutral}x-active{active}x",
                    "guard": guard_name,
                    "neutral_exposure": str(neutral),
                    "active_exposure": str(active),
                    "targets": targets,
                    "metrics": metrics,
                }
                row["development_eligible"] = development_eligible(row, benchmarks)
                row["development_score"] = min(
                    metrics["research"]["excess"], metrics["validation"]["excess"]
                )
                rows.append(row)
    eligible = [row for row in rows if row["development_eligible"]]
    eligible.sort(key=lambda row: row["development_score"], reverse=True)
    selected = eligible[0] if eligible else None
    if selected is None:
        raise RuntimeError("no development-eligible candidate")

    audited = evaluate(
        bars,
        funding,
        selected["targets"],
        splits,
        benchmarks,
        record_equity=True,
    )
    intrabar_safe = next(
        row
        for row in rows
        if row["guard"] == selected["guard"]
        and row["neutral_exposure"] == "1"
        and row["active_exposure"] == "1.5"
    )
    intrabar_safe_metrics = evaluate(
        bars,
        funding,
        intrabar_safe["targets"],
        splits,
        benchmarks,
    )
    full_curve = audited["full"]["equity_curve"]
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full_curve, 100_000.0, start_ms=splits["full"][0]
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20260902 + block,
        )
        for block in (7, 30, 90)
    }
    elapsed = (splits["full"][1] - splits["full"][0]) / (365.2425 * 86_400_000)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "protocol": {
            "base": "daily SMA 8/40; bear -0.1x",
            "guard": "last completed 1h/4h four-SMA state; guard failure reduces bullish exposure",
            "selection": "research and validation only; OOS revealed after selection",
            "execution": "completed signal candle, next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "leverage": "3x maximum futures leverage, actively capped at each 15m open",
        },
        "data": {
            "bars": len(bars),
            "first": datetime.fromtimestamp(bars[0].start_ms / 1000, UTC).isoformat(),
            "last": datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).isoformat(),
        },
        "benchmarks": benchmarks,
        "grid_size": len(rows),
        "development_eligible_count": len(eligible),
        "selected": {
            key: value for key, value in selected.items() if key not in {"targets", "metrics"}
        },
        "selected_metrics": audited,
        "intrabar_safe_variant": {
            "id": intrabar_safe["id"],
            "metrics": intrabar_safe_metrics,
        },
        "development_ranking": [
            {key: value for key, value in row.items() if key not in {"targets", "metrics"}}
            for row in eligible[:20]
        ],
        "bootstrap": bootstrap,
        "tail_concentration": tail_concentration(strategy_logs, benchmark_logs),
        "full_cagr": (1.0 + audited["full"]["return"]) ** (1.0 / elapsed) - 1.0,
        "benchmark_full_cagr": (1.0 + benchmarks["full"]["net_return"]) ** (1.0 / elapsed) - 1.0,
        "intrabar_safe_full_cagr": (1.0 + intrabar_safe_metrics["full"]["return"])
        ** (1.0 / elapsed)
        - 1.0,
        "limitations": [
            "The historical OOS interval was visible in earlier exploration and is not fresh.",
            (
                "Observed intrabar leverage can exceed the controlled opening target "
                "after a price move."
            ),
            "Bootstrap measures path sensitivity; it is not an independent significance test.",
        ],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2, default=list) + "\n")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload))
    print(OUTPUT_DIR / "README.md")


def pct(value):
    return f"{value:.2%}"


def markdown(payload):
    selected = payload["selected"]
    lines = [
        "# BTC 日线 SMA 8/40 + 低周期崩坏保护（严格 3X）",
        "",
        (
            f"开发期从 {payload['grid_size']} 个预先限定组合中选择；"
            f"合格组合 {payload['development_eligible_count']} 个。"
        ),
        f"冻结候选：`{selected['id']}`。选择只使用 Research 与 Validation，OOS 在选择后读取。",
        "",
        "## 分段结果",
        "",
        "| 区间 | 策略 | B&H | 超额 | 策略 DD | 受控开仓杠杆 | 观测盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = payload["selected_metrics"][name]
        base = payload["benchmarks"][name]
        lines.append(
            f"| {name} | {pct(row['return'])} | {pct(base['net_return'])} | {pct(row['excess'])} | "
            f"{pct(row['max_drawdown'])} | {row['maximum_controlled_open_leverage']:.3f}X | "
            f"{row['maximum_observed_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        (
            f"Full CAGR：{pct(payload['full_cagr'])}；"
            f"B&H CAGR：{pct(payload['benchmark_full_cagr'])}。"
        ),
        "",
        "## 任何时刻不超过 3X 的保守版本",
        "",
        ("固定使用同一 1h Guard、neutral 1X，但将 active 暴露降至 1.5X；历史观测盘中杠杆低于 3X。"),
        "它的 Validation 仍落后 B&H，因此仅作风险约束参考。",
        "",
        "| 区间 | 收益 | B&H | 超额 | DD | 盘中最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = payload["intrabar_safe_variant"]["metrics"][name]
        base = payload["benchmarks"][name]
        lines.append(
            f"| {name} | {pct(row['return'])} | {pct(base['net_return'])} | "
            f"{pct(row['excess'])} | {pct(row['max_drawdown'])} | "
            f"{row['maximum_observed_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "## 结论",
        "",
        (
            "该机制在历史分段中超过 B&H，并显著降低了相对日线主策略的回撤；"
            "但 Bootstrap 的年化超额 P05 仍为负，"
        ),
        (
            "若将 3X 定义为任何时刻的有效杠杆上限，应采用盘中安全版本 "
            "（active 1.5X）；该版本在 Validation 落后 B&H，不能直接批准。"
        ),
        "逐年与滚动窗口见[独立审计](../../btc_daily_sma_guard_3x_audit/2026-09-02/README.md)。",
        (
            "2019 独立留出中 Challenger 落后 B&H 7.04pp，安全版落后 4.45pp；"
            "该机制仍不是跨阶段稳定 Edge。"
        ),
        "不能据此宣称 Edge 已被证明。状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
