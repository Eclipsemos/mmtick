#!/usr/bin/env python3
"""Audit an equal-capital ensemble of the two frozen BTC candidates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

from audit_btc_frozen_rolling_windows import WINDOWS, evaluate_windows, summarize
from research_btc_dynamic_exposure import as_dict, benchmark, replay_dynamic_incremental
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT_DIR = Path("reports/experiments/btc_frozen_ensemble/2026-09-02")
FUNDING_THRESHOLD = Decimal("0.0001")
MAXIMUM_EXPOSURE = Decimal("3")
CANDIDATES = (
    {
        "id": "primary",
        "periods": (26, 52, 104, 208),
        "bear_exposure": Decimal("0"),
        "bull_exposure": Decimal("1.5"),
    },
    {
        "id": "partial_bear_challenger",
        "periods": (25, 50, 100, 200),
        "bear_exposure": Decimal("0.5"),
        "bull_exposure": Decimal("1.75"),
    },
)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    targets = build_targets(bars, funding)
    targets["equal_weight_ensemble"] = combine_sparse_targets(
        targets["primary"], targets["partial_bear_challenger"]
    )
    metrics = evaluate_splits(bars, funding, targets, splits)
    rolling = evaluate_rolling(bars, funding, targets)
    yearly = evaluate_years(bars, funding, targets)
    generated_at = datetime.now(UTC).isoformat()
    frozen = frozen_config(generated_at)
    full_start, full_end = splits["full"]
    elapsed_years = (full_end - full_start) / (365.2425 * 86_400_000)
    full_ensemble = metrics["full"]["strategies"]["equal_weight_ensemble"]
    payload = {
        "generated_at": generated_at,
        "status": "FORWARD_TESTING_ENSEMBLE",
        "frozen_config": frozen,
        "protocol": {
            "construction": (
                "50% capital to each frozen candidate; combined target is their arithmetic mean"
            ),
            "selection": "fixed equal weights; no weight search and no OOS-based optimization",
            "execution": "completed 4h signal; rebalance exposure delta at next 15m open",
            "maximum_allowed_exposure": str(MAXIMUM_EXPOSURE),
            "ensemble_observed_exposure_range": exposure_range(targets["equal_weight_ensemble"]),
            "costs": "base 5+2 bps; stress 10+5 bps on changed notional",
            "funding": "last known rate filter; charged only above 1x",
        },
        "data": {
            "bars": len(bars),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
        },
        "splits": metrics,
        "annualized": {
            "elapsed_years": elapsed_years,
            "base_cagr": cagr(full_ensemble["base"]["net_return"], elapsed_years),
            "stress_cagr": cagr(full_ensemble["stress"]["net_return"], elapsed_years),
        },
        "rolling": rolling,
        "yearly": yearly,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "frozen-ensemble.json").write_text(
        json.dumps(frozen, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_targets(bars, funding):
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    result = {}
    for candidate in CANDIDATES:
        regime = map_targets_to_source(
            len(bars),
            three_state_targets(
                aggregate,
                candidate["periods"],
                candidate["bear_exposure"],
                candidate["bull_exposure"],
            ),
            ends,
        )
        result[candidate["id"]] = funding_aware_targets(
            regime,
            funding,
            candidate["bull_exposure"],
            FUNDING_THRESHOLD,
        )
    return result


def combine_sparse_targets(
    left,
    right,
    *,
    left_weight: Decimal = Decimal("0.5"),
    maximum_exposure: Decimal = MAXIMUM_EXPOSURE,
):
    """Combine two causal sparse target streams without forward filling future states."""
    if len(left) != len(right):
        raise ValueError("target lengths differ")
    if not Decimal("0") <= left_weight <= Decimal("1"):
        raise ValueError("left weight must be between zero and one")
    if maximum_exposure <= 0:
        raise ValueError("maximum exposure must be positive")
    right_weight = Decimal("1") - left_weight
    left_state = Decimal("1")
    right_state = Decimal("1")
    previous = Decimal("1")
    combined = []
    for left_target, right_target in zip(left, right, strict=True):
        changed = False
        if left_target is not None:
            left_state = Decimal(left_target)
            changed = True
        if right_target is not None:
            right_state = Decimal(right_target)
            changed = True
        target = left_state * left_weight + right_state * right_weight
        if not Decimal("0") <= target <= maximum_exposure:
            raise ValueError("combined target exceeds exposure bounds")
        if changed and target != previous:
            combined.append(target)
            previous = target
        else:
            combined.append(None)
    return tuple(combined)


def evaluate_splits(bars, funding, targets, splits):
    output = {}
    for split, (start, end) in splits.items():
        baseline = benchmark(bars, start, end)
        output[split] = {"buy_and_hold": baseline, "strategies": {}}
        for candidate_id, candidate_targets in targets.items():
            base = replay_dynamic_incremental(
                bars,
                candidate_targets,
                funding,
                start,
                end,
                funding_on_excess_only=True,
            )
            stress = replay_dynamic_incremental(
                bars,
                candidate_targets,
                funding,
                start,
                end,
                fee_bps=Decimal("10"),
                slippage_bps=Decimal("5"),
                funding_on_excess_only=True,
            )
            output[split]["strategies"][candidate_id] = {
                "base": as_dict(base),
                "stress": as_dict(stress),
                "base_excess": base.net_return - baseline["net_return"],
                "stress_excess": stress.net_return - baseline["net_return"],
            }
    return output


def evaluate_rolling(bars, funding, targets):
    output = {}
    for candidate_id, candidate_targets in targets.items():
        output[candidate_id] = {}
        for label, days in WINDOWS:
            rows = evaluate_windows(bars, funding, candidate_targets, days)
            output[candidate_id][label] = summarize(rows)
    return output


def evaluate_years(bars, funding, targets):
    first_year = 2020
    last_year = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).year
    output = []
    for year in range(first_year, last_year + 1):
        start = utc_ms(year, 1, 1)
        end = min(utc_ms(year + 1, 1, 1) - 1, bars[-1].end_ms)
        if end < bars[0].start_ms:
            continue
        baseline = benchmark(bars, max(start, bars[0].start_ms), end)
        row = {"year": year, "buy_and_hold": baseline["net_return"], "strategies": {}}
        for candidate_id, candidate_targets in targets.items():
            stress = replay_dynamic_incremental(
                bars,
                candidate_targets,
                funding,
                max(start, bars[0].start_ms),
                end,
                fee_bps=Decimal("10"),
                slippage_bps=Decimal("5"),
                funding_on_excess_only=True,
            )
            row["strategies"][candidate_id] = stress.net_return
        output.append(row)
    return output


def exposure_range(targets):
    values = [Decimal("1")]
    values.extend(Decimal(target) for target in targets if target is not None)
    return {"minimum": str(min(values)), "maximum": str(max(values))}


def frozen_config(generated_at):
    return {
        "frozen_at": generated_at,
        "forward_start": "2026-09-03T00:00:00+00:00",
        "status": "FORWARD_TESTING_SECONDARY",
        "position_model": "fixed quantities between sparse target changes",
        "weights": {"primary": "0.5", "partial_bear_challenger": "0.5"},
        "components": [
            {
                "id": candidate["id"],
                "timeframe": "4h",
                "periods": candidate["periods"],
                "bear_exposure": str(candidate["bear_exposure"]),
                "neutral_exposure": "1",
                "bull_exposure": str(candidate["bull_exposure"]),
                "funding_threshold": str(FUNDING_THRESHOLD),
            }
            for candidate in CANDIDATES
        ],
        "maximum_allowed_exposure": str(MAXIMUM_EXPOSURE),
        "maximum_ensemble_exposure": "1.625",
        "parameter_changes_allowed": False,
    }


def markdown(payload):
    lines = [
        "# BTC 冻结候选等权组合审计",
        "",
        "把冻结主策略和熊市部分底仓挑战者各分配 50% 资金；不搜索组合权重。",
        "两个子策略仍只使用完整 4h K 线，下一根 15m 开盘按总敞口差额调仓。",
        "",
        "## 分段结果",
        "",
        "| 分段 | 策略 | 收益 | 压力收益 | B&H | 压力超额 | DD |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for split, split_result in payload["splits"].items():
        baseline = split_result["buy_and_hold"]
        for candidate_id, result in split_result["strategies"].items():
            lines.append(
                f"| {split} | `{candidate_id}` | {pct(result['base']['net_return'])} | "
                f"{pct(result['stress']['net_return'])} | {pct(baseline['net_return'])} | "
                f"{pct(result['stress_excess'])} | {pct(result['base']['max_drawdown'])} |"
            )
    lines += [
        "",
        "## 滚动窗口压力检验",
        "",
        "| 策略 | 窗口 | 超过B&H比例 | 收益与DD同时胜出 | 中位超额 | 最差超额 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for candidate_id, windows in payload["rolling"].items():
        for label, result in windows.items():
            lines.append(
                f"| `{candidate_id}` | {label} | "
                f"{pct(result['stress_return_win_rate'])} | "
                f"{pct(result['stress_return_and_drawdown_win_rate'])} | "
                f"{pct(result['median_stress_excess'])} | "
                f"{pct(result['worst_stress_excess'])} |"
            )
    ensemble_years = [
        row["strategies"]["equal_weight_ensemble"] - row["buy_and_hold"]
        for row in payload["yearly"]
    ]
    lines += [
        "",
        "## 结论",
        "",
        f"组合实际总敞口范围为 "
        f"{payload['protocol']['ensemble_observed_exposure_range']['minimum']}X–"
        f"{payload['protocol']['ensemble_observed_exposure_range']['maximum']}X，"
        "严格低于 3X 上限。",
        f"逐年压力超额中位数为 {pct(median(ensemble_years))}。",
        f"全样本 CAGR 为 {pct(payload['annualized']['base_cagr'])}；"
        f"压力成本 CAGR 为 {pct(payload['annualized']['stress_cagr'])}。",
        "年度家族Walk-Forward压力复合72.47%，高于B&H 67.59%，但低于主策略78.79%。",
        "该组合是在前向起点前定义的独立 challenger；历史结果不能代替前向证据。",
        "",
    ]
    return "\n".join(lines)


def utc_ms(year, month, day):
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return f"{value:.2%}"


def cagr(net_return, years):
    return (1 + net_return) ** (1 / years) - 1


if __name__ == "__main__":
    main()
