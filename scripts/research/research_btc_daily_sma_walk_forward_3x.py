#!/usr/bin/env python3
"""Evaluate daily SMA long/short candidates with causal annual walk-forward selection."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_daily_sma_walk_forward_3x/2026-09-02")
START_YEAR = 2020
TEST_YEARS = tuple(range(2022, 2027))
SPOT_CAP = Decimal("0.5")
BULL_EXPOSURE = Decimal("1.5")
LEVERAGE_CAP = Decimal("3")
BEAR_EXPOSURES = (Decimal("-0.1"), Decimal("0"), Decimal("0.2"))
SMA_PAIRS = ((7, 35), (8, 40), (9, 45), (10, 50))


def utc_ms(year: int, month: int = 1, day: int = 1) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def targets_for(daily, ends, source_count, fast, slow, bear):
    fast_sma = simple_moving_average(daily, fast)
    slow_sma = simple_moving_average(daily, slow)
    dense = []
    for index, bar in enumerate(daily):
        if fast_sma[index] is None or slow_sma[index] is None:
            dense.append(None)
        elif bar.close < slow_sma[index] and fast_sma[index] < slow_sma[index]:
            dense.append(bear)
        else:
            dense.append(BULL_EXPOSURE)
    return map_targets_to_source(source_count, tuple(dense), ends)


def replay(bars, funding, targets, start_ms, end_ms):
    return replay_segregated(
        bars,
        targets,
        funding,
        start_ms,
        end_ms,
        spot_cap=SPOT_CAP,
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        enforce_effective_leverage_cap=True,
        maximum_futures_leverage=LEVERAGE_CAP,
    )


def candidate_id(pair, bear):
    return f"daily-sma-{pair[0]}-{pair[1]}-bear{bear}x-bull{BULL_EXPOSURE}x"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    daily, ends = aggregate_complete_periods(bars, "1d")
    candidates = []
    for pair in SMA_PAIRS:
        for bear in BEAR_EXPOSURES:
            candidates.append(
                {
                    "id": candidate_id(pair, bear),
                    "pair": pair,
                    "bear": bear,
                    "targets": targets_for(daily, ends, len(bars), *pair, bear),
                }
            )

    rows = []
    for year in TEST_YEARS:
        train_start = utc_ms(START_YEAR)
        train_end = utc_ms(year) - 1
        test_start = utc_ms(year)
        test_end = min(utc_ms(year + 1) - 1, bars[-1].end_ms)
        train_benchmark = benchmark(bars, train_start, train_end)
        scored = []
        for candidate in candidates:
            result = replay(bars, funding, candidate["targets"], train_start, train_end)
            scored.append(
                {
                    "id": candidate["id"],
                    "pair": candidate["pair"],
                    "bear_exposure": str(candidate["bear"]),
                    "stress_return": result.net_return,
                    "stress_excess": result.net_return - train_benchmark["net_return"],
                    "max_drawdown": result.max_drawdown,
                    "liquidated": result.liquidated,
                }
            )
        eligible = [row for row in scored if not row["liquidated"]]
        selected = max(eligible, key=lambda row: (row["stress_excess"], row["max_drawdown"]))
        selected_candidate = next(item for item in candidates if item["id"] == selected["id"])
        test_result = replay(bars, funding, selected_candidate["targets"], test_start, test_end)
        test_benchmark = benchmark(bars, test_start, test_end)
        rows.append(
            {
                "year": year,
                "training_end": datetime.fromtimestamp(train_end / 1000, UTC).isoformat(),
                "selected": selected,
                "test": {
                    "strategy_return": test_result.net_return,
                    "benchmark_return": test_benchmark["net_return"],
                    "excess": test_result.net_return - test_benchmark["net_return"],
                    "strategy_drawdown": test_result.max_drawdown,
                    "benchmark_drawdown": test_benchmark["max_drawdown"],
                    "liquidated": test_result.liquidated,
                    "maximum_open_leverage": test_result.maximum_controlled_open_futures_leverage,
                    "maximum_intrabar_leverage": test_result.maximum_observed_futures_leverage,
                },
                "top_training_candidates": sorted(
                    scored, key=lambda row: row["stress_excess"], reverse=True
                )[:5],
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
        "status": "RESEARCH_ONLY",
        "protocol": {
            "selection": "each year selected only from 2020 through the prior year",
            "candidate_grid": {
                "sma_pairs": SMA_PAIRS,
                "bear_exposures": [str(value) for value in BEAR_EXPOSURES],
                "bull_exposure": str(BULL_EXPOSURE),
            },
            "signal": "completed UTC daily close; next 15m open",
            "costs": "10 bps fee + 5 bps slippage",
            "funding": "historical funding on actual futures notional",
            "leverage": "3x maximum futures-wallet leverage with active open control",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": bars[-1].end_ms},
        "years": rows,
        "compound_strategy_return": strategy_growth - 1,
        "compound_benchmark_return": benchmark_growth - 1,
        "years_beating_bh": sum(row["test"]["excess"] > 0 for row in rows),
        "years_with_better_drawdown": sum(
            row["test"]["strategy_drawdown"] >= row["test"]["benchmark_drawdown"] for row in rows
        ),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def pct(value: float) -> str:
    return f"{value:.2%}"


def markdown(payload: dict) -> str:
    lines = [
        "# BTC 日线 SMA 多空年度 Walk‑Forward（严格 3X）",
        "",
        "每个测试年份只使用此前历史选择 SMA 与熊市暴露；测试年完全不参与选参。",
        "",
        "| 年份 | 训练选择 | 策略 | B&H | 超额 | 策略DD | B&H DD | 盘中杠杆 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["years"]:
        test = row["test"]
        lines.append(
            f"| {row['year']} | `{row['selected']['id']}` | "
            f"{pct(test['strategy_return'])} | {pct(test['benchmark_return'])} | "
            f"{pct(test['excess'])} | {pct(test['strategy_drawdown'])} | "
            f"{pct(test['benchmark_drawdown'])} | {test['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        f"Walk‑Forward 复合收益：{pct(payload['compound_strategy_return'])}；"
        f"B&H：{pct(payload['compound_benchmark_return'])}。",
        f"胜过 B&H 年数：{payload['years_beating_bh']}/{len(payload['years'])}；"
        f"回撤更优年数：{payload['years_with_better_drawdown']}/{len(payload['years'])}。",
        "",
        "结果仍需新鲜 forward observation；年度样本数量有限，不能视为统计显著性证明。",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
