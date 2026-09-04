#!/usr/bin/env python3
"""Audit a fixed BTC trend/defensive hybrid under a hard 3x futures cap."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_macro_gated_3x import run_bootstrap
from research_btc_block_bootstrap import paired_daily_log_returns
from research_btc_collateral_architecture import replay_segregated
from research_btc_daily_sma_long_short_3x import build_targets as daily_targets
from research_btc_dynamic_exposure import benchmark
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT_DIR = Path("reports/experiments/btc_3x_hybrid_blend/2026-09-02")
MAX_FUTURES_LEVERAGE = Decimal("3")
STRESS_FEE_BPS = Decimal("10")
STRESS_SLIPPAGE_BPS = Decimal("5")
WEIGHTS = (Decimal("0"), Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1"))


def replay_component(bars, targets, funding, start, end, *, spot_cap):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=spot_cap,
        maintenance_rate=Decimal("0.02"),
        fee_bps=STRESS_FEE_BPS,
        slippage_bps=STRESS_SLIPPAGE_BPS,
        record_equity=True,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=MAX_FUTURES_LEVERAGE,
    )


def metric(component_a, component_b, weight):
    return {
        "daily_sma_weight": float(weight),
        "return": float(
            weight * Decimal(str(component_a.net_return))
            + (Decimal("1") - weight) * Decimal(str(component_b.net_return))
        ),
        "max_drawdown": combined_drawdown(
            component_a.equity_curve, component_b.equity_curve, weight
        ),
    }


def combined_drawdown(curve_a, curve_b, weight):
    values_a = dict(curve_a)
    values_b = dict(curve_b)
    timestamps = sorted(set(values_a) & set(values_b))
    if not timestamps:
        raise ValueError("component equity curves do not overlap")
    peak = 100_000.0
    drawdown = 0.0
    for timestamp in timestamps:
        value = float(weight) * values_a[timestamp] + (1.0 - float(weight)) * values_b[timestamp]
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    return drawdown


def split_metrics(bars, funding, targets_a, targets_b, splits, weight):
    output = {}
    for name, (start, end) in splits.items():
        left = replay_component(bars, targets_a, funding, start, end, spot_cap=Decimal("0.5"))
        right = replay_component(bars, targets_b, funding, start, end, spot_cap=Decimal("0.75"))
        baseline = benchmark(bars, start, end)
        result = metric(left, right, weight)
        result.update(
            {
                "benchmark_return": baseline["net_return"],
                "benchmark_drawdown": baseline["max_drawdown"],
                "excess_return": result["return"] - baseline["net_return"],
                "liquidated": left.liquidated or right.liquidated,
                "daily_sma_intrabar_leverage": left.maximum_observed_futures_leverage,
                "spot_core_intrabar_leverage": right.maximum_observed_futures_leverage,
            }
        )
        output[name] = result
    return output


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)

    daily, daily_ends = aggregate_complete_periods(bars, "1d")
    targets_a = daily_targets(daily, daily_ends, len(bars), 8, 40)
    four_hour, four_hour_ends = aggregate_complete_periods(bars, "4h")
    core_raw = three_state_targets(
        four_hour,
        (26, 52, 104, 208),
        Decimal("0"),
        Decimal("1.25"),
    )
    core_targets = funding_aware_targets(
        map_targets_to_source(len(bars), core_raw, four_hour_ends),
        funding,
        Decimal("1.25"),
        Decimal("0.0001"),
    )

    full_left = replay_component(bars, targets_a, funding, *splits["full"], spot_cap=Decimal("0.5"))
    full_right = replay_component(
        bars, core_targets, funding, *splits["full"], spot_cap=Decimal("0.75")
    )
    blends = [metric(full_left, full_right, weight) for weight in WEIGHTS]
    split_results = {
        str(weight): split_metrics(bars, funding, targets_a, core_targets, splits, weight)
        for weight in WEIGHTS
    }

    # The 25% and 50% blends are predeclared diagnostics, not OOS-selected parameters.
    bootstrap = {}
    for weight in (Decimal("0.25"), Decimal("0.5")):
        left = dict(full_left.equity_curve)
        right = dict(full_right.equity_curve)
        combined = tuple(
            (
                timestamp,
                float(weight) * left[timestamp] + float(Decimal("1") - weight) * right[timestamp],
            )
            for timestamp in sorted(set(left) & set(right))
        )
        strategy_logs, benchmark_logs = paired_daily_log_returns(
            bars, combined, 100_000.0, start_ms=splits["full"][0]
        )
        bootstrap[str(weight)] = {
            f"{block}d": run_bootstrap(
                strategy_logs,
                benchmark_logs,
                block_days=block,
                samples=10_000,
                seed=20260902 + block + int(weight * 100),
            )
            for block in (7, 30, 90)
        }

    elapsed_years = (splits["full"][1] - splits["full"][0]) / (365.2425 * 86_400_000)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "protocol": {
            "components": [
                "daily SMA 8/40, bear -0.1x, otherwise +1.5x, 50% spot cap",
                "4h SMA 26/52/104/208, bear 0x, neutral 1x, bull 1.25x, funding-aware",
            ],
            "combination": "fixed initial capital weights; no free rebalancing between sleeves",
            "execution": "completed signal candle, next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "maximum_futures_leverage": "3x with active effective-cap enforcement",
            "selection": "weights are a predeclared diagnostic grid; OOS is not used for selection",
        },
        "data": {
            "bars": len(bars),
            "first": datetime.fromtimestamp(bars[0].start_ms / 1000, UTC).isoformat(),
            "last": datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).isoformat(),
        },
        "benchmarks": {name: benchmark(bars, *bounds) for name, bounds in splits.items()},
        "component_full": {
            "daily_sma": {"return": full_left.net_return, "max_drawdown": full_left.max_drawdown},
            "spot_core": {"return": full_right.net_return, "max_drawdown": full_right.max_drawdown},
        },
        "blends": blends,
        "split_results": split_results,
        "bootstrap": bootstrap,
        "full_cagr": {
            str(weight): (1.0 + row["return"]) ** (1.0 / elapsed_years) - 1.0
            for weight, row in zip(WEIGHTS, blends, strict=True)
        },
        "limitations": [
            "The historical OOS interval was already visible during prior research.",
            "Component blend reduces but does not eliminate roughly 65%+ historical drawdown.",
            "Bootstrap is path sensitivity, not independent statistical significance.",
            "A 3x opening/effective cap does not model exchange outages or liquidation slippage.",
        ],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def pct(value):
    return f"{value:.2%}"


def markdown(payload):
    lines = [
        "# BTC 固定双组件混合（3X 上限）",
        "",
        "将日线 SMA 8/40 高收益组件与 4h 现货核心组件按固定初始资金比例混合。",
        "所有回放使用已完成信号、下一根 15m 开盘、10+5 bps 压力成本、Funding 和 3X 有效杠杆封顶。",
        "",
        "## 全样本与 OOS 结果",
        "",
        "| 日线组件权重 | Full收益 | Full CAGR | Full DD | OOS收益 | OOS超额 |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for weight, row in zip(WEIGHTS, payload["blends"], strict=True):
        oos = payload["split_results"][str(weight)]["oos"]
        lines.append(
            f"| {weight:.0%} | {pct(row['return'])} | {pct(payload['full_cagr'][str(weight)])} | "
            f"{pct(row['max_drawdown'])} | {pct(oos['return'])} | {pct(oos['excess_return'])} |"
        )
    lines += [
        "",
        "## 解读",
        "",
        "固定 25% 日线组件与 75% 现货核心时，Full 仍超过 B&H，同时回撤略低于单独日线组件；",
        (
            "50%/50% 混合进一步提高历史收益，但回撤接近 70%。"
            "这不是新的独立 Edge，两个组件都依赖趋势暴露。"
        ),
        "",
        "## 结论",
        "",
        "结果值得进入冻结后的前向观察，但不能据此批准实盘。尤其要等待 2026-09-03 之后的未见数据，",
        (
            "并监控有效杠杆、Funding 和盘中回撤。"
            "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。"
        ),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
