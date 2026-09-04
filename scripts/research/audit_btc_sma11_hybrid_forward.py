#!/usr/bin/env python3
"""Update the forward-only ledger for the frozen BTC SMA11 hybrid candidate."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as candidate

FORWARD_START = datetime(2026, 9, 3, 1, 15, tzinfo=UTC)
OUTPUT = Path("reports/experiments/btc_sma11_enter2_active150_hybrid/2026-09-03/forward")


def main() -> None:
    spot, futures, daily, target_indices, funding = candidate.load_hybrid_inputs()
    bars = spot + futures
    start_ms = int(FORWARD_START.timestamp() * 1000)
    targets = candidate.map_targets(
        len(bars),
        target_indices,
        candidate.build_targets(daily, fast_period=11, enter_bear_days=2, active=Decimal("1.5")),
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "AWAITING_FORWARD_DATA",
        "frozen_config": {
            "id": "stitched-daily-sma11-40-enter2-exit1-active1.5",
            "signal": "completed UTC daily SMA11/40",
            "bear_rule": "close<SMA40 and SMA11<SMA40 for 2 consecutive daily bars",
            "recovery_rule": "1 consecutive non-bearish daily bar",
            "active_exposure": "1.5",
            "bear_exposure": "0",
            "execution": "next 15m open",
            "costs": "10 bps fee + 5 bps slippage per side; historical Funding",
            "leverage_guard": "2X futures opening control and <=3X effective leverage",
        },
        "forward_start": iso(start_ms),
        "latest_available_bar": iso(bars[-1].end_ms),
        "parameter_changes_allowed": False,
    }
    if bars[-1].end_ms >= start_ms:
        result = candidate.replay(
            bars,
            targets,
            funding,
            start_ms,
            bars[-1].end_ms,
            record_equity=True,
        )
        moderate = candidate.replay(
            bars,
            targets,
            funding,
            start_ms,
            bars[-1].end_ms,
            fee_bps=Decimal("20"),
            slippage_bps=Decimal("10"),
        )
        benchmark = candidate.benchmark(bars, start_ms, bars[-1].end_ms)
        payload.update(
            {
                "status": "FORWARD_OBSERVATION",
                "source_bars": sum(bar.start_ms >= start_ms for bar in bars),
                "strategy": candidate.public(result, benchmark, start_ms, bars[-1].end_ms),
                "moderate_cost": candidate.public(moderate, benchmark, start_ms, bars[-1].end_ms),
                "latest_target": latest_target(bars, targets),
                "forward_target_changes": target_changes(bars, targets, start_ms),
            }
        )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if payload["status"] == "FORWARD_OBSERVATION":
        payload["completed_forward_days"] = append_daily_returns(
            bars, result.equity_curve, start_ms
        )
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    if payload["status"] == "FORWARD_OBSERVATION":
        append_snapshot(payload)
    print(OUTPUT / "README.md")


def latest_target(bars, targets):
    for index in range(len(targets) - 1, -1, -1):
        if targets[index] is not None:
            return {"signal_time": iso(bars[index].end_ms), "target_exposure": str(targets[index])}
    return None


def target_changes(bars, targets, start_ms):
    output = []
    previous_target = None
    for index, target in enumerate(targets):
        if target is None or index + 1 >= len(bars):
            continue
        execution = bars[index + 1].start_ms
        if execution < start_ms:
            previous_target = target
            continue
        if target != previous_target:
            output.append(
                {
                    "signal_time": iso(bars[index].end_ms),
                    "execution_time": iso(execution),
                    "model_execution_price": str(bars[index + 1].open),
                    "target_exposure": str(target),
                }
            )
        previous_target = target
    return output


def append_daily_returns(bars, equity_curve, start_ms: int) -> int:
    """Append complete UTC forward days exactly once and return their stored count."""
    path = OUTPUT / "daily_returns.csv"
    equity_by_end = dict(equity_curve)
    selected = [bar for bar in bars if bar.start_ms >= start_ms]
    if not selected:
        return existing_row_count(path)
    by_day = {}
    for bar in selected:
        by_day.setdefault(bar.start_ms // 86_400_000, []).append(bar)
    previous_equity = 100_000.0
    previous_price = float(selected[0].open)
    rows = []
    for day, day_bars in sorted(by_day.items()):
        last = day_bars[-1]
        if last.end_ms != (day + 1) * 86_400_000 - 1:
            continue
        equity = equity_by_end.get(last.end_ms)
        if equity is None:
            continue
        price = float(last.close)
        rows.append(
            {
                "utc_date": datetime.fromtimestamp(day * 86_400, UTC).date().isoformat(),
                "period_end": iso(last.end_ms),
                "strategy_return": equity / previous_equity - 1,
                "benchmark_return": price / previous_price - 1,
                "excess": equity / previous_equity - price / previous_price,
            }
        )
        previous_equity = equity
        previous_price = price
    append_rows_once(path, rows, key="utc_date")
    return existing_row_count(path)


def append_snapshot(payload: dict) -> None:
    path = OUTPUT / "snapshots.csv"
    strategy = payload["strategy"]
    row = {
        "period_end": payload["latest_available_bar"],
        "generated_at": payload["generated_at"],
        "source_bars": payload["source_bars"],
        "strategy_return": strategy["strategy_return"],
        "benchmark_return": strategy["benchmark_return"],
        "excess": strategy["excess"],
        "strategy_drawdown": strategy["strategy_drawdown"],
        "benchmark_drawdown": strategy["benchmark_drawdown"],
        "maximum_intrabar_leverage": strategy["maximum_intrabar_leverage"],
        "fees": strategy["fees"],
        "funding": strategy["funding"],
        "target_exposure": payload["latest_target"]["target_exposure"],
        "target_change_count": len(payload["forward_target_changes"]),
    }
    append_rows_once(path, [row], key="period_end")


def append_rows_once(path: Path, rows: list[dict], *, key: str) -> None:
    if not rows:
        return
    existing = set()
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            existing = {row[key] for row in csv.DictReader(handle)}
    fields = list(rows[0])
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if path.stat().st_size == 0:
            writer.writeheader()
        for row in rows:
            if row[key] not in existing:
                writer.writerow(row)
                existing.add(row[key])


def existing_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def render(payload):
    lines = [
        "# BTC SMA11/40 Hybrid Forward Ledger",
        "",
        "参数在前向起点前冻结；不得根据本报告改变 SMA、确认周期、暴露或成本假设。",
        "",
        f"前向起点：{payload['forward_start']}。",
        f"最新完整数据：{payload['latest_available_bar']}。",
        "",
    ]
    if payload["status"] == "AWAITING_FORWARD_DATA":
        lines += ["状态：**AWAITING_FORWARD_DATA**。冻结边界之后尚无完整 15m K 线。", ""]
        return "\n".join(lines)
    strategy = payload["strategy"]
    moderate = payload["moderate_cost"]
    lines += [
        "| 指标 | 默认成本 | 20+10 bps | B&H |",
        "|---|---:|---:|---:|",
        f"| 收益 | {strategy['strategy_return']:.2%} | {moderate['strategy_return']:.2%} | "
        f"{strategy['benchmark_return']:.2%} |",
        f"| 超额 | {strategy['excess']:.2%} | {moderate['excess']:.2%} | - |",
        f"| 最大回撤 | {strategy['strategy_drawdown']:.2%} | {moderate['strategy_drawdown']:.2%} | "
        f"{strategy['benchmark_drawdown']:.2%} |",
        f"| 峰值有效杠杆 | {strategy['maximum_intrabar_leverage']:.3f}X | "
        f"{moderate['maximum_intrabar_leverage']:.3f}X | - |",
        "",
        f"最新目标：`{payload['latest_target']['target_exposure']}X`；"
        f"信号时间：{payload['latest_target']['signal_time']}。",
        f"前向目标变更数：{len(payload['forward_target_changes'])}。",
        f"已记录完整 UTC 日：{payload['completed_forward_days']}。",
        "",
        "状态：**FORWARD_OBSERVATION**。短样本不得用作参数调整依据。",
        "",
    ]
    return "\n".join(lines)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
