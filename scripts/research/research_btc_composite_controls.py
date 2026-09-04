#!/usr/bin/env python3
"""Audit a pre-registered combination of BTC trend, drawdown, and funding controls."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from audit_btc_sma11_levered_benchmark import constant_targets
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap

OUTPUT = Path("reports/experiments/btc_composite_controls/2026-09-03")
STRESS_COST = (Decimal("50"), Decimal("25"))

# These are existing, previously reported settings.  They are evaluated as a
# fixed comparison set; no OOS result is used to select among them.
CANDIDATES = (
    {
        "id": "baseline-sma10-40-hysteresis",
        "lookback": None,
        "drawdown_trigger": None,
        "guard_exposure": None,
        "funding_threshold": None,
    },
    {
        "id": "funding01",
        "lookback": None,
        "drawdown_trigger": None,
        "guard_exposure": None,
        "funding_threshold": Decimal("0.0001"),
    },
    {
        "id": "drawdown-look90-dd15-guard1",
        "lookback": 90,
        "drawdown_trigger": Decimal("0.15"),
        "guard_exposure": Decimal("1"),
        "funding_threshold": None,
    },
    {
        "id": "drawdown-look90-dd15-guard1-funding01",
        "lookback": 90,
        "drawdown_trigger": Decimal("0.15"),
        "guard_exposure": Decimal("1"),
        "funding_threshold": Decimal("0.0001"),
    },
    {
        "id": "drawdown-look180-dd20-guard075-funding01",
        "lookback": 180,
        "drawdown_trigger": Decimal("0.20"),
        "guard_exposure": Decimal("0.75"),
        "funding_threshold": Decimal("0.0001"),
    },
)


def main() -> None:
    spot, futures, daily, target_indices, funding = base.load_hybrid_inputs()
    bars = spot + futures
    bounds = base.periods(bars[-1].end_ms, spot[-1].end_ms)
    benchmarks = {name: base.benchmark(bars, *period) for name, period in bounds.items()}
    matched_targets = constant_targets(len(bars), Decimal("1.5"))
    matched = {
        name: metric(
            base.replay(bars, matched_targets, funding, *period),
            benchmarks[name],
            *period,
        )
        for name, period in bounds.items()
    }
    baseline_sparse = base.build_targets(
        daily,
        fast_period=10,
        slow_period=40,
        enter_bear_days=2,
        exit_bear_days=1,
        active=Decimal("1.5"),
    )

    rows = []
    for specification in CANDIDATES:
        sparse = apply_drawdown_control(baseline_sparse, daily, specification)
        targets = base.map_targets(len(bars), target_indices, sparse)
        targets = apply_funding_control(targets, funding, specification["funding_threshold"])
        metrics = {}
        full_result = None
        for name, period in bounds.items():
            result = base.replay(
                bars,
                targets,
                funding,
                *period,
                record_equity=name == "full",
            )
            metrics[name] = metric(result, benchmarks[name], *period)
            if name == "full":
                full_result = result
        if full_result is None:
            raise RuntimeError("full replay did not produce an equity curve")
        strategy_logs, benchmark_logs = paired_daily_log_returns(
            bars,
            full_result.equity_curve,
            100_000.0,
            start_ms=bounds["full"][0],
        )
        bootstrap = None
        if specification["id"] in {
            "baseline-sma10-40-hysteresis",
            "drawdown-look90-dd15-guard1-funding01",
        }:
            bootstrap = run_bootstrap(
                strategy_logs,
                benchmark_logs,
                block_days=90,
                samples=10_000,
                seed=20260903,
            )
        stress = stress_full(bars, targets, funding, bounds["full"], benchmarks["full"])
        rows.append(
            {
                "candidate": serialize_specification(specification),
                "metrics": metrics,
                "matched_1p5x_excess": {
                    name: metrics[name]["strategy_return"] - matched[name]["strategy_return"]
                    for name in bounds
                },
                "stress_full": stress,
                "bootstrap_90d": bootstrap,
            }
        )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / COMPOSITE_CONTROL_AUDIT",
        "protocol": {
            "signal": "completed UTC daily SMA10/40; bear after 2 days, recover after 1",
            "execution": "spot pre-2020 and perpetual next 15m open from 2020",
            "capital": "50% spot and 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage per side; historical Funding",
            "hard_cap": "2X futures opening control; observed effective leverage <=3X",
            "selection": "fixed comparison set from prior studies; OOS not used for selection",
            "funding_causality": "latest funding event known at each execution bar only",
        },
        "data": {
            "spot_daily_bars": len(spot),
            "perpetual_15m_bars": len(futures),
            "signal_daily_bars": len(daily),
            "first": base.iso(bars[0].start_ms),
            "last": base.iso(bars[-1].end_ms),
        },
        "benchmarks": benchmarks,
        "matched_1p5x": matched,
        "results": rows,
        "decision": decision(rows),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def apply_drawdown_control(baseline, daily, specification):
    lookback = specification["lookback"]
    trigger = specification["drawdown_trigger"]
    guard = specification["guard_exposure"]
    if lookback is None:
        return tuple(baseline)
    if trigger is None or guard is None:
        raise ValueError("drawdown settings must be complete")
    output = []
    for index, (bar, target) in enumerate(zip(daily, baseline, strict=True)):
        if target is None or target == 0:
            output.append(target)
            continue
        high = max(item.close for item in daily[max(0, index - lookback + 1) : index + 1])
        output.append(guard if bar.close / high - 1 <= -trigger else target)
    return tuple(output)


def apply_funding_control(targets, funding, threshold):
    if len(targets) != len(funding):
        raise ValueError("target and funding streams must have equal lengths")
    if threshold is None:
        return tuple(targets)
    active = None
    latest = Decimal("0")
    output = []
    for target, events in zip(targets, funding, strict=True):
        if target is not None:
            active = Decimal(target)
        for event in events:
            latest = event.rate
        if active is None:
            output.append(None)
        elif active > 1 and latest > threshold:
            output.append(Decimal("1"))
        else:
            output.append(active)
    return tuple(output)


def metric(result, benchmark, start, end):
    return {
        "strategy_return": result.net_return,
        "benchmark_return": benchmark["net_return"],
        "excess": result.net_return - benchmark["net_return"],
        "strategy_cagr": (1 + result.net_return) ** (1 / base.years_between(start, end)) - 1,
        "strategy_drawdown": result.max_drawdown,
        "benchmark_drawdown": benchmark["max_drawdown"],
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def stress_full(bars, targets, funding, period, benchmark):
    result = base.replay(
        bars,
        targets,
        funding,
        *period,
        fee_bps=STRESS_COST[0],
        slippage_bps=STRESS_COST[1],
    )
    return metric(result, benchmark, *period)


def serialize_specification(specification):
    return {
        key: (str(value) if isinstance(value, Decimal) else value)
        for key, value in specification.items()
    }


def decision(rows):
    combined = next(
        row for row in rows if row["candidate"]["id"] == "drawdown-look90-dd15-guard1-funding01"
    )
    full = combined["metrics"]["full"]
    return {
        "combined_beats_bh_all_splits": all(
            combined["metrics"][name]["excess"] > 0
            for name in ("spot_pre2020", "research", "validation", "oos", "full")
        ),
        "combined_hard_3x_passed": all(
            combined["metrics"][name]["maximum_intrabar_leverage"] <= 3
            and not combined["metrics"][name]["liquidated"]
            for name in ("spot_pre2020", "research", "validation", "oos", "full")
        ),
        "combined_beats_matched_1p5x_all_splits": all(
            combined["matched_1p5x_excess"][name] > 0
            for name in ("spot_pre2020", "research", "validation", "oos", "full")
        ),
        "combined_validation_vs_matched_1p5x": combined["matched_1p5x_excess"]["validation"],
        "combined_full_excess": full["excess"],
        "combined_vs_matched_1p5x": combined["matched_1p5x_excess"],
        "combined_full_drawdown": full["strategy_drawdown"],
        "combined_bootstrap_90d_excess_p05": combined["bootstrap_90d"]["annualized_excess_vs_bh"][
            "p05"
        ],
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
    }


def render(payload):
    lines = [
        "# BTC Composite Trend, Drawdown, and Funding Controls",
        "",
        "固定比较 SMA10/40 迟滞、Funding 限制、回撤降仓及两种组合；不使用 OOS 选参。",
        "",
        "| Candidate | Research超额 | Validation超额 | OOS超额 | Full CAGR | Full DD | "
        "杠杆 | V vs 1.5X | 90d P05 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        metrics = row["metrics"]
        full = metrics["full"]
        lines.append(
            f"| `{row['candidate']['id']}` | {metrics['research']['excess']:.2%} | "
            f"{metrics['validation']['excess']:.2%} | {metrics['oos']['excess']:.2%} | "
            f"{full['strategy_cagr']:.2%} | {full['strategy_drawdown']:.2%} | "
            f"{full['maximum_intrabar_leverage']:.3f}X | "
            f"{row['matched_1p5x_excess']['validation']:.2%} | "
            f"{p05(row['bootstrap_90d'])} |"
        )
    lines += [
        "",
        "## Stress cost (full sample)",
        "",
        "| Candidate | 50+25bps Full超额 | Full DD | 杠杆 |",
        "|---|---:|---:|---:|",
    ]
    for row in payload["results"]:
        stress = row["stress_full"]
        lines.append(
            f"| `{row['candidate']['id']}` | {stress['excess']:.2%} | "
            f"{stress['strategy_drawdown']:.2%} | {stress['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "组合结论：",
        (
            f"- 组合是否在全部区间超过 B&H："
            f"{'是' if payload['decision']['combined_beats_bh_all_splits'] else '否'}。"
        ),
        (
            f"- 组合是否满足严格 3X："
            f"{'是' if payload['decision']['combined_hard_3x_passed'] else '否'}。"
        ),
        (
            f"- 组合 Full 超额：{payload['decision']['combined_full_excess']:.2%}；"
            f"最大回撤：{payload['decision']['combined_full_drawdown']:.2%}。"
        ),
        (
            f"- 组合相对连续 1.5X 基准的 Validation 超额："
            f"{payload['decision']['combined_validation_vs_matched_1p5x']:.2%}。"
        ),
        (
            f"- 组合 90 日 bootstrap 年化超额 P05："
            f"{payload['decision']['combined_bootstrap_90d_excess_p05']:.2%}。"
        ),
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


def p05(bootstrap):
    if bootstrap is None:
        return "-"
    return f"{bootstrap['annualized_excess_vs_bh']['p05']:.2%}"


if __name__ == "__main__":
    main()
