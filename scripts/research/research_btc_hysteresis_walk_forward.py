#!/usr/bin/env python3
"""Causal annual walk-forward selection for a bounded BTC SMA hysteresis family."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_sma12_40 as base
from research_btc_collateral_architecture import replay_segregated

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_hysteresis_walk_forward/2026-09-02")
START_MS = base.START_MS
FUTURES_START_MS = base.FUTURES_START_MS
FAST_PERIODS = (6, 8, 10, 12, 15, 20)
SLOW = 40
ENTER_DAYS = (1, 2, 3, 5)
EXIT_DAYS = (1, 2, 3)
ACTIVE_EXPOSURES = (Decimal("1.0"), Decimal("1.25"))
SPOT_CAP = Decimal("0.5")
MAX_FUTURES_LEVERAGE = Decimal("3")
MAINTENANCE = Decimal("0.02")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
TEST_YEARS = tuple(range(2019, 2027))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-test-year", type=int, default=TEST_YEARS[0])
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    spot = [bar for bar in base.load_spot_bars() if bar.end_ms < FUTURES_START_MS]
    base.validate_daily_continuity(spot)
    futures_15m = base.load_market("BTCUSDT")
    futures, _ = aggregate_complete_periods(futures_15m, "1d")
    futures = [bar for bar in futures if bar.start_ms >= FUTURES_START_MS]
    bars = spot + futures
    base.validate_daily_continuity(bars)
    funding = [[] for _ in bars]
    funding[len(spot) :] = funding_by_bar(futures, base.load_funding("BTCUSDT", futures_15m))
    candidates = []
    for fast in FAST_PERIODS:
        for enter in ENTER_DAYS:
            for exit_days in EXIT_DAYS:
                for active in ACTIVE_EXPOSURES:
                    candidates.append(
                        {
                            "id": f"sma{fast}-40-enter{enter}-exit{exit_days}-active{active}",
                            "fast": fast,
                            "enter": enter,
                            "exit": exit_days,
                            "active": active,
                            "targets": build_targets(bars, fast, enter, exit_days, active),
                        }
                    )
    rows = []
    test_years = tuple(range(args.start_test_year, TEST_YEARS[-1] + 1))
    for year in test_years:
        test_start = utc_ms(year)
        test_end = min(utc_ms(year + 1) - 1, bars[-1].end_ms)
        train_years = [item for item in range(2018, year) if utc_ms(item) <= bars[-1].end_ms]
        scored = []
        for candidate in candidates:
            train_rows = []
            for train_year in train_years:
                left = max(START_MS, utc_ms(train_year))
                right = min(utc_ms(train_year + 1) - 1, bars[-1].end_ms)
                result = replay(bars, candidate["targets"], funding, left, right)
                benchmark = base.benchmark(bars, left, right)
                train_rows.append(
                    {
                        "year": train_year,
                        "excess": result.net_return - benchmark["net_return"],
                        "strategy_return": result.net_return,
                        "benchmark_return": benchmark["net_return"],
                        "drawdown": result.max_drawdown,
                        "liquidated": result.liquidated,
                    }
                )
            eligible = [item for item in train_rows if not item["liquidated"]]
            score = min((item["excess"] for item in eligible), default=-999.0)
            median = sorted(item["excess"] for item in eligible)
            scored.append(
                {
                    "id": candidate["id"],
                    "fast": candidate["fast"],
                    "enter": candidate["enter"],
                    "exit": candidate["exit"],
                    "active": str(candidate["active"]),
                    "development_score": score,
                    "development_median_excess": median[len(median) // 2] if median else -999.0,
                    "development_rows": train_rows,
                }
            )
        selected = max(
            scored,
            key=lambda item: (
                item["development_score"],
                item["development_median_excess"],
            ),
        )
        chosen = next(item for item in candidates if item["id"] == selected["id"])
        result = replay(bars, chosen["targets"], funding, test_start, test_end)
        benchmark = base.benchmark(bars, test_start, test_end)
        rows.append(
            {
                "year": year,
                "training_years": train_years,
                "selected": selected,
                "test": {
                    "strategy_return": result.net_return,
                    "benchmark_return": benchmark["net_return"],
                    "excess": result.net_return - benchmark["net_return"],
                    "strategy_drawdown": result.max_drawdown,
                    "benchmark_drawdown": benchmark["max_drawdown"],
                    "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
                    "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                    "liquidated": result.liquidated,
                },
            }
        )
        print(year, selected["id"], flush=True)
    strategy_growth = 1.0
    benchmark_growth = 1.0
    for row in rows:
        strategy_growth *= 1 + row["test"]["strategy_return"]
        benchmark_growth *= 1 + row["test"]["benchmark_return"]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "protocol": {
            "selection": "each year selected only from prior complete years",
            "candidate_grid": {
                "fast": FAST_PERIODS,
                "slow": SLOW,
                "enter_bear_days": ENTER_DAYS,
                "exit_bear_days": EXIT_DAYS,
                "active_exposures": [str(value) for value in ACTIVE_EXPOSURES],
            },
            "signal": "completed daily candle; next bar execution",
            "costs": "10 bps fee + 5 bps slippage; historical Funding",
            "hard_effective_leverage_cap": "3X",
        },
        "data": {"combined_bars": len(bars), "last": base.iso(bars[-1].end_ms)},
        "candidate_count": len(candidates),
        "test_years": test_years,
        "years": rows,
        "compound_strategy_return": strategy_growth - 1,
        "compound_benchmark_return": benchmark_growth - 1,
        "years_beating_bh": sum(row["test"]["excess"] > 0 for row in rows),
        "years_with_better_drawdown": sum(
            row["test"]["strategy_drawdown"] >= row["test"]["benchmark_drawdown"] for row in rows
        ),
        "hard_cap_passed": all(
            row["test"]["maximum_intrabar_leverage"] <= 3 and not row["test"]["liquidated"]
            for row in rows
        ),
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(render(payload), encoding="utf-8")
    print(output_dir / "README.md")


def build_targets(bars, fast_period, enter_days, exit_days, active):
    fast = simple_moving_average(bars, fast_period)
    slow = simple_moving_average(bars, SLOW)
    state = None
    bear_count = 0
    recovery_count = 0
    output = []
    for index, bar in enumerate(bars):
        if fast[index] is None or slow[index] is None:
            output.append(None)
            continue
        bearish = bar.close < slow[index] and fast[index] < slow[index]
        bear_count = bear_count + 1 if bearish else 0
        recovery_count = recovery_count + 1 if not bearish else 0
        if state is None:
            state = "bear" if bearish else "active"
        elif state == "active" and bear_count >= enter_days:
            state = "bear"
        elif state == "bear" and recovery_count >= exit_days:
            state = "active"
        output.append(Decimal("0") if state == "bear" else active)
    return tuple(output)


def replay(bars, targets, funding, start_ms, end_ms):
    return replay_segregated(
        bars,
        targets,
        funding,
        start_ms,
        end_ms,
        spot_cap=SPOT_CAP,
        maintenance_rate=MAINTENANCE,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=MAX_FUTURES_LEVERAGE,
    )


def utc_ms(year: int) -> int:
    return int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000)


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC SMA Hysteresis Annual Walk-Forward (Strict 3X)",
        "",
        "每个测试年份只使用此前完整年度选择参数；测试年份完全留出。",
        "",
        "| 测试年 | 训练选择 | 策略 | B&H | 超额 | 策略DD | 最高盘中杠杆 |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["years"]:
        test = row["test"]
        selected = row["selected"]
        config = (
            f"SMA{selected['fast']}/40-e{selected['enter']}/"
            f"x{selected['exit']}-a{selected['active']}"
        )
        lines.append(
            f"| {row['year']} | `{config}` | {pct(test['strategy_return'])} | "
            f"{pct(test['benchmark_return'])} | {pct(test['excess'])} | "
            f"{pct(test['strategy_drawdown'])} | {test['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        f"Walk-Forward 复合收益：{pct(payload['compound_strategy_return'])}；"
        f"B&H：{pct(payload['compound_benchmark_return'])}。",
        f"胜过 B&H：{payload['years_beating_bh']}/{len(payload['years'])} 年；"
        f"回撤更优：{payload['years_with_better_drawdown']}/{len(payload['years'])} 年。",
        f"硬杠杆约束：{'通过' if payload['hard_cap_passed'] else '失败'}。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
