#!/usr/bin/env python3
"""Research a causal price-drawdown guard for the BTC SMA12/40 candidate."""

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

OUTPUT = Path("reports/experiments/btc_sma12_drawdown_guard/2026-09-03")
LOOKBACKS = (30, 60, 90)
TRIGGERS = (Decimal("0.10"), Decimal("0.15"), Decimal("0.20"))
GUARDED_EXPOSURES = (Decimal("0.75"), Decimal("1.0"), Decimal("1.25"))
ACTIVE_EXPOSURE = Decimal("1.5")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    daily, ends = aggregate_complete_periods(bars, "1d")
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}

    baseline_targets = map_targets_to_source(
        len(bars), build_dense_targets(daily, None, None, None), ends
    )
    baseline = {
        name: evaluate(bars, baseline_targets, funding, *splits[name])
        for name in ("research", "validation")
    }

    candidates = []
    for lookback in LOOKBACKS:
        for trigger in TRIGGERS:
            for guarded_exposure in GUARDED_EXPOSURES:
                targets = map_targets_to_source(
                    len(bars),
                    build_dense_targets(daily, lookback, trigger, guarded_exposure),
                    ends,
                )
                metrics = {
                    name: evaluate(bars, targets, funding, *splits[name])
                    for name in ("research", "validation")
                }
                qualifies = all(
                    metrics[name]["net_return"] > benchmarks[name]["net_return"]
                    and metrics[name]["max_drawdown"] >= baseline[name]["max_drawdown"]
                    and metrics[name]["maximum_intrabar_leverage"] <= 3
                    and not metrics[name]["liquidated"]
                    for name in ("research", "validation")
                )
                score = min(
                    annualized_return(metrics[name]["net_return"], years_between(*splits[name]))
                    - annualized_return(
                        benchmarks[name]["net_return"], years_between(*splits[name])
                    )
                    for name in ("research", "validation")
                )
                candidates.append(
                    {
                        "id": f"look{lookback}-dd{trigger}-guard{guarded_exposure}x",
                        "lookback": lookback,
                        "trigger": str(trigger),
                        "guarded_exposure": str(guarded_exposure),
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
    selected["metrics"].update(
        {
            name: evaluate(
                bars,
                selected["targets"],
                funding,
                *splits[name],
                record_equity=name == "full",
            )
            for name in ("oos", "full")
        }
    )
    full_result = selected["metrics"]["full"].pop("_result")
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full_result.equity_curve, 100_000.0, start_ms=splits["full"][0]
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20260903 + block,
        )
        for block in (7, 30, 90)
    }
    if risk_selected is selected:
        risk_bootstrap = bootstrap
    else:
        risk_selected["metrics"].update(
            {
                name: evaluate(
                    bars,
                    risk_selected["targets"],
                    funding,
                    *splits[name],
                    record_equity=name == "full",
                )
                for name in ("oos", "full")
            }
        )
        risk_full_result = risk_selected["metrics"]["full"].pop("_result")
        risk_logs, risk_benchmark_logs = paired_daily_log_returns(
            bars,
            risk_full_result.equity_curve,
            100_000.0,
            start_ms=splits["full"][0],
        )
        risk_bootstrap = {
            f"{block}d": run_bootstrap(
                risk_logs,
                risk_benchmark_logs,
                block_days=block,
                samples=10_000,
                seed=20261003 + block,
            )
            for block in (7, 30, 90)
        }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": (
            "REJECTED" if not qualifying else "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED"
        ),
        "protocol": {
            "base": "daily SMA12/40; active 1.5X; bear-flat 0X",
            "guard": "completed daily close drawdown from trailing completed-daily high",
            "selection": "ranked only on 2020-2024 research and validation; OOS excluded",
            "qualification": (
                "beat B&H and do not worsen baseline drawdown in both development splits"
            ),
            "execution": "completed UTC daily signal; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "leverage": "2.5X futures opening cap and <=3X intrabar effective leverage",
        },
        "data": {
            "bars": len(bars),
            "daily_bars": len(daily),
            "last": iso(bars[-1].end_ms),
        },
        "candidate_count": len(candidates),
        "development_qualifying_count": len(qualifying),
        "baseline_development": baseline,
        "selected": public_candidate(selected, benchmarks, splits),
        "risk_selected": public_candidate(risk_selected, benchmarks, splits),
        "top_development": [public_candidate(item, benchmarks, splits) for item in qualifying[:10]],
        "bootstrap": bootstrap,
        "risk_bootstrap": risk_bootstrap,
        "decision": (
            "保护层仅作为低回撤 Challenger 进入冻结前向观察，不替换 SMA12/40 基线"
            if qualifying
            else "没有保护层同时改善开发期回撤并跑赢 B&H"
        ),
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def build_dense_targets(daily, lookback, trigger, guarded_exposure):
    fast = simple_moving_average(daily, 12)
    slow = simple_moving_average(daily, 40)
    output = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            output.append(None)
            continue
        if bar.close < slow[index] and fast[index] < slow[index]:
            output.append(Decimal("0"))
            continue
        if lookback is None:
            output.append(ACTIVE_EXPOSURE)
            continue
        trailing_high = max(item.close for item in daily[max(0, index - lookback + 1) : index + 1])
        drawdown = bar.close / trailing_high - Decimal("1")
        output.append(guarded_exposure if drawdown <= -trigger else ACTIVE_EXPOSURE)
    return tuple(output)


def evaluate(bars, targets, funding, start, end, *, record_equity=False):
    result = replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
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


def public_candidate(candidate, benchmarks, splits):
    output = {
        key: candidate[key]
        for key in (
            "id",
            "lookback",
            "trigger",
            "guarded_exposure",
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
    selected = payload["selected"]
    risk_selected = payload["risk_selected"]
    lines = [
        "# BTC SMA12/40 Drawdown Guard",
        "",
        "只使用完成日线相对过去高点的回撤调整暴露；2025–最新不参与参数选择。",
        "",
        (
            f"开发候选 {payload['candidate_count']} 个，"
            f"合格 {payload['development_qualifying_count']} 个。"
        ),
        f"选择：`{selected['id']}`。",
        "",
        "| 区间 | 策略 | B&H | 超额 | DD | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = selected["metrics"][name]
        lines.append(
            f"| {name} | {row['net_return']:.2%} | {row['benchmark_return']:.2%} | "
            f"{row['excess']:.2%} | {row['max_drawdown']:.2%} | "
            f"{row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += ["", "## Bootstrap", ""]
    for label, row in payload["bootstrap"].items():
        lines.append(
            f"- {label}: 超过 B&H {row['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += [
        "",
        "## Risk-Adjusted Challenger",
        "",
        (f"仅根据开发期最小化最坏回撤选择：`{risk_selected['id']}`。"),
        "",
        "| 区间 | 策略 | B&H | 超额 | DD |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = risk_selected["metrics"][name]
        lines.append(
            f"| {name} | {row['net_return']:.2%} | {row['benchmark_return']:.2%} | "
            f"{row['excess']:.2%} | {row['max_drawdown']:.2%} |"
        )
    lines += ["", "Risk-adjusted Bootstrap:", ""]
    for label, row in payload["risk_bootstrap"].items():
        lines.append(
            f"- {label}: 超过 B&H {row['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += ["", f"结论：{payload['decision']}。", f"状态：**{payload['status']}**。", ""]
    return "\n".join(lines)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
