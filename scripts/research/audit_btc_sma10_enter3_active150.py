#!/usr/bin/env python3
"""Strict 15m audit for the pre-registered SMA10/40 enter-3 candidate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_hysteresis_15m_full as strict

from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_sma10_enter3_active150_strict/2026-09-03")
FAST = 10
SLOW = 40
ENTER_BEAR_DAYS = 3
EXIT_BEAR_DAYS = 1
ACTIVE = Decimal("1.5")


def main() -> None:
    bars = strict.load_market("BTCUSDT")
    daily, source_ends = aggregate_complete_periods(bars, "1d")
    sparse = build_targets(daily)
    targets = map_targets_to_source(len(bars), sparse, source_ends)
    funding = strict.funding_by_bar(bars, strict.load_funding("BTCUSDT", bars))
    periods = {
        "research": (strict.START_MS, strict.utc_ms(2022, 12, 31, 23, 59, 59, 999000)),
        "validation": (strict.utc_ms(2023), strict.utc_ms(2024, 12, 31, 23, 59, 59, 999000)),
        "oos": (strict.utc_ms(2025), bars[-1].end_ms),
        "full": (strict.START_MS, bars[-1].end_ms),
    }
    rows = {}
    for name, bounds in periods.items():
        result = strict.replay(bars, targets, funding, *bounds, record=name == "full")
        benchmark = strict.benchmark(bars, *bounds)
        years = max((bounds[1] - bounds[0]) / (365.2425 * 86_400_000), 1 / 365.2425)
        rows[name] = public(result, benchmark, years)

    full = strict.replay(bars, targets, funding, *periods["full"], record=True)
    strategy_logs, benchmark_logs = strict.paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=periods["full"][0]
    )
    bootstrap = {
        f"{block}d": strict.run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20263000 + block,
        )
        for block in (7, 30, 90)
    }
    yearly = {}
    for year in range(2020, datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).year + 1):
        left = max(strict.START_MS, strict.utc_ms(year))
        right = min(bars[-1].end_ms, strict.utc_ms(year + 1) - 1)
        result = strict.replay(bars, targets, funding, left, right)
        benchmark = strict.benchmark(bars, left, right)
        yearly[str(year)] = public(result, benchmark, 1)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": {
            "id": "daily-sma10-40-enter3-exit1-active1.5",
            "fast_sma": FAST,
            "slow_sma": SLOW,
            "enter_bear_after_days": ENTER_BEAR_DAYS,
            "exit_bear_after_days": EXIT_BEAR_DAYS,
            "active_exposure": str(ACTIVE),
            "bear_exposure": "0",
            "selection": "fixed from the predeclared exposure/cost grid; OOS excluded",
        },
        "protocol": {
            "signal": "completed UTC daily candle; target mapped to next 15m open",
            "wallets": "50% spot and 50% isolated USD-M collateral",
            "costs": "10 bps fee + 5 bps slippage per side; historical Funding",
            "hard_effective_leverage_cap": "3X",
            "lookahead": "none; only completed daily bars are used",
        },
        "data": {
            "source_bars": len(bars),
            "complete_daily_bars": len(daily),
            "first": strict.iso(bars[0].start_ms),
            "last_complete": strict.iso(bars[-1].end_ms),
        },
        "periods": {
            name: [strict.iso(left), strict.iso(right)] for name, (left, right) in periods.items()
        },
        "results": rows,
        "yearly": yearly,
        "bootstrap": bootstrap,
        "hard_cap_passed": all(
            row["maximum_intrabar_leverage"] <= 3 and not row["liquidated"] for row in rows.values()
        ),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def build_targets(daily):
    fast = simple_moving_average(daily, FAST)
    slow = simple_moving_average(daily, SLOW)
    state = None
    bear_count = recovery_count = 0
    targets = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            targets.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= ENTER_BEAR_DAYS:
            state = "bear"
        elif state == "bear" and recovery_count >= EXIT_BEAR_DAYS:
            state = "active"
        targets.append(Decimal("0") if state == "bear" else ACTIVE)
    return tuple(targets)


def public(result, benchmark, years):
    return {
        "strategy_return": result.net_return,
        "benchmark_return": benchmark["net_return"],
        "excess": result.net_return - benchmark["net_return"],
        "strategy_cagr": (
            (1 + result.net_return) ** (1 / years) - 1 if result.net_return > -1 else -1
        ),
        "strategy_drawdown": result.max_drawdown,
        "benchmark_drawdown": benchmark["max_drawdown"],
        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def render(payload):
    lines = [
        "# BTC SMA10/40 Enter-3 Active-1.5 Strict 15m Audit",
        "",
        (
            "连续 3 根 bearish 日线进入 0X，连续 1 根 non-bearish 日线恢复 1.5X；"
            "目标变化在下一根 15m 开盘执行。"
        ),
        "",
        "| 区间 | 策略 | B&H | 超额 | CAGR | DD | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["results"].items():
        lines.append(
            f"| {name} | {row['strategy_return']:.2%} | {row['benchmark_return']:.2%} | "
            f"{row['excess']:.2%} | {row['strategy_cagr']:.2%} | "
            f"{row['strategy_drawdown']:.2%} | {row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        f"硬杠杆约束：{'通过' if payload['hard_cap_passed'] else '失败'}。",
        "",
        "## Bootstrap",
        "",
    ]
    for block, value in payload["bootstrap"].items():
        lines.append(
            f"- {block}: 跑赢 B&H {value['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {value['annualized_excess_vs_bh']['p05']:.2%}；"
            f"收益与 DD 同胜 {value['probability_beats_return_and_drawdown']:.2%}。"
        )
    lines += ["", "## Yearly", "", "| 年份 | 策略 | B&H | 超额 |", "|---:|---:|---:|---:|"]
    for year, row in payload["yearly"].items():
        lines.append(
            f"| {year} | {row['strategy_return']:.2%} | {row['benchmark_return']:.2%} | "
            f"{row['excess']:.2%} |"
        )
    lines += ["", "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
