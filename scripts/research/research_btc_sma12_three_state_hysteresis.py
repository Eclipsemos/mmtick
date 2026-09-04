#!/usr/bin/env python3
"""Audit fixed 2/1 bear hysteresis on the frozen BTC SMA12/40 three-state signal."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_macro_gated_3x import tail_concentration
from audit_btc_sma12_three_state_stability import exact_sign_pvalue
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_dynamic_exposure import benchmark
from research_btc_sma12_three_state import build_dense_targets
from research_btc_sma_three_state_ensemble import iso, public, replay, yearly_results
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT = Path("reports/experiments/btc_sma12_three_state_hysteresis/2026-09-03")
NEUTRAL = Decimal("1.25")
BULL = Decimal("1.5")
ENTER_BEAR_DAYS = 2
EXIT_BEAR_DAYS = 1


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    raw_daily_targets = build_dense_targets(daily, NEUTRAL, BULL)
    candidate_targets = map_targets_to_source(
        len(bars),
        apply_bear_hysteresis(
            raw_daily_targets,
            enter_bear_days=ENTER_BEAR_DAYS,
            exit_bear_days=EXIT_BEAR_DAYS,
        ),
        ends,
    )
    center_targets = map_targets_to_source(len(bars), raw_daily_targets, ends)
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}

    candidate, candidate_full = evaluate_all(bars, candidate_targets, funding, splits, benchmarks)
    center, center_full = evaluate_all(bars, center_targets, funding, splits, benchmarks)
    candidate_stats = path_statistics(
        bars, candidate_targets, funding, splits, candidate_full, seed=20261300
    )
    center_stats = path_statistics(
        bars, center_targets, funding, splits, center_full, seed=20261400
    )
    decision = compare(candidate, center, candidate_stats, center_stats)
    status = (
        "RESEARCH_ONLY / NEW_FORWARD_FREEZE_CANDIDATE"
        if decision["material_robustness_improvement"]
        else "RESEARCH_ONLY / NOT_PROMOTED"
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": status,
        "protocol": {
            "candidate": (
                "SMA12/40 bear 0X, neutral 1.25X, bull 1.5X; "
                "enter bear after 2 consecutive raw bear days and exit after 1 non-bear day"
            ),
            "selection": "fixed 2/1 rule migrated from prior research; no parameter search",
            "execution": "completed UTC daily signal; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "hard_cap": "2.5X futures opening control and <=3X observed intrabar leverage",
            "causality": "state at day t uses only raw targets through completed day t",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "candidate": candidate,
        "center_sma12_40": center,
        "candidate_path": candidate_stats,
        "center_path": center_stats,
        "decision": decision,
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def apply_bear_hysteresis(
    raw_targets,
    *,
    enter_bear_days: int = ENTER_BEAR_DAYS,
    exit_bear_days: int = EXIT_BEAR_DAYS,
):
    if enter_bear_days < 1 or exit_bear_days < 1:
        raise ValueError("hysteresis confirmation days must be positive")
    output = []
    state = None
    bear_days = 0
    recovery_days = 0
    active_target = None
    for raw in raw_targets:
        if raw is None:
            output.append(None)
            continue
        raw = Decimal(raw)
        bearish = raw == 0
        bear_days = bear_days + 1 if bearish else 0
        recovery_days = recovery_days + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish and bear_days >= enter_bear_days else "active"
        elif state == "active" and bear_days >= enter_bear_days:
            state = "bear"
        elif state == "bear" and recovery_days >= exit_bear_days:
            state = "active"
        if state == "bear":
            active_target = Decimal("0")
        elif not bearish:
            active_target = raw
        elif active_target is None:
            active_target = raw
        output.append(active_target)
    return tuple(output)


def evaluate_all(bars, targets, funding, splits, benchmarks):
    metrics = {}
    full_result = None
    for name, bounds in splits.items():
        result = replay(
            bars,
            targets,
            funding,
            bounds,
            record_equity=name == "full",
        )
        metrics[name] = public(result, benchmarks[name], bounds)
        if name == "full":
            full_result = result
    if full_result is None:
        raise RuntimeError("full replay was not produced")
    return metrics, full_result


def path_statistics(bars, targets, funding, splits, full_result, *, seed):
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full_result.equity_curve, 100_000.0, start_ms=splits["full"][0]
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=seed + block,
        )
        for block in (7, 30, 90)
    }
    yearly = yearly_results(bars, targets, funding, splits["full"])
    wins = sum(row["excess"] > 0 for row in yearly)
    return {
        "bootstrap": bootstrap,
        "tail_concentration": tail_concentration(strategy_logs, benchmark_logs),
        "yearly": yearly,
        "yearly_summary": {
            "wins": wins,
            "years": len(yearly),
            "one_sided_sign_pvalue": exact_sign_pvalue(wins, len(yearly)),
        },
    }


def compare(candidate, center, candidate_path, center_path):
    candidate_tail = {
        row["removed_best_relative_days"]: row["annualized_excess"]
        for row in candidate_path["tail_concentration"]
    }
    center_tail = {
        row["removed_best_relative_days"]: row["annualized_excess"]
        for row in center_path["tail_concentration"]
    }
    candidate_p05 = candidate_path["bootstrap"]["90d"]["annualized_excess_vs_bh"]["p05"]
    center_p05 = center_path["bootstrap"]["90d"]["annualized_excess_vs_bh"]["p05"]
    checks = {
        "beats_bh_all_splits": all(row["excess"] > 0 for row in candidate.values()),
        "hard_3x_passed": all(row["maximum_intrabar_leverage"] <= 3 for row in candidate.values()),
        "beats_center_full_return": candidate["full"]["net_return"] > center["full"]["net_return"],
        "beats_center_oos_return": candidate["oos"]["net_return"] > center["oos"]["net_return"],
        "improves_center_drawdown": (
            candidate["full"]["max_drawdown"] >= center["full"]["max_drawdown"]
        ),
        "improves_center_90d_p05": candidate_p05 > center_p05,
        "improves_center_tail_5d": candidate_tail[5] > center_tail[5],
        "improves_center_tail_10d": candidate_tail[10] > center_tail[10],
        "yearly_wins_not_worse": (
            candidate_path["yearly_summary"]["wins"] >= center_path["yearly_summary"]["wins"]
        ),
    }
    checks["material_robustness_improvement"] = all(checks.values())
    return checks


def render(payload):
    lines = [
        "# BTC SMA12/40 Three-State 2/1 Hysteresis",
        "",
        "固定迁移 2 日熊市确认、1 日恢复确认；不搜索确认天数或暴露参数。",
        "",
        "| 区间 | Hysteresis | SMA12/40 | B&H | Hysteresis超额 | DD | 最高杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("research", "validation", "oos", "full"):
        row = payload["candidate"][name]
        center = payload["center_sma12_40"][name]
        lines.append(
            f"| {name} | {row['net_return']:.2%} | {center['net_return']:.2%} | "
            f"{row['benchmark_return']:.2%} | {row['excess']:.2%} | "
            f"{row['max_drawdown']:.2%} | {row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        "## Bootstrap Comparison",
        "",
        "| 区块 | Hysteresis P05 | SMA12/40 P05 |",
        "|---|---:|---:|",
    ]
    for label in ("7d", "30d", "90d"):
        candidate = payload["candidate_path"]["bootstrap"][label]
        center = payload["center_path"]["bootstrap"][label]
        lines.append(
            f"| {label} | {candidate['annualized_excess_vs_bh']['p05']:.2%} | "
            f"{center['annualized_excess_vs_bh']['p05']:.2%} |"
        )
    lines += [
        "",
        "## Tail Comparison",
        "",
        "| 移除最佳相对收益日 | Hysteresis | SMA12/40 |",
        "|---:|---:|---:|",
    ]
    center_tail = {
        row["removed_best_relative_days"]: row
        for row in payload["center_path"]["tail_concentration"]
    }
    for row in payload["candidate_path"]["tail_concentration"]:
        count = row["removed_best_relative_days"]
        lines.append(
            f"| {count} | {row['annualized_excess']:.2%} | "
            f"{center_tail[count]['annualized_excess']:.2%} |"
        )
    candidate_years = payload["candidate_path"]["yearly_summary"]
    center_years = payload["center_path"]["yearly_summary"]
    lines += [
        "",
        f"逐年跑赢：Hysteresis {candidate_years['wins']}/{candidate_years['years']}，"
        f"SMA12/40 {center_years['wins']}/{center_years['years']}。",
        "",
        f"状态：**{payload['status']}**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
