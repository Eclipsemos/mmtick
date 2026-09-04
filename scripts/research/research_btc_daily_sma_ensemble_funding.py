#!/usr/bin/env python3
"""Research a fixed daily-SMA ensemble with a causal funding-risk gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import (
    annualized_return,
    replay_segregated,
    years_between,
)
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_ensemble_funding/2026-09-02")
COMPONENTS = ((7, 35, Decimal("-0.1")), (8, 40, Decimal("-0.1")), (12, 40, Decimal("0")))
BULL = Decimal("1.5")
SPOT_CAP = Decimal("0.5")
OPEN_CAP = Decimal("2.5")
EFFECTIVE_CAP = 3.0
THRESHOLDS = (None, Decimal("0.0001"), Decimal("0.00015"), Decimal("0.0002"), Decimal("0.0003"))
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
FREEZE_MS = int(datetime(2026, 9, 2, 8, tzinfo=UTC).timestamp() * 1000)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    daily, ends = aggregate_complete_periods(bars, "1d")
    base_dense = ensemble_dense(daily)
    base_targets = map_targets_to_source(len(bars), base_dense, ends)

    candidates = []
    for threshold in THRESHOLDS:
        targets = apply_funding_gate(base_targets, funding, threshold)
        metrics = {
            name: evaluate(bars, targets, funding, *bounds) for name, bounds in splits.items()
        }
        score = min(
            annualized_excess(metrics[name], benchmarks[name], splits[name])
            for name in ("research", "validation")
        )
        candidates.append(
            {
                "id": "no-gate" if threshold is None else f"funding>{threshold}",
                "threshold": None if threshold is None else str(threshold),
                "targets": targets,
                "metrics": metrics,
                "development_score": score,
                "development_qualifies": qualifies(metrics, benchmarks),
            }
        )
    qualifying = [item for item in candidates if item["development_qualifies"]]
    qualifying.sort(key=lambda item: item["development_score"], reverse=True)
    if not qualifying:
        raise RuntimeError("no funding-gated ensemble passed development gates")
    selected = qualifying[0]
    selected["metrics"]["full"] = evaluate(
        bars,
        selected["targets"],
        funding,
        *splits["full"],
        record_equity=True,
    )
    full = selected["metrics"]["full"]
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full["equity_curve"], 100_000.0, start_ms=splits["full"][0]
    )
    elapsed = years_between(*splits["full"])
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20261100 + block,
        )
        for block in (7, 30, 90)
    }
    metrics = selected["metrics"]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "base_strategy": "fixed equal-weight SMA7/35, SMA8/40, SMA12/40 ensemble",
            "funding_gate": "when last known funding > threshold, target >1 is reduced to 1",
            "selection": "threshold chosen only by Research/Validation annualized excess",
            "signal": "completed UTC daily bar; next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "open_cap": str(OPEN_CAP),
            "effective_cap": "<=3x including intrabar-low audit",
        },
        "data": {
            "bars": len(bars),
            "daily_bars": len(daily),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
            "evaluation_start": iso(splits["full"][0]),
            "evaluation_end": iso(splits["full"][1]),
        },
        "benchmarks": benchmarks,
        "candidate_count": len(candidates),
        "development_qualifying_count": len(qualifying),
        "selected": public(selected, benchmarks, elapsed),
        "development_ranking": [public(item, benchmarks, None) for item in qualifying],
        "bootstrap": bootstrap,
        "rolling": evaluate_rolling(bars, selected["targets"], funding, *splits["full"]),
        "yearly": evaluate_yearly(bars, selected["targets"], funding, *splits["full"]),
        "forward_observation": forward_observation(bars, selected["targets"], funding, FREEZE_MS),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def ensemble_dense(daily):
    streams = []
    for fast, slow, bear in COMPONENTS:
        fast_values = simple_moving_average(daily, fast)
        slow_values = simple_moving_average(daily, slow)
        streams.append(
            tuple(
                None
                if fast_values[i] is None or slow_values[i] is None
                else bear
                if bar.close < slow_values[i] and fast_values[i] < slow_values[i]
                else BULL
                for i, bar in enumerate(daily)
            )
        )
    return tuple(
        None if any(value is None for value in values) else sum(values, Decimal("0")) / 3
        for values in zip(*streams, strict=True)
    )


def apply_funding_gate(targets, funding, threshold):
    state = Decimal("0")
    latest = Decimal("0")
    output = []
    for target, events in zip(targets, funding, strict=True):
        if target is not None:
            state = Decimal(target)
        for event in events:
            latest = event.rate
        output.append(
            Decimal("1") if threshold is not None and state > 1 and latest > threshold else state
        )
    return tuple(output)


def evaluate(bars, targets, funding, start, end, *, record_equity=False):
    result = replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=SPOT_CAP,
        maintenance_rate=Decimal("0.02"),
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=OPEN_CAP,
    )
    output = {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
        "liquidated": result.liquidated,
        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
    }
    if record_equity:
        output["equity_curve"] = result.equity_curve
    return output


def qualifies(metrics, benchmarks):
    return all(
        not metrics[name]["liquidated"]
        and metrics[name]["net_return"] > benchmarks[name]["net_return"]
        and metrics[name]["maximum_intrabar_leverage"] <= EFFECTIVE_CAP + 1e-9
        for name in ("research", "validation")
    )


def annualized_excess(metrics, baseline, bounds):
    years = years_between(*bounds)
    return annualized_return(metrics["net_return"], years) - annualized_return(
        baseline["net_return"], years
    )


def public(candidate, benchmarks, elapsed):
    out = {
        "id": candidate["id"],
        "threshold": candidate["threshold"],
        "development_score": candidate["development_score"],
        "development_qualifies": candidate["development_qualifies"],
        "metrics": {},
    }
    for name, row in candidate["metrics"].items():
        if name == "full" and "equity_curve" in row:
            row = {key: value for key, value in row.items() if key != "equity_curve"}
        item = dict(row)
        if benchmarks is not None and name in benchmarks:
            item["benchmark_return"] = benchmarks[name]["net_return"]
            item["excess"] = row["net_return"] - benchmarks[name]["net_return"]
        if elapsed is not None and name == "full":
            item["cagr"] = annualized_return(row["net_return"], elapsed)
        out["metrics"][name] = item
    return out


def evaluate_rolling(bars, targets, funding, start_ms, end_ms):
    first = datetime.fromtimestamp(start_ms / 1000, UTC)
    last = datetime.fromtimestamp(end_ms / 1000, UTC)
    output = {}
    for label, days in (("1y", 365), ("2y", 730), ("3y", 1095)):
        rows = []
        cursor = first
        while cursor + timedelta(days=days) <= last:
            stop = cursor + timedelta(days=days) - timedelta(milliseconds=1)
            left, right = int(cursor.timestamp() * 1000), int(stop.timestamp() * 1000)
            result = evaluate(bars, targets, funding, left, right)
            baseline = benchmark(bars, left, right)
            rows.append(
                {
                    "excess": result["net_return"] - baseline["net_return"],
                    "beats_return": result["net_return"] > baseline["net_return"],
                    "beats_return_and_drawdown": result["net_return"] > baseline["net_return"]
                    and result["max_drawdown"] >= baseline["max_drawdown"],
                }
            )
            cursor += timedelta(days=30)
        output[label] = {
            "windows": len(rows),
            "return_win_rate": ratio(row["beats_return"] for row in rows),
            "return_and_drawdown_win_rate": ratio(row["beats_return_and_drawdown"] for row in rows),
            "median_excess": median([row["excess"] for row in rows]),
            "worst_excess": min((row["excess"] for row in rows), default=None),
        }
    return output


def evaluate_yearly(bars, targets, funding, start_ms, end_ms):
    first_year = datetime.fromtimestamp(start_ms / 1000, UTC).year
    last_year = datetime.fromtimestamp(end_ms / 1000, UTC).year
    rows = []
    for year in range(first_year, last_year + 1):
        left = max(start_ms, int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000))
        right = min(end_ms, int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1)
        result = evaluate(bars, targets, funding, left, right)
        baseline = benchmark(bars, left, right)
        rows.append(
            {
                "year": year,
                "strategy_return": result["net_return"],
                "benchmark_return": baseline["net_return"],
                "excess": result["net_return"] - baseline["net_return"],
            }
        )
    return rows


def forward_observation(bars, targets, funding, freeze_ms):
    observed = [bar for bar in bars if bar.start_ms >= freeze_ms]
    if not observed:
        return {"status": "AWAITING_FORWARD_DATA", "bars": 0}
    start, end = observed[0].start_ms, observed[-1].start_ms
    result = evaluate(bars, targets, funding, start, end)
    baseline = benchmark(bars, start, end)
    return {
        "status": "FORWARD_OBSERVATION",
        "bars": len(observed),
        "strategy_return": result["net_return"],
        "benchmark_return": baseline["net_return"],
        "excess": result["net_return"] - baseline["net_return"],
        "maximum_intrabar_leverage": result["maximum_intrabar_leverage"],
    }


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def ratio(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def render(payload):
    selected = payload["selected"]
    lines = [
        "# BTC Daily SMA Ensemble + Funding Gate — Strict 3X",
        "",
        (
            f"开发期选择：`{selected['id']}`；合格候选 "
            f"{payload['development_qualifying_count']} / {payload['candidate_count']}。"
        ),
        "Funding 过滤只使用当时已公布的最近费率；额外暴露在高 Funding 时降为 1X。",
        "",
        "| 区间 | 策略 | B&H | 超额 | DD | 盘中最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = selected["metrics"][name]
        lines.append(
            f"| {name} | {pct(row['net_return'])} | {pct(row['benchmark_return'])} | "
            f"{pct(row['excess'])} | {pct(row['max_drawdown'])} | "
            f"{row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        f"Full CAGR：{pct(selected['metrics']['full']['cagr'])}；"
        f"B&H CAGR：{pct(benchmark_cagr(payload))}。",
        "",
        "## Threshold ranking",
        "",
    ]
    for item in payload["development_ranking"]:
        lines.append(
            f"- `{item['id']}`：development score {pct(item['development_score'])}；"
            f"Research/Validation 均通过 {item['development_qualifies']}。"
        )
    lines += ["", "## Bootstrap", ""]
    for block, row in payload["bootstrap"].items():
        lines.append(
            f"- {block}: 超过 B&H {row['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += ["", "## Rolling windows", ""]
    for label, row in payload["rolling"].items():
        lines.append(
            f"- {label}: 超过 B&H {row['return_win_rate']:.2%}；"
            f"收益与 DD 同胜 {row['return_and_drawdown_win_rate']:.2%}；"
            f"最差超额 {pct(row['worst_excess'])}。"
        )
    lines += ["", "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。", ""]
    return "\n".join(lines)


def period_bounds(payload):
    first = datetime.fromisoformat(payload["data"]["evaluation_start"])
    last = datetime.fromisoformat(payload["data"]["evaluation_end"])
    return int(first.timestamp() * 1000), int(last.timestamp() * 1000)


def benchmark_cagr(payload):
    return annualized_return(
        payload["benchmarks"]["full"]["net_return"],
        years_between(*period_bounds(payload)),
    )


if __name__ == "__main__":
    main()
