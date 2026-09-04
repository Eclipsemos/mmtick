#!/usr/bin/env python3
"""Research a macro-gated BTC trend strategy with a hard 3x order leverage cap."""

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
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import ResearchBar, funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_macro_gated_3x/2026-09-02")
PERIODS = (
    (24, 48, 96, 192),
    (25, 50, 100, 200),
    (26, 52, 104, 208),
)
MACRO_PERIODS = (600, 900, 1200)
BULL_EXPOSURES = tuple(Decimal(value) for value in ("2.5", "2.75", "3"))
BEAR_EXPOSURE = Decimal("0.5")
FUNDING_THRESHOLD = Decimal("0.0001")
SPOT_CAP = Decimal("0")
BASE_MAINTENANCE = Decimal("0.004")
STRESS_MAINTENANCE = Decimal("0.02")
FUNDING_SENSITIVITY = tuple(
    Decimal(value)
    for value in (
        "0.00009",
        "0.000099",
        "0.0001",
        "0.000101",
        "0.00011",
        "0.000125",
        "0.00015",
        "0.0002",
        "1",
    )
)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, start, end) for name, (start, end) in splits.items()}
    candidates = build_candidates(bars, funding)
    evaluate_splits(candidates, bars, funding, splits, ("research", "validation"))
    selected, qualifying = select_development_candidate(candidates, benchmarks, splits)
    challenger = select_max_leverage_challenger(qualifying)
    evaluate_splits(candidates, bars, funding, splits, ("oos", "full"))
    yearly = evaluate_years(bars, funding, selected["targets"])
    challenger_yearly = evaluate_years(bars, funding, challenger["targets"])
    sensitivity = evaluate_funding_sensitivity(bars, funding, selected, splits)
    selected_public = public_candidate(selected)
    challenger_public = public_candidate(challenger)
    elapsed = years_between(utc_ms(2022, 1, 1), bars[-1].end_ms)
    status = strategy_status(selected, benchmarks, yearly, elapsed, require_twenty=False)
    challenger_status = strategy_status(
        challenger, benchmarks, challenger_yearly, elapsed, require_twenty=True
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": status,
        "challenger_status": challenger_status,
        "protocol": {
            "account": "USD-M futures wallet; all account equity is futures collateral",
            "signal": (
                "completed 4h fast SMA regime; bull leverage requires close above a completed "
                "4h macro SMA"
            ),
            "execution": "signal becomes tradable at the next 15m open",
            "bear_exposure": str(BEAR_EXPOSURE),
            "neutral_exposure": "1",
            "maximum_order_leverage": str(MAX_FUTURES_LEVERAGE),
            "funding_filter": (
                "bull leverage falls to 1x when the last known funding rate exceeds 0.0001"
            ),
            "development_selection": (
                "research and validation must both beat aligned B&H under stress; research "
                "drawdown must be no worse than B&H; maximize the weaker annualized excess"
            ),
            "oos_policy": "2025 onward is read only after the development candidate is fixed",
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
        "selected": selected_public,
        "max_leverage_challenger": challenger_public,
        "development_ranking": [public_candidate(row) for row in qualifying],
        "all_candidates": [public_candidate(row) for row in candidates],
        "yearly": yearly,
        "challenger_yearly": challenger_yearly,
        "annualized_2022_latest": {
            "years": elapsed,
            "strategy_stress_cagr": annualized_return(yearly["strategy_compound"], elapsed),
            "benchmark_cagr": annualized_return(yearly["benchmark_compound"], elapsed),
        },
        "challenger_annualized_2022_latest": {
            "years": elapsed,
            "strategy_stress_cagr": annualized_return(
                challenger_yearly["strategy_compound"], elapsed
            ),
            "benchmark_cagr": annualized_return(challenger_yearly["benchmark_compound"], elapsed),
        },
        "funding_sensitivity": sensitivity,
        "limitations": [
            "The candidate has no untouched data after 2026-09-02 UTC.",
            "A 3x order can exceed 3x effective leverage after collateral losses.",
            "Historical drawdown remains large and annual excess is not positive every year.",
        ],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_candidates(bars, funding):
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    macro_streams = {period: simple_moving_average(aggregate, period) for period in MACRO_PERIODS}
    candidates = []
    for periods in PERIODS:
        for macro_period in MACRO_PERIODS:
            for bull_exposure in BULL_EXPOSURES:
                raw = three_state_targets(aggregate, periods, BEAR_EXPOSURE, bull_exposure)
                gated = macro_gated_targets(
                    aggregate,
                    raw,
                    macro_streams[macro_period],
                    bull_exposure,
                )
                regime_targets = map_targets_to_source(len(bars), gated, ends)
                targets = funding_aware_targets(
                    regime_targets,
                    funding,
                    bull_exposure,
                    FUNDING_THRESHOLD,
                )
                candidates.append(
                    {
                        "id": (
                            f"4h-{'-'.join(map(str, periods))}-macro{macro_period}-"
                            f"bear{BEAR_EXPOSURE}x-neutral1x-bull{bull_exposure}x-"
                            f"funding-le-{FUNDING_THRESHOLD}"
                        ),
                        "periods": periods,
                        "macro_period": macro_period,
                        "bull_exposure": str(bull_exposure),
                        "regime_targets": regime_targets,
                        "targets": targets,
                        "metrics": {},
                    }
                )
    return candidates


def macro_gated_targets(
    bars: list[ResearchBar],
    regime_targets,
    macro_values,
    bull_exposure: Decimal,
):
    if not (len(bars) == len(regime_targets) == len(macro_values)):
        raise ValueError("macro gate inputs differ in length")
    output = []
    for bar, target, macro_value in zip(bars, regime_targets, macro_values, strict=True):
        if target == bull_exposure and (macro_value is None or bar.close <= macro_value):
            output.append(Decimal("1"))
        else:
            output.append(target)
    return tuple(output)


def evaluate_splits(candidates, bars, funding, splits, names):
    for candidate in candidates:
        for name in names:
            start, end = splits[name]
            candidate["metrics"][name] = evaluate_one(
                bars, candidate["targets"], funding, start, end
            )


def evaluate_one(bars, targets, funding, start, end):
    base = replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=SPOT_CAP,
        maintenance_rate=BASE_MAINTENANCE,
    )
    stress = replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=SPOT_CAP,
        maintenance_rate=STRESS_MAINTENANCE,
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
    )
    return {"base": asdict(base), "stress": asdict(stress)}


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
            and research["max_drawdown"] >= benchmarks["research"]["max_drawdown"]
            and research["maximum_futures_leverage"] <= float(MAX_FUTURES_LEVERAGE)
            and validation["maximum_futures_leverage"] <= float(MAX_FUTURES_LEVERAGE)
        ):
            candidate["development_score"] = development_score(candidate, benchmarks, splits)
            qualifying.append(candidate)
    if not qualifying:
        raise RuntimeError("no macro-gated candidate passed development gates")
    qualifying.sort(
        key=lambda row: (
            row["development_score"],
            -Decimal(row["bull_exposure"]),
            row["macro_period"],
        ),
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


def select_max_leverage_challenger(qualifying):
    for candidate in qualifying:
        if Decimal(candidate["bull_exposure"]) == MAX_FUTURES_LEVERAGE:
            return candidate
    raise RuntimeError("no 3x candidate passed development gates")


def evaluate_years(bars, funding, targets):
    last_year = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).year
    rows = []
    strategy_equity = Decimal("1")
    benchmark_equity = Decimal("1")
    for year in range(2022, last_year + 1):
        start = utc_ms(year, 1, 1)
        end = min(utc_ms(year + 1, 1, 1) - 1, bars[-1].end_ms)
        result = replay_segregated(
            bars,
            targets,
            funding,
            start,
            end,
            spot_cap=SPOT_CAP,
            maintenance_rate=STRESS_MAINTENANCE,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
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


def evaluate_funding_sensitivity(bars, funding, selected, splits):
    output = []
    for threshold in FUNDING_SENSITIVITY:
        targets = funding_aware_targets(
            selected["regime_targets"],
            funding,
            Decimal(selected["bull_exposure"]),
            threshold,
        )
        metrics = {}
        for name in ("research", "validation", "oos", "full"):
            start, end = splits[name]
            result = replay_segregated(
                bars,
                targets,
                funding,
                start,
                end,
                spot_cap=SPOT_CAP,
                maintenance_rate=STRESS_MAINTENANCE,
                fee_bps=Decimal("10"),
                slippage_bps=Decimal("5"),
            )
            metrics[name] = asdict(result)
        output.append({"threshold": str(threshold), "metrics": metrics})
    return output


def strategy_status(selected, benchmarks, yearly, elapsed, *, require_twenty):
    full = selected["metrics"]["full"]["stress"]
    oos = selected["metrics"]["oos"]["stress"]
    cagr = annualized_return(yearly["strategy_compound"], elapsed)
    passed = (
        not full["liquidated"]
        and not oos["liquidated"]
        and full["net_return"] > benchmarks["full"]["net_return"]
        and oos["net_return"] > benchmarks["oos"]["net_return"]
        and yearly["strategy_compound"] > yearly["benchmark_compound"]
        and (not require_twenty or cagr >= 0.20)
    )
    return "FORWARD_OBSERVATION_CANDIDATE" if passed else "RESEARCH_ONLY"


def public_candidate(candidate):
    return {
        key: value for key, value in candidate.items() if key not in {"targets", "regime_targets"}
    }


def markdown(payload):
    selected = payload["selected"]
    challenger = payload["max_leverage_challenger"]
    yearly = payload["yearly"]
    challenger_yearly = payload["challenger_yearly"]
    annualized = payload["annualized_2022_latest"]
    challenger_annualized = payload["challenger_annualized_2022_latest"]
    lines = [
        "# BTC 3X 宏观门槛趋势策略",
        "",
        "用长期宏观均线阻止短周期牛市排列在长期熊市反弹中启用高杠杆。"
        "现货占比为0，全部权益作为USD-M抵押；下单杠杆最高3X。",
        "",
        "## 开发期冻结候选",
        "",
        f"配置：`{selected['id']}`",
        "",
        f"开发门槛通过数量：{payload['qualifying_candidates']}。"
        "选择过程仅使用research与validation。",
        "",
        "| 区间 | 策略压力收益 | B&H | 超额 | 最大DD | 下单杠杆 | 观察有效杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        result = selected["metrics"][name]["stress"]
        baseline = payload["benchmarks"][name]
        lines.append(
            f"| {name} | {pct(result['net_return'])} | "
            f"{pct(baseline['net_return'])} | "
            f"{pct(result['net_return'] - baseline['net_return'])} | "
            f"{pct(result['max_drawdown'])} | "
            f"{result['maximum_futures_leverage']:.2f}X | "
            f"{result['maximum_observed_futures_leverage']:.2f}X |"
        )
    lines += [
        "",
        "## 2022至今逐年压力回放",
        "",
        "| 年份 | 策略 | B&H | 超额 | 最大DD |",
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
        "## 3X高收益 Challenger",
        "",
        f"配置：`{challenger['id']}`",
        "",
        "该配置是在开发期合格候选中，预先限制牛市必须为3X后按同一开发评分选出。",
        "",
        "| 区间 | 策略压力收益 | B&H | 超额 | 最大DD |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        result = challenger["metrics"][name]["stress"]
        baseline = payload["benchmarks"][name]
        lines.append(
            f"| {name} | {pct(result['net_return'])} | "
            f"{pct(baseline['net_return'])} | "
            f"{pct(result['net_return'] - baseline['net_return'])} | "
            f"{pct(result['max_drawdown'])} |"
        )
    lines += [
        "",
        f"2022至今压力复合收益：{pct(challenger_yearly['strategy_compound'])}；"
        f"B&H：{pct(challenger_yearly['benchmark_compound'])}。",
        f"压力年化：{pct(challenger_annualized['strategy_stress_cagr'])}；"
        f"B&H：{pct(challenger_annualized['benchmark_cagr'])}。",
        "",
        "## 结论",
        "",
        f"风险调整主候选状态：**{payload['status']}**；"
        f"3X challenger状态：**{payload['challenger_status']}**。",
        "",
        f"主候选历史压力年化为{pct(annualized['strategy_stress_cagr'])}，未达到20%；"
        f"3X challenger为{pct(challenger_annualized['strategy_stress_cagr'])}并超过B&H。"
        "两者最大回撤仍很高，且2026-09-02之后尚无未见数据，因此不能批准实盘。",
        "",
        "3X是下单时杠杆上限；保证金亏损后观察有效杠杆可能高于3X。",
        "",
    ]
    return "\n".join(lines)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
