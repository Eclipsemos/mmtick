#!/usr/bin/env python3
"""Research a fixed equal-weight ensemble of BTC three-state SMA neighbors."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_macro_gated_3x import tail_concentration
from audit_btc_sma12_three_state_stability import exact_sign_pvalue
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import annualized_return, replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma12_three_state import build_dense_targets
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT = Path("reports/experiments/btc_sma_three_state_ensemble/2026-09-03")
FAST_PERIODS = (10, 11, 12, 13, 14)
SLOW_PERIODS = (36, 38, 40, 42, 44)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    streams = tuple(
        build_dense_targets(
            daily,
            Decimal("1.25"),
            Decimal("1.5"),
            fast_period=fast,
            slow_period=slow,
        )
        for fast in FAST_PERIODS
        for slow in SLOW_PERIODS
    )
    ensemble_targets = map_targets_to_source(len(bars), equal_weight_targets(streams), ends)
    center_targets = map_targets_to_source(
        len(bars),
        build_dense_targets(daily, Decimal("1.25"), Decimal("1.5")),
        ends,
    )
    ensemble_results = {}
    center_results = {}
    full_result = None
    for name, bounds in splits.items():
        result = replay(
            bars,
            ensemble_targets,
            funding,
            bounds,
            record_equity=name == "full",
        )
        ensemble_results[name] = public(result, benchmarks[name], bounds)
        center_results[name] = public(
            replay(bars, center_targets, funding, bounds), benchmarks[name], bounds
        )
        if name == "full":
            full_result = result
    if full_result is None:
        raise RuntimeError("full replay was not produced")
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full_result.equity_curve, 100_000.0, start_ms=splits["full"][0]
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20261203 + block,
        )
        for block in (7, 30, 90)
    }
    yearly = yearly_results(bars, ensemble_targets, funding, splits["full"])
    yearly_wins = sum(row["excess"] > 0 for row in yearly)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / NOT_PROMOTED",
        "protocol": {
            "members": "all 25 combinations of fast 10-14 and slow 36/38/40/42/44",
            "weights": "fixed equal 4% weights; no selection or optimization",
            "member_exposures": "bear 0X; neutral 1.25X; bull 1.5X",
            "execution": "average completed-daily target; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "hard_cap": "2.5X futures opening cap and <=3X intrabar leverage",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "ensemble": ensemble_results,
        "center_sma12_40": center_results,
        "bootstrap": bootstrap,
        "tail_concentration": tail_concentration(strategy_logs, benchmark_logs),
        "yearly": yearly,
        "yearly_summary": {
            "wins": yearly_wins,
            "years": len(yearly),
            "one_sided_sign_pvalue": exact_sign_pvalue(yearly_wins, len(yearly)),
        },
        "decision": decision(ensemble_results, center_results, bootstrap),
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def equal_weight_targets(streams):
    if not streams or len({len(stream) for stream in streams}) != 1:
        raise ValueError("target streams must be non-empty and equally sized")
    count = Decimal(len(streams))
    output = []
    for values in zip(*streams, strict=True):
        if any(value is None for value in values):
            output.append(None)
        else:
            output.append(sum((Decimal(value) for value in values), Decimal("0")) / count)
    return tuple(output)


def replay(bars, targets, funding, bounds, *, record_equity=False):
    return replay_segregated(
        bars,
        targets,
        funding,
        *bounds,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("2.5"),
    )


def public(result, baseline, bounds):
    return {
        "net_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "cagr": annualized_return(result.net_return, years_between(*bounds)),
        "max_drawdown": result.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def yearly_results(bars, targets, funding, bounds):
    first = datetime.fromtimestamp(bounds[0] / 1000, UTC).year
    last = datetime.fromtimestamp(bounds[1] / 1000, UTC).year
    rows = []
    for year in range(first, last + 1):
        start = max(bounds[0], int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000))
        end = min(
            bounds[1],
            int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1,
        )
        result = replay(bars, targets, funding, (start, end))
        baseline = benchmark(bars, start, end)
        rows.append(
            {
                "year": year,
                "strategy_return": result.net_return,
                "benchmark_return": baseline["net_return"],
                "excess": result.net_return - baseline["net_return"],
            }
        )
    return rows


def decision(ensemble, center, bootstrap):
    full = ensemble["full"]
    center_full = center["full"]
    return {
        "beats_bh_all_splits": all(row["excess"] > 0 for row in ensemble.values()),
        "hard_3x_passed": all(row["maximum_intrabar_leverage"] <= 3 for row in ensemble.values()),
        "beats_center_return": full["net_return"] > center_full["net_return"],
        "improves_center_drawdown": full["max_drawdown"] >= center_full["max_drawdown"],
        "bootstrap_p05_positive": all(
            row["annualized_excess_vs_bh"]["p05"] > 0 for row in bootstrap.values()
        ),
    }


def render(payload):
    lines = [
        "# BTC Three-State SMA Neighborhood Ensemble",
        "",
        "固定等权组合全部 25 个 SMA 邻域成员，不搜索成员或权重。",
        "",
        "| 区间 | Ensemble | SMA12/40 | B&H | Ensemble超额 | DD | 最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = payload["ensemble"][name]
        center = payload["center_sma12_40"][name]
        lines.append(
            f"| {name} | {row['net_return']:.2%} | {center['net_return']:.2%} | "
            f"{row['benchmark_return']:.2%} | {row['excess']:.2%} | "
            f"{row['max_drawdown']:.2%} | {row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += ["", "## Bootstrap", ""]
    for label, row in payload["bootstrap"].items():
        lines.append(
            f"- {label}: 超过 B&H {row['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        )
    lines += ["", "## Tail Concentration", ""]
    for row in payload["tail_concentration"]:
        lines.append(
            f"- 移除 {row['removed_best_relative_days']} 日：年化超额 "
            f"{row['annualized_excess']:.2%}。"
        )
    summary = payload["yearly_summary"]
    lines += [
        "",
        f"逐年跑赢：{summary['wins']}/{summary['years']}；"
        f"单侧符号检验 p={summary['one_sided_sign_pvalue']:.4f}。",
        "",
        f"状态：**{payload['status']}**。",
        "",
    ]
    return "\n".join(lines)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
