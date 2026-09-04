#!/usr/bin/env python3
"""Research volatility-scaled leverage for the BTC macro-gated 3x strategy."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import (
    annualized_return,
    replay_segregated,
    utc_ms,
    years_between,
)
from research_btc_dynamic_exposure import benchmark
from research_btc_macro_gated_3x import (
    macro_gated_targets,
    select_development_candidate,
)
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import ResearchBar, funding_by_bar
from mastermind_tick.models import FundingRate
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_macro_vol_scaled_3x/2026-09-02")
PERIODS = (24, 48, 96, 192)
MACRO_PERIOD = 1200
BEAR_EXPOSURE = Decimal("0.5")
MAX_BULL_EXPOSURE = Decimal("3")
FUNDING_THRESHOLD = Decimal("0.0001")
VOLATILITY_LOOKBACKS = (180, 360, 720)
TARGET_VOLATILITIES = tuple(Decimal(value) for value in ("0.8", "1", "1.2", "1.5", "2"))
ANNUALIZATION_PERIODS = Decimal(6 * 365)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, start, end) for name, (start, end) in splits.items()}
    candidates = build_candidates(bars, funding)
    evaluate_splits(candidates, bars, funding, splits, ("research", "validation"))
    selected, qualifying = select_development_candidate(candidates, benchmarks, splits)
    evaluate_splits(candidates, bars, funding, splits, ("oos", "full"))
    yearly = evaluate_years(bars, funding, selected["targets"])
    elapsed = years_between(utc_ms(2022, 1, 1), bars[-1].end_ms)
    cagr = annualized_return(yearly["strategy_compound"], elapsed)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": status(selected, benchmarks, yearly, cagr),
        "protocol": {
            "base_candidate": ("4h 24/48/96/192, macro1200, bear0.5x, neutral1x, bull up to3x"),
            "volatility": ("closed 4h simple-return standard deviation, annualized by sqrt(6*365)"),
            "bull_exposure": "clip(target volatility / realized volatility, 1x, 3x)",
            "funding": "dynamic exposure falls to 1x above last known funding 0.0001",
            "selection": (
                "research and validation both beat B&H under stress; research drawdown no "
                "worse than B&H; maximize weaker annualized excess"
            ),
            "costs": "10 bps fee + 5 bps slippage in selection and reported stress path",
            "execution": "completed 4h inputs, target active at next 15m open",
            "leverage_control": (
                "if open effective futures leverage exceeds 3x after losses, reduce it to 3x "
                "at that open and charge trading costs"
            ),
        },
        "data": {
            "warmup_start": iso(bars[0].start_ms),
            "evaluation_start": iso(splits["full"][0]),
            "last": iso(bars[-1].end_ms),
            "bars": len(bars),
        },
        "benchmarks": benchmarks,
        "qualifying_candidates": len(qualifying),
        "selected": public_candidate(selected),
        "development_ranking": [public_candidate(row) for row in qualifying],
        "all_candidates": [public_candidate(row) for row in candidates],
        "yearly": yearly,
        "annualized_2022_latest": {
            "years": elapsed,
            "strategy_stress_cagr": cagr,
            "benchmark_cagr": annualized_return(yearly["benchmark_compound"], elapsed),
        },
        "limitations": [
            "No untouched observations exist after 2026-09-02 UTC.",
            "Volatility scaling changes only on completed 4h bars and cannot cap intrabar gaps.",
            "Annual excess remains negative in some calendar years.",
        ],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_candidates(bars, funding):
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    raw = three_state_targets(aggregate, PERIODS, BEAR_EXPOSURE, MAX_BULL_EXPOSURE)
    macro = simple_moving_average(aggregate, MACRO_PERIOD)
    macro_gated = macro_gated_targets(aggregate, raw, macro, MAX_BULL_EXPOSURE)
    candidates = []
    for lookback in VOLATILITY_LOOKBACKS:
        volatility = rolling_annualized_volatility(aggregate, lookback)
        for target_volatility in TARGET_VOLATILITIES:
            dynamic = volatility_scaled_targets(
                macro_gated,
                volatility,
                target_volatility,
                MAX_BULL_EXPOSURE,
            )
            mapped = map_targets_to_source(len(bars), dynamic, ends)
            targets = variable_funding_cap_targets(mapped, funding, FUNDING_THRESHOLD)
            candidates.append(
                {
                    "id": (
                        f"4h-24-48-96-192-macro1200-vol{lookback}-target{target_volatility}-max3x"
                    ),
                    "periods": PERIODS,
                    "macro_period": MACRO_PERIOD,
                    "volatility_lookback": lookback,
                    "target_volatility": str(target_volatility),
                    "bull_exposure": str(MAX_BULL_EXPOSURE),
                    "targets": targets,
                    "metrics": {},
                }
            )
    return candidates


def rolling_annualized_volatility(
    bars: list[ResearchBar], lookback: int
) -> tuple[Decimal | None, ...]:
    if lookback < 2:
        raise ValueError("volatility lookback must be at least two")
    returns: deque[Decimal] = deque()
    total = Decimal("0")
    total_squared = Decimal("0")
    previous_close = None
    output = []
    annualizer = ANNUALIZATION_PERIODS.sqrt()
    for bar in bars:
        value = (
            Decimal("0") if previous_close is None else bar.close / previous_close - Decimal("1")
        )
        previous_close = bar.close
        returns.append(value)
        total += value
        total_squared += value * value
        if len(returns) > lookback:
            removed = returns.popleft()
            total -= removed
            total_squared -= removed * removed
        if len(returns) < lookback:
            output.append(None)
            continue
        count = Decimal(lookback)
        mean = total / count
        variance = max(Decimal("0"), total_squared / count - mean * mean)
        output.append(variance.sqrt() * annualizer)
    return tuple(output)


def volatility_scaled_targets(
    macro_targets,
    volatility,
    target_volatility: Decimal,
    maximum_exposure: Decimal,
):
    if len(macro_targets) != len(volatility):
        raise ValueError("target and volatility lengths differ")
    if target_volatility <= 0:
        raise ValueError("target volatility must be positive")
    output = []
    for target, realized in zip(macro_targets, volatility, strict=True):
        if target != maximum_exposure:
            output.append(target)
        elif realized is None or realized <= 0:
            output.append(Decimal("1"))
        else:
            output.append(
                max(
                    Decimal("1"),
                    min(maximum_exposure, target_volatility / realized),
                )
            )
    return tuple(output)


def variable_funding_cap_targets(
    mapped_targets,
    funding: list[list[FundingRate]],
    threshold: Decimal,
):
    if len(mapped_targets) != len(funding):
        raise ValueError("target and funding lengths differ")
    state = Decimal("1")
    latest_rate = Decimal("0")
    previous_target = None
    output = []
    for index, signal in enumerate(mapped_targets):
        if signal is not None:
            state = Decimal(signal)
        for event in funding[index]:
            latest_rate = event.rate
        target = Decimal("1") if state > 1 and latest_rate > threshold else state
        output.append(target if target != previous_target else None)
        previous_target = target
    return tuple(output)


def evaluate_splits(candidates, bars, funding, splits, names):
    for candidate in candidates:
        for name in names:
            start, end = splits[name]
            base = replay_segregated(
                bars,
                candidate["targets"],
                funding,
                start,
                end,
                spot_cap=Decimal("0"),
                maintenance_rate=Decimal("0.004"),
                enforce_effective_leverage_cap=True,
            )
            stress = replay_segregated(
                bars,
                candidate["targets"],
                funding,
                start,
                end,
                spot_cap=Decimal("0"),
                maintenance_rate=Decimal("0.02"),
                fee_bps=Decimal("10"),
                slippage_bps=Decimal("5"),
                enforce_effective_leverage_cap=True,
            )
            candidate["metrics"][name] = {
                "stress": asdict(stress),
                "base": asdict(base),
            }


def evaluate_years(bars, funding, targets):
    last_year = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).year
    strategy_equity = Decimal("1")
    benchmark_equity = Decimal("1")
    rows = []
    for year in range(2022, last_year + 1):
        start = utc_ms(year, 1, 1)
        end = min(utc_ms(year + 1, 1, 1) - 1, bars[-1].end_ms)
        result = replay_segregated(
            bars,
            targets,
            funding,
            start,
            end,
            spot_cap=Decimal("0"),
            maintenance_rate=Decimal("0.02"),
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            enforce_effective_leverage_cap=True,
        )
        baseline = benchmark(bars, start, end)
        strategy_equity *= Decimal(str(1 + result.net_return))
        benchmark_equity *= Decimal(str(1 + baseline["net_return"]))
        rows.append(
            {
                "year": year,
                "strategy": asdict(result),
                "buy_and_hold": baseline,
                "excess_return": result.net_return - baseline["net_return"],
            }
        )
    return {
        "years": rows,
        "strategy_compound": float(strategy_equity - Decimal("1")),
        "benchmark_compound": float(benchmark_equity - Decimal("1")),
    }


def status(selected, benchmarks, yearly, cagr):
    full = selected["metrics"]["full"]["stress"]
    oos = selected["metrics"]["oos"]["stress"]
    passed = (
        not full["liquidated"]
        and full["net_return"] > benchmarks["full"]["net_return"]
        and full["max_drawdown"] >= benchmarks["full"]["max_drawdown"]
        and oos["net_return"] > benchmarks["oos"]["net_return"]
        and yearly["strategy_compound"] > yearly["benchmark_compound"]
        and cagr >= 0.20
    )
    return "STATISTICAL_AUDIT_REQUIRED" if passed else "RESEARCH_ONLY"


def public_candidate(candidate):
    return {key: value for key, value in candidate.items() if key != "targets"}


def markdown(payload):
    selected = payload["selected"]
    yearly = payload["yearly"]
    annualized = payload["annualized_2022_latest"]
    lines = [
        "# BTC 宏观门槛 + 波动率缩放 3X",
        "",
        "在宏观与短趋势均为多头时，按已完成4h实现波动率动态缩放1X至3X。",
        "",
        f"开发期选择：`{selected['id']}`；合格候选 {payload['qualifying_candidates']} 个。",
        "",
        "| 区间 | 策略压力收益 | B&H | 超额 | 策略DD | B&H DD | 最大下单杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        result = selected["metrics"][name]["stress"]
        baseline = payload["benchmarks"][name]
        lines.append(
            f"| {name} | {pct(result['net_return'])} | "
            f"{pct(baseline['net_return'])} | "
            f"{pct(result['net_return'] - baseline['net_return'])} | "
            f"{pct(result['max_drawdown'])} | {pct(baseline['max_drawdown'])} | "
            f"{result['maximum_futures_leverage']:.2f}X |"
        )
    lines += [
        "",
        "## 2022至今",
        "",
        "| 年份 | 策略 | B&H | 超额 | 策略DD |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in yearly["years"]:
        lines.append(
            f"| {row['year']} | {pct(row['strategy']['net_return'])} | "
            f"{pct(row['buy_and_hold']['net_return'])} | "
            f"{pct(row['excess_return'])} | {pct(row['strategy']['max_drawdown'])} |"
        )
    lines += [
        "",
        f"压力复合收益：{pct(yearly['strategy_compound'])}；"
        f"B&H：{pct(yearly['benchmark_compound'])}。",
        f"压力年化：{pct(annualized['strategy_stress_cagr'])}；"
        f"B&H：{pct(annualized['benchmark_cagr'])}。",
        "",
        f"状态：**{payload['status']}**。下一步必须通过冻结参数统计审计，"
        "不能仅凭开发/OOS汇总批准。",
        "",
    ]
    return "\n".join(lines)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
