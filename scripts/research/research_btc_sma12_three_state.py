#!/usr/bin/env python3
"""Research causal daily SMA12/40 bull, neutral, and bear exposures for BTC."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import annualized_return, replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_sma12_three_state/2026-09-03")
NEUTRAL_EXPOSURES = (Decimal("0.75"), Decimal("1"), Decimal("1.25"))
BULL_EXPOSURES = (Decimal("1.25"), Decimal("1.5"), Decimal("1.75"))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}

    candidates = []
    for neutral in NEUTRAL_EXPOSURES:
        for bull in BULL_EXPOSURES:
            if bull < neutral:
                continue
            targets = map_targets_to_source(
                len(bars), build_dense_targets(daily, neutral, bull), ends
            )
            metrics = {
                name: evaluate(bars, targets, funding, *splits[name])
                for name in ("research", "validation")
            }
            qualifies = all(
                metrics[name]["net_return"] > benchmarks[name]["net_return"]
                and metrics[name]["maximum_intrabar_leverage"] <= 3
                and not metrics[name]["liquidated"]
                for name in ("research", "validation")
            )
            score = min(
                annualized_excess(metrics[name], benchmarks[name], splits[name])
                for name in ("research", "validation")
            )
            candidates.append(
                {
                    "id": f"neutral{neutral}x-bull{bull}x",
                    "neutral_exposure": str(neutral),
                    "bull_exposure": str(bull),
                    "development_score": score,
                    "development_qualifies": qualifies,
                    "metrics": metrics,
                    "targets": targets,
                }
            )

    qualifying = sorted(
        (item for item in candidates if item["development_qualifies"]),
        key=lambda item: item["development_score"],
        reverse=True,
    )
    selected = (
        qualifying[0] if qualifying else max(candidates, key=lambda item: item["development_score"])
    )
    risk_selected = (
        max(
            qualifying,
            key=lambda item: min(
                item["metrics"][name]["max_drawdown"] for name in ("research", "validation")
            ),
        )
        if qualifying
        else selected
    )

    selected_bootstrap = complete_and_bootstrap(selected, bars, funding, splits, seed=20260930)
    risk_bootstrap = (
        selected_bootstrap
        if risk_selected is selected
        else complete_and_bootstrap(risk_selected, bars, funding, splits, seed=20261030)
    )
    baseline_targets = map_targets_to_source(len(bars), build_baseline_targets(daily), ends)
    baseline = {
        name: evaluate(bars, baseline_targets, funding, *splits[name]) for name in ("oos", "full")
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": ("RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED" if qualifying else "REJECTED"),
        "protocol": {
            "states": (
                "bull when close>SMA40 and SMA12>SMA40; bear when both are below; otherwise neutral"
            ),
            "selection": "2020-2024 only; 2025+ excluded from ranking",
            "qualification": "beat B&H in both development splits and obey hard 3X cap",
            "execution": "completed UTC daily signal; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "leverage": "2.5X futures opening cap and <=3X intrabar effective leverage",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "candidate_count": len(candidates),
        "development_qualifying_count": len(qualifying),
        "selected": public_candidate(selected, benchmarks, splits),
        "risk_selected": public_candidate(risk_selected, benchmarks, splits),
        "baseline": baseline,
        "selected_bootstrap": selected_bootstrap,
        "risk_bootstrap": risk_bootstrap,
        "development_ranking": [public_candidate(item, benchmarks, splits) for item in qualifying],
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def build_dense_targets(
    daily,
    neutral_exposure,
    bull_exposure,
    *,
    fast_period=12,
    slow_period=40,
):
    if not 0 < fast_period < slow_period:
        raise ValueError("SMA periods must be positive and fast must be below slow")
    fast = simple_moving_average(daily, fast_period)
    slow = simple_moving_average(daily, slow_period)
    output = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            output.append(None)
        elif bar.close > slow[index] and fast[index] > slow[index]:
            output.append(bull_exposure)
        elif bar.close < slow[index] and fast[index] < slow[index]:
            output.append(Decimal("0"))
        else:
            output.append(neutral_exposure)
    return tuple(output)


def build_baseline_targets(daily):
    fast = simple_moving_average(daily, 12)
    slow = simple_moving_average(daily, 40)
    return tuple(
        None
        if fast[index] is None or slow[index] is None
        else Decimal("0")
        if bar.close < slow[index] and fast[index] < slow[index]
        else Decimal("1.5")
        for index, bar in enumerate(daily)
    )


def evaluate(bars, targets, funding, start, end, *, record_equity=False):
    result = replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("2.5"),
    )
    output = {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }
    if record_equity:
        output["_result"] = result
    return output


def complete_and_bootstrap(candidate, bars, funding, splits, *, seed):
    candidate["metrics"].update(
        {
            name: evaluate(
                bars,
                candidate["targets"],
                funding,
                *splits[name],
                record_equity=name == "full",
            )
            for name in ("oos", "full")
        }
    )
    result = candidate["metrics"]["full"].pop("_result")
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, result.equity_curve, 100_000.0, start_ms=splits["full"][0]
    )
    return {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=seed + block,
        )
        for block in (7, 30, 90)
    }


def annualized_excess(metrics, baseline, bounds):
    years = years_between(*bounds)
    return annualized_return(metrics["net_return"], years) - annualized_return(
        baseline["net_return"], years
    )


def public_candidate(candidate, benchmarks, splits):
    output = {
        key: candidate[key]
        for key in (
            "id",
            "neutral_exposure",
            "bull_exposure",
            "development_score",
            "development_qualifies",
        )
    }
    output["metrics"] = {}
    for name, metrics in candidate["metrics"].items():
        row = {key: value for key, value in metrics.items() if key != "_result"}
        row["benchmark_return"] = benchmarks[name]["net_return"]
        row["excess"] = row["net_return"] - benchmarks[name]["net_return"]
        row["cagr"] = annualized_return(row["net_return"], years_between(*splits[name]))
        output["metrics"][name] = row
    return output


def render(payload):
    lines = [
        "# BTC Daily SMA12/40 Three-State Exposure",
        "",
        "熊市空仓，中性阶段降低暴露，明确牛市才提高暴露；OOS 不参与选参。",
        "",
        (
            f"开发候选 {payload['candidate_count']} 个，"
            f"合格 {payload['development_qualifying_count']} 个。"
        ),
    ]
    for title, key, bootstrap_key in (
        ("Return Candidate", "selected", "selected_bootstrap"),
        ("Risk Candidate", "risk_selected", "risk_bootstrap"),
    ):
        candidate = payload[key]
        lines += [
            "",
            f"## {title}",
            "",
            f"`{candidate['id']}`",
            "",
            "| 区间 | 策略 | B&H | 超额 | CAGR | DD | 最高杠杆 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for name in ("research", "validation", "oos", "full"):
            row = candidate["metrics"][name]
            lines.append(
                f"| {name} | {row['net_return']:.2%} | {row['benchmark_return']:.2%} | "
                f"{row['excess']:.2%} | {row['cagr']:.2%} | {row['max_drawdown']:.2%} | "
                f"{row['maximum_intrabar_leverage']:.3f}X |"
            )
        lines += ["", "Bootstrap:", ""]
        for label, row in payload[bootstrap_key].items():
            lines.append(
                f"- {label}: 超过 B&H {row['probability_beats_bh_return']:.2%}；"
                f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
            )
    lines += [
        "",
        f"状态：**{payload['status']}**。",
        "",
    ]
    return "\n".join(lines)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
