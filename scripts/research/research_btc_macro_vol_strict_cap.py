#!/usr/bin/env python3
"""Select and audit the macro/volatility BTC family under a strict 3x effective cap."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import annualized_return, replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_macro_vol_scaled_3x import build_candidates
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar

OUTPUT_DIR = Path("reports/experiments/btc_macro_vol_strict_cap/2026-09-02")
OPEN_CAP = Decimal("2.5")
EFFECTIVE_CAP = 3.0
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
MAINTENANCE = Decimal("0.02")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    candidates = build_candidates(bars, funding)
    for candidate in candidates:
        candidate["source_id"] = candidate["id"]
        candidate["id"] = candidate["id"].replace("-max3x", "-max2.5x-strict3x")
        candidate["targets"] = clamp_targets(candidate["targets"], OPEN_CAP)
        candidate["strict_cap"] = str(OPEN_CAP)
        candidate["metrics"] = {}
        for name in ("research", "validation"):
            candidate["metrics"][name] = evaluate(
                bars, candidate["targets"], funding, *splits[name]
            )
        candidate["development_score"] = min(
            annualized_excess(candidate["metrics"][name], benchmarks[name], splits[name])
            for name in ("research", "validation")
        )
        candidate["development_cap_passed"] = all(
            candidate["metrics"][name]["maximum_observed_futures_leverage"] <= EFFECTIVE_CAP + 1e-9
            for name in ("research", "validation")
        )
        candidate["development_qualifies"] = (
            candidate["development_cap_passed"]
            and all(
                not candidate["metrics"][name]["liquidated"]
                and candidate["metrics"][name]["net_return"] > benchmarks[name]["net_return"]
                for name in ("research", "validation")
            )
            and candidate["metrics"]["research"]["max_drawdown"]
            >= benchmarks["research"]["max_drawdown"]
        )

    qualifying = [item for item in candidates if item["development_qualifies"]]
    qualifying.sort(key=lambda item: item["development_score"], reverse=True)
    if not qualifying:
        raise RuntimeError("no strict-cap candidate passed development gates")
    selected = qualifying[0]
    for name in ("oos", "full"):
        selected["metrics"][name] = evaluate(
            bars, selected["targets"], funding, *splits[name], record_equity=name == "full"
        )

    full = selected["metrics"]["full"]
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.pop("equity_curve"), 100_000.0, start_ms=splits["full"][0]
    )
    elapsed = years_between(*splits["full"])
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs, benchmark_logs, block_days=block, samples=10_000, seed=20260902 + block
        )
        for block in (7, 30, 90)
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "family": "macro-gated 4h SMA + realized-volatility scaling",
            "selection": "development gates and ranking use Research/Validation only",
            "oos": "2025 onward is evaluated only after selection",
            "signal": "completed 4h inputs; next 15m open",
            "costs": "10 bps fee + 5 bps slippage; 2% maintenance stress",
            "open_leverage_cap": "2.5x futures-wallet equity",
            "effective_leverage_cap": "<=3x including stressed intrabar-low audit",
        },
        "data": {
            "bars": len(bars),
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
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def clamp_targets(targets, cap: Decimal):
    return tuple(None if value is None else min(Decimal(value), cap) for value in targets)


def evaluate(bars, targets, funding, start, end, *, record_equity=False):
    result = replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=Decimal("0"),
        maintenance_rate=MAINTENANCE,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        enforce_effective_leverage_cap=True,
        record_equity=record_equity,
        maximum_futures_leverage=OPEN_CAP,
    )
    output = asdict(result)
    return output


def annualized_excess(metrics, baseline, bounds):
    years = years_between(*bounds)
    return annualized_return(metrics["net_return"], years) - annualized_return(
        baseline["net_return"], years
    )


def public(candidate, benchmarks, elapsed):
    metrics = candidate["metrics"]
    out = {
        "id": candidate["id"],
        "volatility_lookback": candidate["volatility_lookback"],
        "target_volatility": candidate["target_volatility"],
        "open_cap": str(OPEN_CAP),
        "development_score": candidate.get("development_score"),
        "development_cap_passed": candidate.get("development_cap_passed"),
        "development_qualifies": candidate.get("development_qualifies"),
        "metrics": {},
    }
    for name, value in metrics.items():
        row = {
            key: value[key]
            for key in (
                "net_return",
                "max_drawdown",
                "total_fees",
                "total_funding",
                "liquidated",
                "maximum_controlled_open_futures_leverage",
                "maximum_observed_futures_leverage",
            )
            if key in value
        }
        if benchmarks is not None and name in benchmarks:
            row["benchmark_return"] = benchmarks[name]["net_return"]
            row["excess"] = value["net_return"] - benchmarks[name]["net_return"]
        if elapsed is not None and name == "full":
            row["cagr"] = annualized_return(value["net_return"], elapsed)
        out["metrics"][name] = row
    return out


def iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def pct(value) -> str:
    return "-" if value is None else f"{value:.2%}"


def render(payload):
    selected = payload["selected"]
    lines = [
        "# BTC Macro/Volatility Strategy — Strict 3X Audit",
        "",
        (
            "波动率缩放候选使用 2.5X 开盘上限，为盘中权益变化保留缓冲；"
            "任何 Research/Validation 盘中有效杠杆超过 3X 的候选均淘汰。"
        ),
        "",
        "## Selected candidate",
        "",
        (
            f"`{selected['id']}`；开发期合格候选 "
            f"{payload['development_qualifying_count']} / {payload['candidate_count']}。"
        ),
        "",
        "| 区间 | 策略收益 | B&H | 超额 | 策略DD | 盘中最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = selected["metrics"][name]
        lines.append(
            f"| {name} | {pct(row['net_return'])} | {pct(row.get('benchmark_return'))} | "
            f"{pct(row.get('excess'))} | {pct(row['max_drawdown'])} | "
            f"{row['maximum_observed_futures_leverage']:.3f}X |"
        )
    lines += [
        "",
        f"Full CAGR：{pct(selected['metrics']['full']['cagr'])}；"
        f"B&H CAGR：{pct(benchmark_cagr(payload))}。",
        "",
        "## Bootstrap",
        "",
    ]
    for block, row in payload["bootstrap"].items():
        lines.append(
            f"- {block}: 超过 B&H {row['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += [
        "",
        "结论：历史/开发与 OOS 结果若为正，只能说明候选值得前向观察；尚未证明统计显著或适合实盘。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


def period_bounds(payload):
    # The full benchmark is already measured over the same split; use its
    # elapsed period from the data timestamps in the report instead of relying
    # on a hard-coded year count.
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
