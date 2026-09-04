#!/usr/bin/env python3
"""Strict 15-minute execution audit for the frozen BTC SMA hysteresis candidate."""

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

OUTPUT = Path("reports/experiments/btc_hysteresis_15m_full/2026-09-02")
START_MS = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")


def main() -> None:
    bars = load_market("BTCUSDT")
    daily, source_ends = aggregate_complete_periods(bars, "1d")
    sparse_targets = build_targets(daily)
    targets = map_targets_to_source(len(bars), sparse_targets, source_ends)
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    periods = {
        "research": (START_MS, utc_ms(2022, 12, 31, 23, 59, 59, 999000)),
        "validation": (utc_ms(2023), utc_ms(2024, 12, 31, 23, 59, 59, 999000)),
        "oos": (utc_ms(2025), bars[-1].end_ms),
        "full": (START_MS, bars[-1].end_ms),
    }
    rows = {}
    for label, (start, end) in periods.items():
        result = replay(bars, targets, funding, start, end, record=label == "full")
        baseline = benchmark(bars, start, end)
        years = max((end - start) / (365.2425 * 86_400_000), 1 / 365.2425)
        rows[label] = {
            "strategy_return": result.net_return,
            "benchmark_return": baseline["net_return"],
            "excess": result.net_return - baseline["net_return"],
            "strategy_cagr": annualized(result.net_return, years),
            "benchmark_cagr": annualized(baseline["net_return"], years),
            "strategy_drawdown": result.max_drawdown,
            "benchmark_drawdown": baseline["max_drawdown"],
            "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
            "maximum_controlled_open_leverage": result.maximum_controlled_open_futures_leverage,
            "fees": result.total_fees,
            "funding": result.total_funding,
            "liquidated": result.liquidated,
        }
    full_result = replay(bars, targets, funding, START_MS, bars[-1].end_ms, record=True)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full_result.equity_curve, 100_000.0, start_ms=START_MS
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20260900 + block,
        )
        for block in (7, 30, 90)
    }
    yearly = {}
    first_year = datetime.fromtimestamp(START_MS / 1000, UTC).year
    last_year = datetime.fromtimestamp(bars[-1].end_ms / 1000, UTC).year
    for year in range(first_year, last_year + 1):
        left = max(START_MS, utc_ms(year))
        right = min(bars[-1].end_ms, utc_ms(year + 1) - 1)
        yearly[str(year)] = evaluate(bars, targets, funding, left, right)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": "daily SMA10/40 hysteresis; bear after 2 days; recovery after 1; active 1.25X",
        "protocol": {
            "signal": "completed UTC daily candle; target mapped to its final 15m bar",
            "execution": "target change executes at the next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical Funding",
            "spot_cap": "0.5",
            "maximum_futures_leverage": "3X",
            "lookahead": "none; incomplete daily periods are excluded",
        },
        "data": {
            "source_bars": len(bars),
            "complete_daily_bars": len(daily),
            "first": iso(bars[0].start_ms),
            "last_complete": iso(bars[-1].end_ms),
        },
        "periods": {name: [iso(left), iso(right)] for name, (left, right) in periods.items()},
        "results": rows,
        "bootstrap": bootstrap,
        "yearly": yearly,
        "hard_cap_passed": all(
            row["maximum_intrabar_leverage"] <= 3 and not row["liquidated"] for row in rows.values()
        ),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def build_targets(daily):
    fast = simple_moving_average(daily, 10)
    slow = simple_moving_average(daily, 40)
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
        elif state == "active" and bear_count >= 2:
            state = "bear"
        elif state == "bear" and recovery_count >= 1:
            state = "active"
        targets.append(Decimal("0") if state == "bear" else Decimal("1.25"))
    return tuple(targets)


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
    baseline = benchmark(bars, start, end)
    return {
        "strategy_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "strategy_drawdown": result.max_drawdown,
        "benchmark_drawdown": baseline["max_drawdown"],
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
    }


def utc_ms(year, month=1, day=1, hour=0, minute=0, second=0, microsecond=0):
    value = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC)
    return int(value.timestamp() * 1000)


def annualized(value, years):
    return (1 + value) ** (1 / years) - 1 if value > -1 else -1.0


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


def render(payload):
    lines = [
        "# BTC SMA10/40 迟滞策略严格 15m 执行复核",
        "",
        "日线只生成信号，实际目标变化在下一根 15m 开盘执行；不使用未完成日线或未来 K 线。",
        "",
        "| 区间 | 策略 | B&H | 超额 | 策略 CAGR | 策略 DD | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in payload["results"].items():
        lines.append(
            f"| {label} | {row['strategy_return']:.2%} | {row['benchmark_return']:.2%} | "
            f"{row['excess']:.2%} | {row['strategy_cagr']:.2%} | "
            f"{row['strategy_drawdown']:.2%} | {row['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        f"硬杠杆约束：{'通过' if payload['hard_cap_passed'] else '失败'}。",
        "",
        "## 逐年",
        "",
        "| 年份 | 策略 | B&H | 超额 | 策略 DD |",
        "|---:|---:|---:|---:|---:|",
        *[
            f"| {year} | {row['strategy_return']:.2%} | {row['benchmark_return']:.2%} | "
            f"{row['excess']:.2%} | {row['strategy_drawdown']:.2%} |"
            for year, row in payload["yearly"].items()
        ],
        "",
        f"逐年跑赢 B&H：{sum(row['excess'] > 0 for row in payload['yearly'].values())}/"
        f"{len(payload['yearly'])} 年。",
        "## Bootstrap",
        "",
        *[
            f"- {block}: 跑赢 B&H {value['probability_beats_bh_return']:.2%}；"
            f"年化超额 P05 {value['annualized_excess_vs_bh']['p05']:.2%}；"
            f"收益与 DD 同胜 {value['probability_beats_return_and_drawdown']:.2%}。"
            for block, value in payload["bootstrap"].items()
        ],
        "",
        "该复核比聚合日线回放更接近实际执行；Bootstrap 衡量历史路径敏感性，"
        "仍不能替代冻结后的长期前向观察。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
