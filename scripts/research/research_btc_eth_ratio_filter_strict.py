#!/usr/bin/env python3
"""Test an ETH/BTC relative-strength filter on the strict BTC SMA challenger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_sma10_three_state_hysteresis_strict import split_periods
from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_eth_ratio_filter_strict/2026-09-03")
COSTS = (
    ("default", Decimal("10"), Decimal("5")),
    ("moderate", Decimal("20"), Decimal("10")),
    ("stress", Decimal("50"), Decimal("25")),
)
RATIO_PERIODS = ((10, 40), (20, 60), (30, 90))
REDUCED_TARGETS = (Decimal("0"), Decimal("0.75"), Decimal("1"))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    btc = load_market("BTCUSDT")
    eth = load_market("ETHUSDT")
    btc_funding = funding_by_bar(btc, load_funding("BTCUSDT", btc))
    btc_daily, btc_ends = aggregate_complete_periods(btc, "1d")
    eth_daily, _ = aggregate_complete_periods(eth, "1d")
    eth_by_end = {bar.end_ms: bar for bar in eth_daily}
    splits = split_periods(btc)
    benchmarks = {name: benchmark(btc, *bounds) for name, bounds in splits.items()}
    base = btc_hysteresis(btc_daily, 10, 40, 3, 1, Decimal("1.55"))
    rows = []
    for fast, slow in RATIO_PERIODS:
        ratio_dates = [bar for bar in btc_daily if bar.end_ms in eth_by_end]
        ratio = tuple(b.close / eth_by_end[b.end_ms].close for b in ratio_dates)
        ratio_state = ratio_hysteresis(ratio, fast, slow)
        ratio_state_by_end = {
            bar.end_ms: state for bar, state in zip(ratio_dates, ratio_state, strict=True)
        }
        for reduced in REDUCED_TARGETS:
            filtered = tuple(
                None
                if target is None
                else Decimal("0")
                if target == 0 or ratio_state_by_end.get(btc_daily[index].end_ms) == "bear"
                else reduced
                for index, target in enumerate(base)
            )
            targets = map_targets_to_source(len(btc), filtered, btc_ends)
            metrics = {}
            for name, bounds in splits.items():
                metrics[name] = {}
                for label, fee, slippage in COSTS:
                    result = replay_segregated(
                        btc,
                        targets,
                        btc_funding,
                        *bounds,
                        spot_cap=Decimal("0.5"),
                        maintenance_rate=Decimal("0.02"),
                        fee_bps=fee,
                        slippage_bps=slippage,
                        enforce_effective_leverage_cap=True,
                        maximum_futures_leverage=Decimal("2.5"),
                    )
                    metrics[name][label] = public(result, benchmarks[name], bounds)
            rows.append(
                {
                    "id": f"ratio-sma{fast}/{slow}-reduce{reduced}x",
                    "ratio_fast": fast,
                    "ratio_slow": slow,
                    "reduced_target": str(reduced),
                    "metrics": metrics,
                    "development_worst_excess": min(
                        metrics[name][cost]["excess"]
                        for name in ("research", "validation")
                        for cost in ("default", "moderate", "stress")
                    ),
                }
            )
    rows.sort(key=lambda row: row["development_worst_excess"], reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "base": "BTC SMA10/40 hysteresis enter3/exit1, active 1.55X, bear 0X",
            "filter": "ETH/BTC ratio bearish when ratio close < slow SMA and fast SMA < slow SMA",
            "execution": "completed daily bars; next BTC 15m open",
            "costs": "default 10+5, moderate 20+10, stress 50+25 bps per side",
            "wallets": "50% BTC spot and 50% isolated BTC USD-M collateral",
            "hard_cap": "2.5X opening control and <=3X observed effective leverage",
            "selection": "worst Research/Validation excess across costs; OOS excluded",
        },
        "data": {
            "btc_bars": len(btc),
            "eth_bars": len(eth),
            "daily_bars": len(btc_daily),
            "btc_last": iso(btc[-1].end_ms),
            "eth_last": iso(eth[-1].end_ms),
        },
        "benchmarks": benchmarks,
        "results": rows,
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def btc_hysteresis(
    daily, fast_period: int, slow_period: int, enter: int, exit_days: int, active: Decimal
):
    fast = simple_moving_average(daily, fast_period)
    slow = simple_moving_average(daily, slow_period)
    state = None
    bear_count = recovery_count = 0
    output = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            output.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= enter:
            state = "bear"
        elif state == "bear" and recovery_count >= exit_days:
            state = "active"
        output.append(Decimal("0") if state == "bear" else active)
    return tuple(output)


def ratio_hysteresis(ratio, fast_period: int, slow_period: int):
    ratio_bars = tuple(type("Bar", (), {"close": value})() for value in ratio)
    fast = simple_moving_average(ratio_bars, fast_period)
    slow = simple_moving_average(ratio_bars, slow_period)
    return tuple(
        "unknown"
        if fast[index] is None or slow[index] is None
        else "bear"
        if ratio[index] < slow[index] and fast[index] < slow[index]
        else "active"
        for index in range(len(ratio))
    )


def public(result, baseline, bounds):
    return {
        "net_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "cagr": (1 + result.net_return) ** (1 / years_between(*bounds)) - 1,
        "max_drawdown": result.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "rebalances": result.rebalances,
    }


def render(payload):
    lines = [
        "# BTC ETH/BTC Relative-Strength Filter (Strict 3X)",
        "",
        "在 BTC SMA10/40 1.55X 候选上，ETH/BTC 走弱时降低主动暴露。",
        "",
        "| 配置 | 开发最差 | Research压力 | Validation压力 | OOS默认 | Full默认 | DD | 杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        m = row["metrics"]
        full = m["full"]["default"]
        lines.append(
            f"| `{row['id']}` | {row['development_worst_excess']:.2%} | "
            f"{m['research']['stress']['excess']:.2%} | "
            f"{m['validation']['stress']['excess']:.2%} | "
            f"{m['oos']['default']['excess']:.2%} | {full['excess']:.2%} | "
            f"{full['max_drawdown']:.2%} | {full['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += ["", "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。", ""]
    return "\n".join(lines)


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
