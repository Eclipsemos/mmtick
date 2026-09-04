#!/usr/bin/env python3
"""Research a causal volatility guard layered on fixed SMA10/40 hysteresis."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_sma12_40 as base
from research_btc_collateral_architecture import replay_segregated

from mastermind_tick.bar_research import funding_by_bar, wilder_atr_values
from mastermind_tick.sma_trend import aggregate_complete_periods
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_hysteresis_vol_guard/2026-09-02")
START_MS = base.START_MS
FUTURES_START_MS = base.FUTURES_START_MS
FAST = 10
SLOW = 40
ENTER_BEAR_DAYS = 2
EXIT_BEAR_DAYS = 1
CALM_ACTIVE = Decimal("1.25")
VOLATILE_EXPOSURES = tuple(Decimal(value) for value in ("0", "0.5", "0.75", "1.0"))
LOOKBACKS = (60, 120)
QUANTILES = (Decimal("0.75"), Decimal("0.9"))
SPOT_CAP = Decimal("0.5")
MAX_FUTURES_LEVERAGE = Decimal("3")
MAINTENANCE = Decimal("0.02")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")


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
    vol_ratio = atr_price_ratio(bars)
    rows = []
    for lookback in LOOKBACKS:
        for quantile in QUANTILES:
            thresholds = rolling_thresholds(vol_ratio, lookback, quantile)
            for volatile in VOLATILE_EXPOSURES:
                targets = build_targets(bars, vol_ratio, thresholds, volatile)
                metrics = {}
                for name, bounds in periods.items():
                    result = replay(bars, targets, funding, *bounds)
                    benchmark = base.benchmark(bars, *bounds)
                    metrics[name] = {
                        "strategy_return": result.net_return,
                        "benchmark_return": benchmark["net_return"],
                        "excess": result.net_return - benchmark["net_return"],
                        "strategy_drawdown": result.max_drawdown,
                        "benchmark_drawdown": benchmark["max_drawdown"],
                        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
                        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                        "liquidated": result.liquidated,
                    }
                development = min(
                    metrics[name]["excess"] for name in ("spot_pre2020", "2020_2022", "2023_2024")
                )
                rows.append(
                    {
                        "lookback": lookback,
                        "quantile": str(quantile),
                        "volatile_exposure": str(volatile),
                        "development_score": development,
                        "metrics": metrics,
                    }
                )
    rows.sort(key=lambda row: row["development_score"], reverse=True)
    valid = [
        row
        for row in rows
        if row["metrics"]["stitched_full"]["maximum_intrabar_leverage"] <= 3
        and not row["metrics"]["stitched_full"]["liquidated"]
    ]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED",
        "candidate": {
            "base_signal": "SMA10/40 hysteresis: enter bear after 2 bearish days, exit after 1",
            "calm_active_exposure": str(CALM_ACTIVE),
            "selection": (
                "ranked by worst excess across pre-2025 development segments; OOS excluded"
            ),
        },
        "protocol": {
            "data": "Binance spot 2017-2019 stitched to USD-M 2020-latest",
            "signal": "completed daily candle; next bar execution",
            "volatility": (
                "Wilder ATR(14)/close; rolling quantile uses values available through current bar"
            ),
            "costs": "10 bps fee + 5 bps slippage; historical Funding on futures segment",
            "hard_effective_leverage_cap": "3X",
        },
        "data": {
            "spot_bars": len(spot),
            "futures_daily_bars": len(futures),
            "combined_bars": len(bars),
            "last": base.iso(bars[-1].end_ms),
        },
        "periods": {
            name: [base.iso(left), base.iso(right)] for name, (left, right) in periods.items()
        },
        "candidate_count": len(rows),
        "valid_under_hard_cap": len(valid),
        "selected_valid_development": valid[0] if valid else None,
        "top_valid": valid[:20],
        "all_rows": rows,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def atr_price_ratio(bars):
    atr = wilder_atr_values(bars, 14)
    return tuple(
        None if value is None or bar.close <= 0 else value / bar.close
        for bar, value in zip(bars, atr, strict=True)
    )


def rolling_thresholds(values, lookback, quantile):
    thresholds = []
    for index, value in enumerate(values):
        if value is None:
            thresholds.append(None)
            continue
        sample = [
            item for item in values[max(0, index - lookback + 1) : index + 1] if item is not None
        ]
        if len(sample) < lookback:
            thresholds.append(None)
            continue
        sample.sort()
        position = int((len(sample) - 1) * float(quantile))
        thresholds.append(sample[position])
    return tuple(thresholds)


def build_targets(bars, vol_ratio, thresholds, volatile_exposure):
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
        if state == "bear":
            output.append(Decimal("0"))
        elif thresholds[index] is not None and vol_ratio[index] > thresholds[index]:
            output.append(volatile_exposure)
        else:
            output.append(CALM_ACTIVE)
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
        "# BTC SMA10/40 Hysteresis Volatility Guard (Hard 3X)",
        "",
        (
            "当 ATR(14)/收盘价进入过去 lookback 日的高分位数时，"
            "将主动暴露从 1.25X 降低；熊市状态仍为 0X。"
        ),
        "",
        "| 配置 | 开发最差超额 | 2025+超额 | Full超额 | Full DD | 最高盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["top_valid"]:
        full = row["metrics"]["stitched_full"]
        oos = row["metrics"]["2025_latest"]
        lines.append(
            f"| look{row['lookback']}/q{row['quantile']}/volatile{row['volatile_exposure']}X | "
            f"{pct(row['development_score'])} | {pct(oos['excess'])} | {pct(full['excess'])} | "
            f"{pct(full['strategy_drawdown'])} | {full['maximum_intrabar_leverage']:.3f}X |"
        )
    lines += [
        "",
        (
            f"严格 3X 有效杠杆通过候选数：{payload['valid_under_hard_cap']} / "
            f"{payload['candidate_count']}。"
        ),
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
