#!/usr/bin/env python3
"""Research low-turnover volatility-managed BTC exposure under a 3x cap."""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import (
    STRESS_MAINTENANCE_RATE,
    annualized_return,
    replay_segregated,
    years_between,
)
from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT_DIR = Path("reports/experiments/btc_vol_managed_bh/2026-09-02")
LOOKBACKS = (20, 40, 60, 90, 120)
TARGET_VOLATILITIES = tuple(Decimal(value) for value in ("0.5", "0.6", "0.7", "0.8", "0.9"))
MINIMUM_EXPOSURES = tuple(Decimal(value) for value in ("0.25", "0.5", "0.75"))
MAXIMUM_EXPOSURE = Decimal("1.5")
SPOT_CAP = Decimal("0.75")
FUNDING_THRESHOLD = Decimal("0.0001")
REBALANCE_WEEKDAYS = (0,)  # Monday UTC after the completed Sunday daily bar.


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    daily, ends = aggregate_complete_periods(bars, "1d")
    candidates = []
    for lookback in LOOKBACKS:
        volatility = rolling_volatility(daily, lookback)
        for target_volatility in TARGET_VOLATILITIES:
            for minimum_exposure in MINIMUM_EXPOSURES:
                dense = weekly_volatility_targets(
                    daily,
                    volatility,
                    target_volatility,
                    minimum_exposure,
                    MAXIMUM_EXPOSURE,
                )
                mapped = map_targets_to_source(len(bars), dense, ends)
                targets = funding_cap_variable_targets(mapped, funding)
                metrics = {}
                for name, (start, end) in splits.items():
                    result = replay_segregated(
                        bars,
                        targets,
                        funding,
                        start,
                        end,
                        spot_cap=SPOT_CAP,
                        maintenance_rate=STRESS_MAINTENANCE_RATE,
                        fee_bps=Decimal("10"),
                        slippage_bps=Decimal("5"),
                        enforce_effective_leverage_cap=True,
                    )
                    metrics[name] = {
                        "net_return": result.net_return,
                        "max_drawdown": result.max_drawdown,
                        "fees": result.total_fees,
                        "funding": result.total_funding,
                        "rebalances": result.rebalances,
                        "liquidated": result.liquidated,
                        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
                        "excess": result.net_return - benchmarks[name]["net_return"],
                    }
                candidates.append(
                    {
                        "id": (
                            f"weekly-vol{lookback}-target{target_volatility}-"
                            f"min{minimum_exposure}-max{MAXIMUM_EXPOSURE}"
                        ),
                        "lookback": lookback,
                        "target_volatility": str(target_volatility),
                        "minimum_exposure": str(minimum_exposure),
                        "maximum_exposure": str(MAXIMUM_EXPOSURE),
                        "metrics": metrics,
                        "development_score": min(
                            metrics["research"]["excess"], metrics["validation"]["excess"]
                        ),
                    }
                )
    candidates.sort(key=lambda item: item["development_score"], reverse=True)
    selected = candidates[0]
    years = years_between(splits["full"][0], bars[-1].end_ms)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "protocol": {
            "signal": "completed daily simple-return volatility",
            "execution": "weekly Monday UTC target change, next 15m open",
            "exposure": "target_vol / realized_vol, clipped to configured minimum and 1.5x",
            "wallets": "75% spot and 25% separate USD-M collateral",
            "funding": "exposure above 1x capped to 1x when last known funding exceeds 0.01%",
            "costs": "10 bps fee + 5 bps slippage on changed notional",
            "selection": "maximize weaker research/validation excess; OOS excluded",
        },
        "data": {"bars": len(bars), "daily_bars": len(daily), "last": iso(bars[-1].end_ms)},
        "benchmarks": benchmarks,
        "evaluation_years": years,
        "selected": selected,
        "selected_full_cagr": annualized_return(selected["metrics"]["full"]["net_return"], years),
        "benchmark_full_cagr": annualized_return(benchmarks["full"]["net_return"], years),
        "candidates": candidates,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def rolling_volatility(bars, lookback: int):
    values: deque[Decimal] = deque()
    total = Decimal("0")
    total_squared = Decimal("0")
    output = []
    previous = None
    annualizer = Decimal("365").sqrt()
    for bar in bars:
        value = Decimal("0") if previous is None else bar.close / previous - Decimal("1")
        previous = bar.close
        values.append(value)
        total += value
        total_squared += value * value
        if len(values) > lookback:
            removed = values.popleft()
            total -= removed
            total_squared -= removed * removed
        if len(values) < lookback:
            output.append(None)
            continue
        count = Decimal(lookback)
        mean = total / count
        variance = max(Decimal("0"), total_squared / count - mean * mean)
        output.append(variance.sqrt() * annualizer)
    return tuple(output)


def weekly_volatility_targets(
    bars,
    volatility,
    target_volatility: Decimal,
    minimum_exposure: Decimal,
    maximum_exposure: Decimal,
):
    output = []
    previous = None
    for bar, realized in zip(bars, volatility, strict=True):
        moment = datetime.fromtimestamp(bar.end_ms / 1000, UTC)
        if realized is None or moment.weekday() not in REBALANCE_WEEKDAYS:
            output.append(None)
            continue
        target = maximum_exposure if realized == 0 else target_volatility / realized
        target = min(maximum_exposure, max(minimum_exposure, target))
        # A 0.05x band avoids trading immaterial target changes.
        target = (target * Decimal("20")).quantize(Decimal("1")) / Decimal("20")
        output.append(target if target != previous else None)
        previous = target
    return tuple(output)


def funding_cap_variable_targets(targets, funding):
    latest = Decimal("0")
    active = Decimal("1")
    previous = None
    output = []
    for index, proposed in enumerate(targets):
        for event in funding[index]:
            latest = event.rate
        if proposed is not None:
            active = Decimal(proposed)
        target = Decimal("1") if active > 1 and latest > FUNDING_THRESHOLD else active
        output.append(target if target != previous else None)
        previous = target
    return tuple(output)


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value: float) -> str:
    return f"{value:.2%}"


def markdown(payload: dict) -> str:
    lines = [
        "# BTC 波动率管理 B&H",
        "",
        "按已完成日线波动率每周调整 0.25X–1.5X 暴露；OOS 不参与选参。",
        "",
        "| 配置 | Research超额 | Validation超额 | OOS超额 | Full超额 | Full CAGR | DD | 盘中杠杆 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["candidates"][:20]:
        metrics = row["metrics"]
        full_cagr = annualized_return(metrics["full"]["net_return"], payload["evaluation_years"])
        lines.append(
            f"| `{row['id']}` | {pct(metrics['research']['excess'])} | "
            f"{pct(metrics['validation']['excess'])} | {pct(metrics['oos']['excess'])} | "
            f"{pct(metrics['full']['excess'])} | "
            f"{pct(full_cagr)} | "
            f"{pct(metrics['full']['max_drawdown'])} | "
            f"{metrics['full']['maximum_intrabar_leverage']:.2f}X |"
        )
    lines += [
        "",
        "开发期排名仅使用 Research 与 Validation 的较弱超额；OOS 只用于盲测。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
