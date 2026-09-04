#!/usr/bin/env python3
"""Compare the fixed BTC hysteresis candidate with equally leveraged B&H baselines."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_sma12_40 as base
from research_btc_collateral_architecture import replay_segregated

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_hysteresis_leverage_benchmark/2026-09-02")
START_MS = base.START_MS
FUTURES_START_MS = base.FUTURES_START_MS
FAST = 10
SLOW = 40
ENTER_BEAR_DAYS = 2
EXIT_BEAR_DAYS = 1
ACTIVE = Decimal("1.25")
SPOT_CAP = Decimal("0.5")
MAX_FUTURES_LEVERAGE = Decimal("3")
MAINTENANCE = Decimal("0.02")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
BENCHMARK_EXPOSURES = (Decimal("1"), Decimal("1.25"), Decimal("1.5"))


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    spot = [bar for bar in base.load_spot_bars() if bar.end_ms < FUTURES_START_MS]
    base.validate_daily_continuity(spot)
    futures_15m = base.load_market("BTCUSDT")
    futures, _ = aggregate_complete_periods(futures_15m, "1d")
    futures = [bar for bar in futures if bar.start_ms >= FUTURES_START_MS]
    bars = spot + futures
    base.validate_daily_continuity(bars)
    funding = [[] for _ in bars]
    funding[len(spot) :] = funding_by_bar(futures, base.load_funding("BTCUSDT", futures_15m))
    periods = period_bounds(bars[-1].end_ms, spot[-1].end_ms)
    candidate_targets = build_candidate_targets(bars)
    all_targets = {"hysteresis_1.25x": candidate_targets}
    all_targets.update(
        {
            f"buy_hold_{exposure}x": constant_targets(bars, exposure)
            for exposure in BENCHMARK_EXPOSURES
        }
    )
    rows = {}
    for name, targets in all_targets.items():
        rows[name] = {}
        for period, bounds in periods.items():
            result = replay(bars, targets, funding, *bounds)
            rows[name][period] = {
                "return": result.net_return,
                "drawdown": result.max_drawdown,
                "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
                "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                "fees": result.total_fees,
                "funding": result.total_funding,
                "liquidated": result.liquidated,
            }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": "SMA10/40 hysteresis, enter bear after 2 days, exit after 1, active 1.25X",
        "protocol": {
            "signal": "completed daily candle; next bar execution",
            "costs": "10 bps fee + 5 bps slippage",
            "funding": "historical Funding on actual futures notional",
            "hard_effective_leverage_cap": "3X",
            "benchmark_note": "B&H baselines use the same segregated collateral and costs",
        },
        "data": {"combined_bars": len(bars), "last": base.iso(bars[-1].end_ms)},
        "periods": {
            name: [base.iso(left), base.iso(right)] for name, (left, right) in periods.items()
        },
        "rows": rows,
        "candidate_hard_cap_passed": all(
            period["maximum_intrabar_leverage"] <= 3 and not period["liquidated"]
            for period in rows["hysteresis_1.25x"].values()
        ),
        "passive_baseline_hard_cap_passed": {
            name: all(
                period["maximum_intrabar_leverage"] <= 3 and not period["liquidated"]
                for period in periods.values()
            )
            for name, periods in rows.items()
            if name != "hysteresis_1.25x"
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def build_candidate_targets(bars):
    fast = simple_moving_average(bars, FAST)
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
        elif state == "active" and bear_count >= ENTER_BEAR_DAYS:
            state = "bear"
        elif state == "bear" and recovery_count >= EXIT_BEAR_DAYS:
            state = "active"
        output.append(Decimal("0") if state == "bear" else ACTIVE)
    return tuple(output)


def constant_targets(bars, exposure):
    return tuple(exposure for _ in bars)


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


def period_bounds(last_end, spot_end):
    return {
        "spot_pre2020": (START_MS, spot_end),
        "2020_2022": (
            FUTURES_START_MS,
            int(datetime(2022, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
        ),
        "2023_2024": (
            int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2024, 12, 31, 23, 59, 59, 999000, tzinfo=UTC).timestamp() * 1000),
        ),
        "2025_latest": (int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000), last_end),
        "stitched_full": (START_MS, last_end),
    }


def pct(value):
    return f"{value:.2%}"


def render(payload):
    lines = [
        "# BTC Hysteresis vs Equal-Leverage B&H",
        "",
        "候选与 1X、1.25X、1.5X 被动买入持有使用相同抵押结构、手续费、滑点和 Funding。",
        "",
        "| 策略 | Full收益 | Full DD | 2023-24收益 | 2025+收益 | Full最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, rows in payload["rows"].items():
        full = rows["stitched_full"]
        v = rows["2023_2024"]
        oos = rows["2025_latest"]
        lines.append(
            f"| {name} | {pct(full['return'])} | {pct(full['drawdown'])} | "
            f"{pct(v['return'])} | {pct(oos['return'])} | "
            f"{full['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        (f"候选硬杠杆约束：{'通过' if payload['candidate_hard_cap_passed'] else '失败'}。"),
        "被动杠杆基准的越界状态单独记录在 results.json；越界基准不应视为合法 3X 策略。",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
