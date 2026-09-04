#!/usr/bin/env python3
"""Select a bounded BTC SMA hysteresis candidate using strict 15m execution."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_strict15m_hysteresis_grid/2026-09-02")
FAST_PERIODS = (6, 8, 10, 12, 15, 20)
EXPOSURES = (Decimal("1"), Decimal("1.25"), Decimal("1.5"))
START_MS = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")


def main() -> None:
    bars = load_market("BTCUSDT")
    daily, ends = aggregate_complete_periods(bars, "1d")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    periods = {
        "train_2020_2022": (START_MS, utc_ms(2022, 12, 31, 23, 59, 59)),
        "validation_2023_2024": (utc_ms(2023), utc_ms(2024, 12, 31, 23, 59, 59)),
        "oos_2025_latest": (utc_ms(2025), bars[-1].end_ms),
        "full": (START_MS, bars[-1].end_ms),
    }
    rows = []
    for fast in FAST_PERIODS:
        signal = hysteresis_targets(daily, fast)
        mapped = map_targets_to_source(len(bars), signal, ends)
        for exposure in EXPOSURES:
            targets = tuple(
                None if value is None else exposure if value else Decimal("0") for value in mapped
            )
            result = {
                name: evaluate(bars, targets, funding, *bounds) for name, bounds in periods.items()
            }
            train_score = min(
                result[name]["excess"] for name in ("train_2020_2022", "validation_2023_2024")
            )
            rows.append(
                {
                    "id": f"sma{fast}/40-active{exposure}",
                    "fast": fast,
                    "active": str(exposure),
                    "train_score": train_score,
                    "periods": result,
                }
            )
    selected = max(
        rows,
        key=lambda row: (
            row["train_score"],
            row["periods"]["validation_2023_2024"]["excess"],
        ),
    )
    selected_signal = map_targets_to_source(
        len(bars), hysteresis_targets(daily, selected["fast"]), ends
    )
    selected_targets = tuple(
        None if value is None else Decimal(selected["active"]) if value else Decimal("0")
        for value in selected_signal
    )
    full = replay(bars, selected_targets, funding, *periods["full"], record=True)
    logs, bh_logs = paired_daily_log_returns(bars, full.equity_curve, 100_000.0, start_ms=START_MS)
    bootstrap = {
        f"{block}d": run_bootstrap(
            logs, bh_logs, block_days=block, samples=10_000, seed=20262000 + block
        )
        for block in (7, 30, 90)
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE",
        "selection": "maximize the worst excess across 2020-2022 and 2023-2024 only",
        "candidate_count": len(rows),
        "selected": selected,
        "all_candidates": rows,
        "bootstrap": bootstrap,
        "protocol": {
            "signal": "completed daily SMA10/40-family candle; next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical Funding",
            "spot_cap": "0.5",
            "effective_leverage_cap": "3X",
            "oos_note": "2025+ has been viewed in prior research and is not a clean blind OOS",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "hard_cap_passed": all(
            row["periods"]["full"]["maximum_intrabar_leverage"] <= 3
            and not row["periods"]["full"]["liquidated"]
            for row in rows
        ),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def hysteresis_targets(daily, fast_period):
    fast = simple_moving_average(daily, fast_period)
    slow = simple_moving_average(daily, 40)
    state = None
    bear_count = recovery_count = 0
    output = []
    for i, bar in enumerate(daily):
        if fast[i] is None or slow[i] is None:
            output.append(None)
            continue
        bearish = bar.close < slow[i] and fast[i] < slow[i]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= 2:
            state = "bear"
        elif state == "bear" and recovery_count >= 1:
            state = "active"
        output.append(Decimal("0") if state == "bear" else Decimal("1"))
    return tuple(output)


def replay(bars, targets, funding, start, end, *, record=False):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=Decimal("0.5"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        record_equity=record,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=Decimal("3"),
    )


def evaluate(bars, targets, funding, start, end):
    result = replay(bars, targets, funding, start, end)
    bh = benchmark(bars, start, end)
    return {
        "strategy_return": result.net_return,
        "benchmark_return": bh["net_return"],
        "excess": result.net_return - bh["net_return"],
        "strategy_drawdown": result.max_drawdown,
        "benchmark_drawdown": bh["max_drawdown"],
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
    }


def utc_ms(year, month=1, day=1, hour=0, minute=0, second=0):
    value = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    return int(value.timestamp() * 1000)


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def render(payload):
    selected = payload["selected"]
    lines = [
        "# BTC 严格 15m SMA 迟滞网格 Challenger",
        "",
        "只用 2020–2024 选择 SMA 快线和 active exposure；2025–最新仅作验证。"
        "由于历史研究已查看过该区间，不能称为盲测 OOS。",
        "",
        f"选择结果：`{selected['id']}`，训练/验证最差超额 {selected['train_score']:.2%}。",
        "",
        "| 区间 | 策略 | B&H | 超额 | DD | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, row in selected["periods"].items():
        lines.append(
            f"| {name} | {row['strategy_return']:.2%} | {row['benchmark_return']:.2%} | "
            f"{row['excess']:.2%} | {row['strategy_drawdown']:.2%} | "
            f"{row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += ["", "## 严格 15m Bootstrap"]
    lines += [
        f"- {name}: 跑赢 B&H {row['probability_beats_bh_return']:.2%}；"
        f"年化超额 P05 {row['annualized_excess_vs_bh']['p05']:.2%}。"
        for name, row in payload["bootstrap"].items()
    ]
    lines += [
        "",
        f"严格 3X 审计：{'通过' if payload['hard_cap_passed'] else '失败'}。",
        "状态：**RESEARCH_ONLY / CHALLENGER_REQUIRES_NEW_FORWARD_FREEZE**。",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
