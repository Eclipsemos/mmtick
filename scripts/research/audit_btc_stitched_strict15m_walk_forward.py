#!/usr/bin/env python3
"""Yearly walk-forward selection for the stitched strict-15m BTC SMA family."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base

OUTPUT = Path("reports/experiments/btc_stitched_strict15m_walk_forward/2026-09-03")
FAST_PERIODS = (9, 10, 11, 12)
ENTER_DAYS = (1, 2, 3)
ACTIVE = Decimal("1.5")
COSTS = (
    ("default", Decimal("10"), Decimal("5")),
    ("moderate", Decimal("20"), Decimal("10")),
)


def main() -> None:
    spot, futures, daily, target_indices, funding = base.load_hybrid_inputs()
    bars = spot + futures
    start = int(base.FUTURES_START.timestamp() * 1000)
    end = bars[-1].end_ms
    candidate_targets = {}
    candidates = []
    for fast in FAST_PERIODS:
        for enter in ENTER_DAYS:
            candidate_id = f"sma{fast}/40-enter{enter}-exit1-active1.5x"
            candidate_targets[candidate_id] = base.map_targets(
                len(bars),
                target_indices,
                base.build_targets(daily, fast_period=fast, enter_bear_days=enter, active=ACTIVE),
            )
            candidates.append(candidate_id)

    selections = select_each_year(bars, candidate_targets, funding, start, end)
    combined = combine_yearly_targets(bars, candidate_targets, selections)
    result = base.replay(bars, combined, funding, start, end, record_equity=True)
    baseline = base.benchmark(bars, start, end)
    yearly = annual_returns(bars, result.equity_curve, start)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "family": "daily SMA fast/40, bear-flat, active 1.5X",
            "selection": (
                "before each calendar year, select the maximum worst cumulative excess across "
                "default and 20+10 bps costs using only prior data"
            ),
            "execution": "spot daily pre-2020; perpetual next 15m open from 2020",
            "costs": "10+5 bps primary, 20+10 bps selection stress, historical Funding",
            "capital": "50% spot and 50% isolated USD-M collateral; 2X futures opening cap",
            "oos": "each selected year's return is evaluated after selection",
        },
        "data": {
            "first": base.iso(bars[0].start_ms),
            "last": base.iso(end),
            "candidates": candidates,
        },
        "selections": selections,
        "yearly": yearly,
        "full": {
            "strategy_return": result.net_return,
            "benchmark_return": baseline["net_return"],
            "excess": result.net_return - baseline["net_return"],
            "max_drawdown": result.max_drawdown,
            "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
            "liquidated": result.liquidated,
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def select_each_year(bars, targets_by_id, funding, start, end):
    final_year = datetime.fromtimestamp(end / 1000, UTC).year
    selections = {}
    for test_year in range(2020, final_year + 1):
        train_end = base.utc_ms(test_year) - 1
        scores = []
        for candidate_id, targets in targets_by_id.items():
            benchmark = base.benchmark(
                bars, int(base.EVALUATION_START.timestamp() * 1000), train_end
            )
            excesses = []
            for _label, fee_bps, slippage_bps in COSTS:
                result = base.replay(
                    bars,
                    targets,
                    funding,
                    int(base.EVALUATION_START.timestamp() * 1000),
                    train_end,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                )
                excesses.append(result.net_return - benchmark["net_return"])
            scores.append((min(excesses), candidate_id))
        score, candidate_id = max(scores)
        selections[str(test_year)] = {"candidate": candidate_id, "training_worst_excess": score}
    return selections


def combine_yearly_targets(bars, targets_by_id, selections):
    output = []
    for index, _bar in enumerate(bars):
        execution_index = min(index + 1, len(bars) - 1)
        execution_year = datetime.fromtimestamp(bars[execution_index].start_ms / 1000, UTC).year
        candidate = selections.get(str(execution_year))
        output.append(None if candidate is None else targets_by_id[candidate["candidate"]][index])
    return tuple(output)


def annual_returns(bars, equity_curve, start_ms):
    equity_by_day = {}
    close_by_day = {}
    for timestamp_ms, equity in equity_curve:
        equity_by_day[timestamp_ms // 86_400_000] = equity
    for bar in bars:
        close_by_day[bar.end_ms // 86_400_000] = float(bar.close)
    previous_equity = 100_000.0
    first_bar = next(bar for bar in bars if bar.start_ms >= start_ms)
    previous_price = float(first_bar.open)
    output = {}
    for day in sorted(set(equity_by_day) & set(close_by_day)):
        equity, price = equity_by_day[day], close_by_day[day]
        year = str(datetime.fromtimestamp(day * 86_400, UTC).year)
        row = output.setdefault(year, {"strategy_logs": [], "benchmark_logs": []})
        row["strategy_logs"].append(math.log(equity / previous_equity))
        row["benchmark_logs"].append(math.log(price / previous_price))
        previous_equity, previous_price = equity, price
    for row in output.values():
        row["strategy_return"] = math.exp(sum(row.pop("strategy_logs"))) - 1
        row["benchmark_return"] = math.exp(sum(row.pop("benchmark_logs"))) - 1
        row["excess"] = row["strategy_return"] - row["benchmark_return"]
    return output


def render(payload):
    lines = [
        "# BTC Stitched Strict-15m Annual Walk-Forward",
        "",
        "每年仅用此前数据从预设 SMA 网格选择下一年的参数，并把各年目标拼接为连续回放。",
        "",
        "| 年份 | 选择 | 训练最差超额 | 策略 | B&H | 超额 |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for year, selected in payload["selections"].items():
        row = payload["yearly"].get(year)
        if row is None:
            continue
        lines.append(
            f"| {year} | `{selected['candidate']}` | {selected['training_worst_excess']:.2%} | "
            f"{row['strategy_return']:.2%} | {row['benchmark_return']:.2%} | {row['excess']:.2%} |"
        )
    full = payload["full"]
    lines += [
        "",
        f"Full 策略收益 `{full['strategy_return']:.2%}`，B&H `{full['benchmark_return']:.2%}`，"
        f"超额 `{full['excess']:.2%}`，DD `{full['max_drawdown']:.2%}`，"
        f"峰值杠杆 `{full['maximum_intrabar_leverage']:.3f}X`。",
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
