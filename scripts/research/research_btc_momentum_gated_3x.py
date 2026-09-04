#!/usr/bin/env python3
"""Research a causal price-momentum BTC strategy with a hard 3x leverage cap."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import (
    MAX_FUTURES_LEVERAGE,
    annualized_return,
    replay_segregated,
    utc_ms,
    years_between,
)
from research_btc_dynamic_exposure import benchmark
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import ResearchBar, funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_momentum_gated_3x/2026-09-02")
MACRO_PERIODS = (900, 1200, 1500)
MOMENTUM_PERIODS = (270, 360, 450)
BEAR_EXPOSURES = tuple(Decimal(value) for value in ("0", "0.5"))
BULL_EXPOSURES = tuple(Decimal(value) for value in ("2.5", "3"))
FUNDING_THRESHOLD = Decimal("0.0001")
STRESS_MAINTENANCE = Decimal("0.02")
BASE_MAINTENANCE = Decimal("0.004")


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
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": strategy_status(selected, benchmarks, yearly, elapsed),
        "protocol": {
            "signal": (
                "completed 4h close above macro SMA and positive trailing momentum; bearish "
                "state requires close below macro SMA and negative momentum"
            ),
            "macro_periods": MACRO_PERIODS,
            "momentum_periods": MOMENTUM_PERIODS,
            "bear_exposures": [str(value) for value in BEAR_EXPOSURES],
            "bull_exposures": [str(value) for value in BULL_EXPOSURES],
            "execution": "completed 4h signal, next 15m open",
            "funding": "bull exposure falls to 1x when last known funding exceeds 0.0001",
            "maximum_order_leverage": str(MAX_FUTURES_LEVERAGE),
            "effective_leverage_control": (
                "at every 15m open, reduce futures notional to 3x futures-wallet equity"
            ),
            "selection": (
                "research and validation must beat aligned B&H under stress; maximize the "
                "weaker annualized excess, with drawdown reported rather than used as a gate"
            ),
            "costs": "base 5+2 bps; stress 10+5 bps per changed notional",
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
            "strategy_stress_cagr": annualized_return(yearly["strategy_compound"], elapsed),
            "benchmark_cagr": annualized_return(yearly["benchmark_compound"], elapsed),
        },
        "limitations": [
            "No observations after 2026-09-02 UTC are included.",
            "Annual returns are not guaranteed to exceed B&H in every year.",
            "Intrabar leverage can exceed 3x between open controls during a fast price fall.",
        ],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_candidates(bars, funding):
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    macro_values = {period: simple_moving_average(aggregate, period) for period in MACRO_PERIODS}
    momentum_values = {period: trailing_momentum(aggregate, period) for period in MOMENTUM_PERIODS}
    candidates = []
    for macro_period in MACRO_PERIODS:
        for momentum_period in MOMENTUM_PERIODS:
            for bear_exposure in BEAR_EXPOSURES:
                for bull_exposure in BULL_EXPOSURES:
                    dense = momentum_state_targets(
                        aggregate,
                        macro_values[macro_period],
                        momentum_values[momentum_period],
                        bear_exposure,
                        bull_exposure,
                    )
                    regime = map_targets_to_source(len(bars), dense, ends)
                    targets = funding_aware_targets(
                        regime, funding, bull_exposure, FUNDING_THRESHOLD
                    )
                    candidates.append(
                        {
                            "id": (
                                f"4h-macro{macro_period}-momentum{momentum_period}-"
                                f"bear{bear_exposure}x-neutral1x-bull{bull_exposure}x-"
                                f"funding-le-{FUNDING_THRESHOLD}"
                            ),
                            "macro_period": macro_period,
                            "momentum_period": momentum_period,
                            "bear_exposure": str(bear_exposure),
                            "bull_exposure": str(bull_exposure),
                            "targets": targets,
                            "metrics": {},
                        }
                    )
    return candidates


def trailing_momentum(bars: list[ResearchBar], period: int):
    if period < 1:
        raise ValueError("momentum period must be positive")
    output = []
    for index, bar in enumerate(bars):
        output.append(
            None if index < period else bar.close / bars[index - period].close - Decimal("1")
        )
    return tuple(output)


def momentum_state_targets(
    bars: list[ResearchBar],
    macro_values,
    momentum_values,
    bear_exposure: Decimal,
    bull_exposure: Decimal,
):
    if len(bars) != len(macro_values) or len(bars) != len(momentum_values):
        raise ValueError("momentum inputs differ in length")
    output = []
    for bar, macro, momentum in zip(bars, macro_values, momentum_values, strict=True):
        bullish = macro is not None and momentum is not None and bar.close > macro and momentum > 0
        bearish = macro is not None and momentum is not None and bar.close < macro and momentum < 0
        output.append(bull_exposure if bullish else bear_exposure if bearish else Decimal("1"))
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
                maintenance_rate=BASE_MAINTENANCE,
                enforce_effective_leverage_cap=True,
            )
            stress = replay_segregated(
                bars,
                candidate["targets"],
                funding,
                start,
                end,
                spot_cap=Decimal("0"),
                maintenance_rate=STRESS_MAINTENANCE,
                fee_bps=Decimal("10"),
                slippage_bps=Decimal("5"),
                enforce_effective_leverage_cap=True,
            )
            candidate["metrics"][name] = {
                "base": asdict(base),
                "stress": asdict(stress),
            }


def select_development_candidate(candidates, benchmarks, splits):
    qualifying = []
    for candidate in candidates:
        research = candidate["metrics"]["research"]["stress"]
        validation = candidate["metrics"]["validation"]["stress"]
        if (
            not research["liquidated"]
            and not validation["liquidated"]
            and research["net_return"] > benchmarks["research"]["net_return"]
            and validation["net_return"] > benchmarks["validation"]["net_return"]
            and research["maximum_controlled_open_futures_leverage"] <= 3
            and validation["maximum_controlled_open_futures_leverage"] <= 3
        ):
            candidate["development_score"] = development_score(candidate, benchmarks, splits)
            qualifying.append(candidate)
    if not qualifying:
        raise RuntimeError("no momentum candidate passed development gates")
    qualifying.sort(
        key=lambda row: (row["development_score"], -Decimal(row["bull_exposure"])),
        reverse=True,
    )
    return qualifying[0], qualifying


def development_score(candidate, benchmarks, splits):
    excess = []
    for name in ("research", "validation"):
        start, end = splits[name]
        years = years_between(start, end)
        strategy = candidate["metrics"][name]["stress"]["net_return"]
        baseline = benchmarks[name]["net_return"]
        excess.append(annualized_return(strategy, years) - annualized_return(baseline, years))
    return min(excess)


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
            maintenance_rate=STRESS_MAINTENANCE,
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


def strategy_status(selected, benchmarks, yearly, elapsed):
    full = selected["metrics"]["full"]["stress"]
    oos = selected["metrics"]["oos"]["stress"]
    return (
        "FORWARD_OBSERVATION_CANDIDATE"
        if (
            not full["liquidated"]
            and not oos["liquidated"]
            and full["net_return"] > benchmarks["full"]["net_return"]
            and oos["net_return"] > benchmarks["oos"]["net_return"]
            and yearly["strategy_compound"] > yearly["benchmark_compound"]
            and annualized_return(yearly["strategy_compound"], elapsed) >= 0.20
        )
        else "RESEARCH_ONLY"
    )


def public_candidate(candidate):
    return {key: value for key, value in candidate.items() if key != "targets"}


def markdown(payload):
    selected = payload["selected"]
    yearly = payload["yearly"]
    annualized = payload["annualized_2022_latest"]
    lines = [
        "# BTC 价格动量 + 宏观门槛 3X 策略",
        "",
        "使用已完成4h收盘价的长期均线与 trailing momentum；牛市目标最高3X，"
        "每根15m开盘主动控制合约有效杠杆。",
        "",
        f"开发期选择：`{selected['id']}`；合格候选 {payload['qualifying_candidates']} 个。",
        "",
        "| 区间 | 策略压力收益 | B&H | 超额 | 策略DD | B&H DD | 下单杠杆 |",
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
            f"{result['maximum_controlled_open_futures_leverage']:.2f}X |"
        )
    lines += [
        "",
        "## 2022至今年度压力回放",
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
        f"复合收益：策略 {pct(yearly['strategy_compound'])}；"
        f"B&H {pct(yearly['benchmark_compound'])}。",
        f"年化：策略 {pct(annualized['strategy_stress_cagr'])}；"
        f"B&H {pct(annualized['benchmark_cagr'])}。",
        "",
        f"状态：**{payload['status']}**。统计审计与真实前向数据仍是必要条件。",
        "",
    ]
    return "\n".join(lines)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
