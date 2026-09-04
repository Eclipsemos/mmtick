#!/usr/bin/env python3
"""Audit a fixed calm-bear exposure floor on BTC SMA12/40 three-state targets."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_dynamic_exposure import benchmark
from research_btc_sma12_three_state import build_dense_targets
from research_btc_sma12_three_state_hysteresis import evaluate_all, path_statistics
from research_btc_sma_three_state_ensemble import iso
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar, wilder_atr_values
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT = Path("reports/experiments/btc_sma12_bear_vol_floor/2026-09-03")
LOOKBACK = 120
FLOOR = Decimal("0.25")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    center_daily = build_dense_targets(daily, Decimal("1.25"), Decimal("1.5"))
    ratios = atr_price_ratios(daily)
    thresholds = rolling_medians(ratios, LOOKBACK)
    floor_daily = calm_bear_floor(center_daily, ratios, thresholds, FLOOR)
    floor_targets = map_targets_to_source(len(bars), floor_daily, ends)
    center_targets = map_targets_to_source(len(bars), center_daily, ends)
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}

    candidate, candidate_full = evaluate_all(bars, floor_targets, funding, splits, benchmarks)
    center, center_full = evaluate_all(bars, center_targets, funding, splits, benchmarks)
    candidate_path = path_statistics(
        bars, floor_targets, funding, splits, candidate_full, seed=20261500
    )
    center_path = path_statistics(bars, center_targets, funding, splits, center_full, seed=20261600)
    decision = compare(candidate, center, candidate_path, center_path)
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
                "frozen SMA12/40 three-state plus 0.25X floor when raw state is bear and "
                "ATR14/close is at or below its trailing 120-day median"
            ),
            "selection": "one fixed rule; no parameter grid or exposure optimization",
            "execution": "completed UTC daily signal; next 15m open",
            "wallets": "50% spot; 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "hard_cap": "2.5X futures opening control and <=3X observed intrabar leverage",
            "causality": "ATR and median at day t use completed daily bars through day t only",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "candidate": candidate,
        "center_sma12_40": center,
        "candidate_path": candidate_path,
        "center_path": center_path,
        "decision": decision,
    }
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def atr_price_ratios(bars):
    atr = wilder_atr_values(bars, 14)
    return tuple(
        None if value is None or bar.close <= 0 else value / bar.close
        for bar, value in zip(bars, atr, strict=True)
    )


def rolling_medians(values, lookback: int):
    if lookback < 1:
        raise ValueError("lookback must be positive")
    output = []
    for index in range(len(values)):
        sample = [
            value for value in values[max(0, index - lookback + 1) : index + 1] if value is not None
        ]
        if len(sample) < lookback:
            output.append(None)
            continue
        sample.sort()
        middle = lookback // 2
        median = (
            sample[middle] if lookback % 2 else (sample[middle - 1] + sample[middle]) / Decimal("2")
        )
        output.append(median)
    return tuple(output)


def calm_bear_floor(raw_targets, ratios, thresholds, floor):
    if not (len(raw_targets) == len(ratios) == len(thresholds)):
        raise ValueError("target and volatility streams must have equal lengths")
    if floor < 0:
        raise ValueError("floor cannot be negative")
    output = []
    for target, ratio, threshold in zip(raw_targets, ratios, thresholds, strict=True):
        if target is None:
            output.append(None)
        elif target == 0 and ratio is not None and threshold is not None and ratio <= threshold:
            output.append(floor)
        else:
            output.append(target)
    return tuple(output)


def compare(candidate, center, candidate_path, center_path):
    candidate_tail = tail_map(candidate_path)
    center_tail = tail_map(center_path)
    candidate_p05 = p05_90d(candidate_path)
    center_p05 = p05_90d(center_path)
    checks = {
        "beats_bh_all_splits": all(row["excess"] > 0 for row in candidate.values()),
        "hard_3x_passed": all(row["maximum_intrabar_leverage"] <= 3 for row in candidate.values()),
        "beats_center_full_return": candidate["full"]["net_return"] > center["full"]["net_return"],
        "beats_center_validation_return": (
            candidate["validation"]["net_return"] > center["validation"]["net_return"]
        ),
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


def tail_map(path):
    return {
        row["removed_best_relative_days"]: row["annualized_excess"]
        for row in path["tail_concentration"]
    }


def p05_90d(path):
    return path["bootstrap"]["90d"]["annualized_excess_vs_bh"]["p05"]


def render(payload):
    lines = [
        "# BTC SMA12/40 Calm-Bear Volatility Floor",
        "",
        "固定规则：低波动熊市保留 0.25X，高波动熊市仍为 0X；不搜索参数。",
        "",
        "| 区间 | Vol Floor | SMA12/40 | B&H | Vol Floor超额 | DD | 最高杠杆 |",
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
        "## Stability Comparison",
        "",
        "| 指标 | Vol Floor | SMA12/40 |",
        "|---|---:|---:|",
    ]
    candidate_tail = tail_map(payload["candidate_path"])
    center_tail = tail_map(payload["center_path"])
    lines += [
        f"| 90d Bootstrap P05 | {p05_90d(payload['candidate_path']):.2%} | "
        f"{p05_90d(payload['center_path']):.2%} |",
        f"| 移除最佳5日后年化超额 | {candidate_tail[5]:.2%} | {center_tail[5]:.2%} |",
        f"| 移除最佳10日后年化超额 | {candidate_tail[10]:.2%} | {center_tail[10]:.2%} |",
    ]
    candidate_years = payload["candidate_path"]["yearly_summary"]
    center_years = payload["center_path"]["yearly_summary"]
    lines += [
        "",
        f"逐年跑赢：Vol Floor {candidate_years['wins']}/{candidate_years['years']}，"
        f"SMA12/40 {center_years['wins']}/{center_years['years']}。",
        "",
        f"状态：**{payload['status']}**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
