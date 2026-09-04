#!/usr/bin/env python3
"""Audit a stitched daily-SMA BTC candidate with 15m perpetual execution."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_spot_pre2020 import load_spot_bars, validate_daily_continuity
from research_btc_block_bootstrap import paired_daily_log_returns, run_bootstrap
from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT = Path("reports/experiments/btc_stitched_strict15m_sma10/2026-09-03")
FUTURES_START = datetime(2020, 1, 1, tzinfo=UTC)
EVALUATION_START = datetime(2017, 10, 1, tzinfo=UTC)
FAST = 10
SLOW = 40
ENTER_BEAR_DAYS = 2
EXIT_BEAR_DAYS = 1
ACTIVE = Decimal("1.5")
SPOT_CAP = Decimal("0.5")
FUTURES_CAP = Decimal("2")
MAINTENANCE = Decimal("0.02")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
BOOTSTRAP_BLOCKS = (7, 30, 90, 180, 365, 730)
COSTS = (
    ("default", Decimal("10"), Decimal("5")),
    ("moderate", Decimal("20"), Decimal("10")),
    ("stress", Decimal("50"), Decimal("25")),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", type=int, default=FAST)
    parser.add_argument("--slow", type=int, default=SLOW)
    parser.add_argument("--enter-bear-days", type=int, default=ENTER_BEAR_DAYS)
    parser.add_argument("--exit-bear-days", type=int, default=EXIT_BEAR_DAYS)
    parser.add_argument("--active", type=Decimal, default=ACTIVE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.fast <= 0 or args.slow <= args.fast or args.enter_bear_days <= 0:
        raise ValueError("SMA periods and bear confirmation must be positive and ordered")
    if args.exit_bear_days <= 0 or args.active <= 0:
        raise ValueError("recovery confirmation and active exposure must be positive")
    spot, futures, daily, target_indices, funding = load_hybrid_inputs()
    bars = spot + futures
    targets = map_targets(
        len(bars),
        target_indices,
        build_targets(
            daily,
            fast_period=args.fast,
            slow_period=args.slow,
            enter_bear_days=args.enter_bear_days,
            exit_bear_days=args.exit_bear_days,
            active=args.active,
        ),
    )
    bounds = periods(bars[-1].end_ms, spot[-1].end_ms)
    results = {}
    cost_sensitivity = {}
    for name, (start, end) in bounds.items():
        result = replay(bars, targets, funding, start, end, record_equity=name == "full")
        results[name] = public(result, benchmark(bars, start, end), start, end)
        cost_sensitivity[name] = {"default": results[name]}
        for label, fee_bps, slippage_bps in COSTS[1:]:
            stressed = replay(
                bars,
                targets,
                funding,
                start,
                end,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
            )
            cost_sensitivity[name][label] = public(
                stressed, benchmark(bars, start, end), start, end
            )

    full = replay(bars, targets, funding, *bounds["full"], record_equity=True)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars, full.equity_curve, 100_000.0, start_ms=bounds["full"][0]
    )
    bootstrap = {
        f"{block}d": run_bootstrap(
            strategy_logs,
            benchmark_logs,
            block_days=block,
            samples=10_000,
            seed=20263100 + block,
        )
        for block in BOOTSTRAP_BLOCKS
    }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": {
            "id": (
                f"stitched-daily-sma{args.fast}-{args.slow}-enter{args.enter_bear_days}"
                f"-exit{args.exit_bear_days}-active{args.active}"
            ),
            "fast_sma": args.fast,
            "slow_sma": args.slow,
            "enter_bear_after_days": args.enter_bear_days,
            "exit_bear_after_days": args.exit_bear_days,
            "active_exposure": str(args.active),
            "bear_exposure": "0",
        },
        "protocol": {
            "spot_history": "Binance BTCUSDT spot daily 2017-08 through 2019-12",
            "perpetual_history": "Binance USD-M BTCUSDT 15m from 2020-01 onward",
            "signal": "completed UTC daily SMA; no incomplete periods",
            "execution": "spot: next daily open; perpetual: next 15m open",
            "costs": "10 bps fee + 5 bps slippage per side; historical Funding on perpetual",
            "wallets": "50% spot and 50% isolated USD-M collateral",
            "hard_cap": "2X futures opening control; observed effective leverage must be <=3X",
        },
        "data": {
            "spot_daily_bars": len(spot),
            "perpetual_15m_bars": len(futures),
            "signal_daily_bars": len(daily),
            "first": iso(bars[0].start_ms),
            "last": iso(bars[-1].end_ms),
        },
        "periods": {name: [iso(start), iso(end)] for name, (start, end) in bounds.items()},
        "results": results,
        "cost_sensitivity": cost_sensitivity,
        "bootstrap": bootstrap,
        "hard_cap_passed": all(
            row["maximum_intrabar_leverage"] <= 3 and not row["liquidated"]
            for row in results.values()
        ),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "README.md").write_text(render(payload), encoding="utf-8")
    print(args.output / "README.md")


def load_hybrid_inputs():
    futures_start_ms = int(FUTURES_START.timestamp() * 1000)
    spot = [bar for bar in load_spot_bars() if bar.end_ms < futures_start_ms]
    validate_daily_continuity(spot)
    source = load_market("BTCUSDT")
    first_perpetual_index = next(
        index for index, bar in enumerate(source) if bar.start_ms >= futures_start_ms
    )
    futures = source[first_perpetual_index:]
    daily_source, source_ends = aggregate_complete_periods(source, "1d")
    daily_pairs = [
        (bar, end_index)
        for bar, end_index in zip(daily_source, source_ends, strict=True)
        if bar.start_ms >= futures_start_ms
    ]
    perpetual_daily = [bar for bar, _end_index in daily_pairs]
    daily = spot + perpetual_daily
    validate_daily_continuity(daily)
    target_indices = []
    for index in range(len(daily)):
        if index < len(spot):
            target_indices.append(index)
        else:
            _bar, source_end = daily_pairs[index - len(spot)]
            target_indices.append(len(spot) + source_end - first_perpetual_index)
    source_funding = funding_by_bar(source, load_funding("BTCUSDT", source))
    funding = [[] for _ in spot] + source_funding[first_perpetual_index:]
    return spot, futures, daily, tuple(target_indices), funding


def map_targets(source_count, target_indices, sparse_targets):
    if len(target_indices) != len(sparse_targets):
        raise ValueError("target indices and sparse targets differ in length")
    targets = [None] * source_count
    for index, target in zip(target_indices, sparse_targets, strict=True):
        if not 0 <= index < source_count:
            raise ValueError("target index is out of range")
        targets[index] = target
    return tuple(targets)


def build_targets(
    daily,
    *,
    fast_period=FAST,
    slow_period=SLOW,
    enter_bear_days=ENTER_BEAR_DAYS,
    exit_bear_days=EXIT_BEAR_DAYS,
    active=ACTIVE,
):
    fast = simple_moving_average(daily, fast_period)
    slow = simple_moving_average(daily, slow_period)
    state = None
    bear_count = recovery_count = 0
    output = []
    for index, bar in enumerate(daily):
        if fast[index] is None or slow[index] is None:
            output.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= enter_bear_days:
            state = "bear"
        elif state == "bear" and recovery_count >= exit_bear_days:
            state = "active"
        output.append(Decimal("0") if state == "bear" else active)
    return tuple(output)


def periods(last_end, spot_end):
    return {
        "spot_pre2020": (int(EVALUATION_START.timestamp() * 1000), spot_end),
        "research": (
            int(FUTURES_START.timestamp() * 1000),
            utc_ms(2022, 12, 31, 23, 59, 59, 999000),
        ),
        "validation": (utc_ms(2023), utc_ms(2024, 12, 31, 23, 59, 59, 999000)),
        "oos": (utc_ms(2025), last_end),
        "full": (int(EVALUATION_START.timestamp() * 1000), last_end),
    }


def replay(
    bars,
    targets,
    funding,
    start,
    end,
    *,
    record_equity=False,
    fee_bps=FEE_BPS,
    slippage_bps=SLIPPAGE_BPS,
):
    return replay_segregated(
        bars,
        targets,
        funding,
        start,
        end,
        spot_cap=SPOT_CAP,
        maintenance_rate=MAINTENANCE,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        record_equity=record_equity,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=FUTURES_CAP,
    )


def public(result, baseline, start, end):
    return {
        "strategy_return": result.net_return,
        "benchmark_return": baseline["net_return"],
        "excess": result.net_return - baseline["net_return"],
        "strategy_cagr": (1 + result.net_return) ** (1 / years_between(start, end)) - 1,
        "strategy_drawdown": result.max_drawdown,
        "benchmark_drawdown": baseline["max_drawdown"],
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def render(payload):
    lines = [
        f"# BTC {payload['candidate']['id']} Strict Perpetual-15m Audit",
        "",
        (
            "2017–2019 使用现货日线，2020 至今使用 USD-M 15m 回放；"
            "完成日线信号只在下一根可交易 bar 执行。"
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
        "## Cost Sensitivity",
        "",
        "| 区间 | 默认超额 | 20+10 bps 超额 | 50+25 bps 超额 |",
        "|---|---:|---:|---:|",
    ]
    for name, values in payload["cost_sensitivity"].items():
        lines.append(
            f"| {name} | {values['default']['excess']:.2%} | "
            f"{values['moderate']['excess']:.2%} | {values['stress']['excess']:.2%} |"
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
    lines += ["", "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。", ""]
    return "\n".join(lines)


def utc_ms(year, month=1, day=1, hour=0, minute=0, second=0, microsecond=0):
    return int(
        datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC).timestamp() * 1000
    )


def iso(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
