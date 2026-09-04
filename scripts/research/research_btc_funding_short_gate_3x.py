#!/usr/bin/env python3
"""Research a causal funding gate for the bearish side of BTC SMA 8/40."""

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

OUTPUT_DIR = Path("reports/experiments/btc_funding_short_gate_3x/2026-09-02")
SLOPE_LOOKBACKS = (0, 3, 5)
FUNDING_THRESHOLDS = tuple(
    Decimal(value) for value in ("-0.00005", "0", "0.00005", "0.0001", "0.00015", "0.0002")
)
FAST = 8
SLOW = 40
BULL_EXPOSURE = Decimal("1.5")
BEAR_EXPOSURE = Decimal("-0.1")
SPOT_CAP = Decimal("0.5")
MAX_LEVERAGE = Decimal("3")
MAINTENANCE = Decimal("0.02")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
HOLDOUT_START = 1571356800000
HOLDOUT_END = 1577836799999


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
        regime = build_regime(daily, fast, slow, lookback)
        source_regime = map_targets_to_source(len(bars), regime, ends)
        for threshold in FUNDING_THRESHOLDS:
            targets = funding_gate_targets(source_regime, funding, threshold)
            metrics = {}
            for name, (start, end) in splits.items():
                result = replay(bars, targets, funding, start, end)
                metrics[name] = {
                    **asdict(result),
                    "excess": result.net_return - benchmarks[name]["net_return"],
                }
                metrics[name].pop("equity_curve", None)
            holdout = replay(bars, targets, funding, HOLDOUT_START, HOLDOUT_END)
            holdout_bh = benchmark(bars, HOLDOUT_START, HOLDOUT_END)
            rows.append(
                {
                    "id": f"daily-sma8-40-slope{lookback}d-funding>{threshold}",
                    "slope_lookback_days": lookback,
                    "funding_threshold": str(threshold),
                    "metrics": metrics,
                    "holdout": {
                        **asdict(holdout),
                        "benchmark_return": holdout_bh["net_return"],
                    },
                }
            )
    rows.sort(key=development_score, reverse=True)
    selected = rows[0]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "selected": public(selected),
        "protocol": {
            "signal": (
                "daily SMA 8/40; bearish target -0.1x only when the latest known funding "
                "rate exceeds the selected threshold"
            ),
            "causality": "funding events at or before each 15m bar are used; no future rate",
            "execution": "next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "wallets": "50% spot and 50% USD-M collateral",
            "hard_leverage": "maximum observed open and intrabar-low futures leverage <=3x",
            "selection": "Research + Validation only; OOS and 2019 holdout excluded",
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
            "The OOS period is historical and visible; it is not a fresh holdout.",
            "The funding gate can change trade frequency and funding exposure materially.",
            "A positive historical result still requires fresh forward observation.",
        ],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (OUTPUT_DIR / "README.md").write_text(render(payload))
    print(OUTPUT_DIR / "README.md")


def build_regime(daily, fast, slow, lookback):
    regime = []
    for index, bar in enumerate(daily):
        if (
            fast[index] is None
            or slow[index] is None
            or index < lookback
            or (lookback and slow[index - lookback] is None)
        ):
            regime.append(None)
            continue
        bearish = (
            bar.close < slow[index]
            and fast[index] < slow[index]
            and (not lookback or slow[index] < slow[index - lookback])
        )
        regime.append(BEAR_EXPOSURE if bearish else BULL_EXPOSURE)
    return regime


def funding_gate_targets(regime_targets, funding, threshold):
    latest_rate = Decimal("0")
    previous = None
    output = []
    for index, regime in enumerate(regime_targets):
        for event in funding[index]:
            latest_rate = event.rate
        if regime is None:
            target = previous
        elif Decimal(regime) < 0 and latest_rate <= threshold:
            target = Decimal("0")
        else:
            target = Decimal(regime)
        output.append(target if target != previous else None)
        previous = target
    return tuple(output)


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
        "slope_lookback_days": row["slope_lookback_days"],
        "funding_threshold": row["funding_threshold"],
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
        "# BTC Funding-Gated Short Side (Hard 3X)",
        "",
        f"选择候选：`{payload['selected']['id']}`；选择只使用 Research 与 Validation。",
        "空头只在最新已公布 Funding 高于阈值时启用，否则目标为空仓。",
        "",
        (
            "| 斜率N | Funding阈值 | Research超额 | Validation超额 | OOS超额 | "
            "Full CAGR | Full DD | 2019留出超额 | 最高盘中杠杆 |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    years = payload["data"]["evaluation_years"]
    for row in payload["candidates"]:
        full, hold = row["full"], row["holdout_2019"]
        cagr = (1 + full["net_return"]) ** (1 / years) - 1
        obs = max(
            row[name]["maximum_observed_futures_leverage"]
            for name in ("research", "validation", "oos", "full")
        )
        lines.append(
            f"| {row['slope_lookback_days']} | {row['funding_threshold']} | "
            f"{pct(row['research']['excess'])} | {pct(row['validation']['excess'])} | "
            f"{pct(row['oos']['excess'])} | {pct(cagr)} | {pct(full['max_drawdown'])} | "
            f"{pct(hold['net_return'] - hold['benchmark_return'])} | {obs:.3f}X |"
        )
    lines += [
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "Bootstrap、滚动窗口和冻结后新鲜数据仍是必要条件。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
