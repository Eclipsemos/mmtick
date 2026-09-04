#!/usr/bin/env python3
"""Causally reduce BTC SMA exposure after a completed-daily price drawdown."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap

OUTPUT = Path("reports/experiments/btc_stitched_strict15m_drawdown_guard/2026-09-03")
LOOKBACKS = (30, 60, 90, 180)
TRIGGERS = (Decimal("0.10"), Decimal("0.15"), Decimal("0.20"))
GUARDS = (Decimal("0.5"), Decimal("0.75"), Decimal("1"), Decimal("1.25"))
COSTS = (
    ("default", Decimal("10"), Decimal("5")),
    ("moderate", Decimal("20"), Decimal("10")),
    ("stress", Decimal("50"), Decimal("25")),
)


def main() -> None:
    spot, futures, daily, target_indices, funding = base.load_hybrid_inputs()
    bars = spot + futures
    bounds = base.periods(bars[-1].end_ms, spot[-1].end_ms)
    benchmarks = {name: base.benchmark(bars, *period) for name, period in bounds.items()}
    baseline_sparse = base.build_targets(
        daily, fast_period=11, enter_bear_days=2, active=Decimal("1.5")
    )
    baseline_targets = base.map_targets(len(bars), target_indices, baseline_sparse)
    baseline = {
        name: evaluate(bars, baseline_targets, funding, benchmarks[name], *bounds[name])
        for name in ("research", "validation")
    }
    candidates = []
    for lookback in LOOKBACKS:
        for trigger in TRIGGERS:
            for guard in GUARDS:
                sparse = guarded_targets(baseline_sparse, daily, lookback, trigger, guard)
                targets = base.map_targets(len(bars), target_indices, sparse)
                metrics = {}
                for name in ("research", "validation"):
                    metrics[name] = {}
                    for label, fee_bps, slippage_bps in COSTS[:2]:
                        metrics[name][label] = evaluate(
                            bars,
                            targets,
                            funding,
                            benchmarks[name],
                            *bounds[name],
                            fee_bps=fee_bps,
                            slippage_bps=slippage_bps,
                        )
                development_excess = [
                    metrics[name][cost]["excess"]
                    for name in ("research", "validation")
                    for cost, _fee, _slippage in COSTS[:2]
                ]
                qualifies = all(value > 0 for value in development_excess) and all(
                    metrics[name]["default"]["max_drawdown"] >= baseline[name]["max_drawdown"]
                    for name in ("research", "validation")
                )
                candidates.append(
                    {
                        "id": f"look{lookback}-dd{trigger}-guard{guard}x",
                        "lookback": lookback,
                        "trigger": str(trigger),
                        "guard": str(guard),
                        "targets": targets,
                        "metrics": metrics,
                        "development_worst_excess": min(development_excess),
                        "development_worst_drawdown": min(
                            metrics[name]["default"]["max_drawdown"]
                            for name in ("research", "validation")
                        ),
                        "qualifies": qualifies,
                    }
                )
    qualifying = [candidate for candidate in candidates if candidate["qualifies"]]
    return_selected = max(qualifying, key=lambda candidate: candidate["development_worst_excess"])
    risk_selected = max(qualifying, key=lambda candidate: candidate["development_worst_drawdown"])
    selected = {"return": return_selected, "risk": risk_selected}
    for candidate in set_by_identity(selected.values()):
        for name in ("research", "validation"):
            fee_bps, slippage_bps = COSTS[2][1:]
            candidate["metrics"][name]["stress"] = evaluate(
                bars,
                candidate["targets"],
                funding,
                benchmarks[name],
                *bounds[name],
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
        for name in ("oos", "full"):
            candidate["metrics"][name] = {}
            for label, fee_bps, slippage_bps in COSTS:
                candidate["metrics"][name][label] = evaluate(
                    bars,
                    candidate["targets"],
                    funding,
                    benchmarks[name],
                    *bounds[name],
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    record_equity=name == "full" and label == "default",
                )
        full_result = candidate["metrics"]["full"]["default"].pop("_result")
        candidate["bootstrap"] = bootstrap(full_result, bars, bounds)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "base": "SMA11/40, enter bear after 2 daily bars, active 1.5X, bear 0X",
            "guard": "completed daily close drawdown from the trailing completed-daily high",
            "selection": "Research/Validation default and moderate costs only; OOS excluded",
            "qualification": (
                "positive excess under both costs and no worse default DD "
                "in both development periods"
            ),
            "execution": "spot daily pre-2020; perpetual next 15m open from 2020",
            "capital": "50% spot and 50% isolated USD-M collateral; 2X futures opening cap",
        },
        "data": {"last": base.iso(bars[-1].end_ms), "candidate_count": len(candidates)},
        "baseline_development": baseline,
        "qualifying_count": len(qualifying),
        "return_selected": public(return_selected),
        "risk_selected": public(risk_selected),
        "top_candidates": [public(item) for item in ranked(qualifying)[:15]],
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def guarded_targets(baseline, daily, lookback, trigger, guard):
    output = []
    for index, (bar, target) in enumerate(zip(daily, baseline, strict=True)):
        if target is None or target == 0:
            output.append(target)
            continue
        high = max(item.close for item in daily[max(0, index - lookback + 1) : index + 1])
        output.append(guard if bar.close / high - 1 <= -trigger else target)
    return tuple(output)


def evaluate(
    bars,
    targets,
    funding,
    benchmark,
    start,
    end,
    *,
    fee_bps=Decimal("10"),
    slippage_bps=Decimal("5"),
    record_equity=False,
):
    result = base.replay(
        bars,
        targets,
        funding,
        start,
        end,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        record_equity=record_equity,
    )
    row = {
        "net_return": result.net_return,
        "benchmark_return": benchmark["net_return"],
        "excess": result.net_return - benchmark["net_return"],
        "max_drawdown": result.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
    }
    if record_equity:
        row["_result"] = result
    return row


def bootstrap(result, bars, bounds):
    strategy, benchmark = paired_daily_log_returns(
        bars, result.equity_curve, 100_000.0, start_ms=bounds["full"][0]
    )
    return {
        f"{block}d": run_bootstrap(
            strategy, benchmark, block_days=block, samples=10_000, seed=20263200 + block
        )
        for block in (7, 30, 90, 180, 365)
    }


def public(candidate):
    return {key: value for key, value in candidate.items() if key not in {"targets"}}


def ranked(candidates):
    return sorted(
        candidates, key=lambda candidate: candidate["development_worst_excess"], reverse=True
    )


def set_by_identity(candidates):
    output = []
    for candidate in candidates:
        if not any(candidate is item for item in output):
            output.append(candidate)
    return output


def render(payload):
    lines = [
        "# BTC Strict-15m SMA11 Drawdown Guard",
        "",
        "仅在原 SMA11/40 非熊市状态下，价格从已完成日线近期高点回撤后降低暴露。",
        "",
        (
            f"开发网格 {payload['data']['candidate_count']} 个，"
            f"合格 {payload['qualifying_count']} 个。"
        ),
    ]
    for label in ("return", "risk"):
        candidate = payload[f"{label}_selected"]
        lines += ["", f"## {label.title()} Candidate", "", f"`{candidate['id']}`", ""]
        lines += [
            "| 区间 | 默认超额 | 20+10 bps 超额 | 50+25 bps 超额 | 默认 DD |",
            "|---|---:|---:|---:|---:|",
        ]
        for name in ("research", "validation", "oos", "full"):
            metrics = candidate["metrics"][name]
            stress = metrics.get("stress")
            lines.append(
                f"| {name} | {metrics['default']['excess']:.2%} | "
                f"{metrics['moderate']['excess']:.2%} | "
                f"{stress['excess']:.2%} | {metrics['default']['max_drawdown']:.2%} |"
            )
        lines += ["", "Bootstrap:", ""]
        for block, value in candidate["bootstrap"].items():
            lines.append(
                f"- {block}: 超过 B&H {value['probability_beats_bh_return']:.2%}；"
                f"年化超额 P05 {value['annualized_excess_vs_bh']['p05']:.2%}。"
            )
    lines += ["", "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
