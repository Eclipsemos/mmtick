#!/usr/bin/env python3
"""Research BTC trend/exposure candidates with a hard intrabar 3x audit."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import replay_segregated, years_between
from research_btc_dynamic_exposure import benchmark
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_macro_gated_3x import macro_gated_targets
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source
from mastermind_tick.sma_weekly import simple_moving_average

OUTPUT_DIR = Path("reports/experiments/btc_hard_3x_guard/2026-09-02")
PERIODS = ((25, 50, 100, 200), (26, 52, 104, 208))
MACRO_PERIODS = (900, 1200)
BULL_EXPOSURES = tuple(Decimal(value) for value in ("1.5", "1.75", "2"))
BEAR_EXPOSURES = (Decimal("0"), Decimal("0.25"))
SPOT_CAPS = (Decimal("0.5"), Decimal("0.75"))
FUNDING_THRESHOLD = Decimal("0.0001")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
MAINTENANCE = Decimal("0.02")
MAX_LEVERAGE = Decimal("3")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    macro = {period: simple_moving_average(aggregate, period) for period in MACRO_PERIODS}
    rows = []
    for periods in PERIODS:
        for macro_period in MACRO_PERIODS:
            for bear in BEAR_EXPOSURES:
                for bull in BULL_EXPOSURES:
                    for spot_cap in SPOT_CAPS:
                        if bull > spot_cap + MAX_LEVERAGE * (Decimal("1") - spot_cap):
                            continue
                        raw = three_state_targets(aggregate, periods, bear, bull)
                        gated = macro_gated_targets(aggregate, raw, macro[macro_period], bull)
                        source_targets = map_targets_to_source(len(bars), gated, ends)
                        targets = funding_aware_targets(
                            source_targets, funding, bull, FUNDING_THRESHOLD
                        )
                        metrics = {}
                        for name, (start, end) in splits.items():
                            result = replay_segregated(
                                bars,
                                targets,
                                funding,
                                start,
                                end,
                                spot_cap=spot_cap,
                                maintenance_rate=MAINTENANCE,
                                fee_bps=FEE_BPS,
                                slippage_bps=SLIPPAGE_BPS,
                            )
                            metrics[name] = asdict(result)
                            metrics[name].pop("equity_curve", None)
                            metrics[name]["excess"] = (
                                result.net_return - benchmarks[name]["net_return"]
                            )
                        rows.append(
                            {
                                "id": (
                                    f"4h-{'-'.join(map(str, periods))}-macro{macro_period}-"
                                    f"bear{bear}x-bull{bull}x-spot{spot_cap}"
                                ),
                                "periods": periods,
                                "macro_period": macro_period,
                                "bear_exposure": str(bear),
                                "bull_exposure": str(bull),
                                "spot_cap": str(spot_cap),
                                "metrics": metrics,
                            }
                        )
    eligible = [row for row in rows if hard_cap_passes(row)]
    eligible.sort(key=development_score, reverse=True)
    development_passes = [
        row
        for row in eligible
        if row["metrics"]["research"]["excess"] > 0 and row["metrics"]["validation"]["excess"] > 0
    ]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "protocol": {
            "signal": "completed 4h SMA and macro bars; next 15m open",
            "costs": "10 bps fee + 5 bps slippage; historical funding",
            "maintenance": str(MAINTENANCE),
            "hard_leverage_definition": (
                "maximum observed 15m-open and intrabar-low futures leverage must be <=3x"
            ),
            "selection": (
                "ranking uses research and validation only; OOS and 2019 holdout are read-only"
            ),
        },
        "data": {
            "bars": len(bars),
            "first": bars[0].start_ms,
            "last": bars[-1].end_ms,
            "funding_events": len(load_funding("BTCUSDT", bars)),
        },
        "benchmarks": benchmarks,
        "candidate_count": len(rows),
        "hard_cap_candidate_count": len(eligible),
        "development_pass_count": len(development_passes),
        "evaluation_years": years_between(*splits["full"]),
        "top_candidates": [public(row) for row in eligible[:20]],
        "all_candidates": [public(row) for row in rows],
        "limitations": [
            "This is a bounded mechanism grid, not a proof of statistical significance.",
            "No data after 2026-09-02 is available for a fresh forward test.",
            "2019 holdout is evaluated separately after candidate ranking.",
        ],
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def hard_cap_passes(row: dict) -> bool:
    full = row["metrics"]["full"]
    oos = row["metrics"]["oos"]
    return (
        not any(
            row["metrics"][name]["liquidated"] for name in ("research", "validation", "oos", "full")
        )
        and max(
            row["metrics"][name]["maximum_observed_futures_leverage"]
            for name in ("research", "validation", "oos", "full")
        )
        <= float(MAX_LEVERAGE)
        and full["net_return"] > 0
        and oos["net_return"] > 0
    )


def development_score(row: dict) -> float:
    return min(row["metrics"][name]["excess"] for name in ("research", "validation"))


def public(row: dict) -> dict:
    return {
        "id": row["id"],
        "research": row["metrics"]["research"],
        "validation": row["metrics"]["validation"],
        "oos": row["metrics"]["oos"],
        "full": row["metrics"]["full"],
    }


def pct(value: float) -> str:
    return f"{value:.2%}"


def render(payload: dict) -> str:
    lines = [
        "# BTC Hard 3X Guard Research",
        "",
        (
            "候选使用完成的 4h 趋势与宏观 SMA，下一根 15m 开盘调仓；"
            "压力成本为 10+5 bps，计入 Funding。"
        ),
        "硬约束要求所有分段的开盘与盘中低点观测杠杆均不超过 3X。",
        "",
        (
            f"数据：{payload['data']['bars']:,} 根 15m；"
            f"Funding {payload['data']['funding_events']:,} 次；"
            f"候选 {payload['candidate_count']} 个，"
            f"硬约束通过 {payload['hard_cap_candidate_count']} 个，"
            f"Research 与 Validation 均超过 B&H 的有 "
            f"{payload['development_pass_count']} 个。"
        ),
        "",
        "## 开发期排名（仅 Research + Validation）",
        "",
        (
            "| 配置 | Research超额 | Validation超额 | OOS超额 | Full CAGR近似 | "
            "Full DD | 盘中最高杠杆 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["top_candidates"]:
        full, research, validation, oos = (
            row["full"],
            row["research"],
            row["validation"],
            row["oos"],
        )
        years = payload["evaluation_years"]
        cagr = (1 + full["net_return"]) ** (1 / years) - 1 if full["net_return"] > -1 else -1
        max_intrabar = max(
            row[name]["maximum_observed_futures_leverage"]
            for name in ("research", "validation", "oos", "full")
        )
        lines.append(
            f"| `{row['id']}` | {pct(research['excess'])} | "
            f"{pct(validation['excess'])} | {pct(oos['excess'])} | "
            f"{pct(cagr)} | {pct(full['max_drawdown'])} | {max_intrabar:.3f}X |"
        )
    lines += [
        "",
        "## 解读",
        "",
        (
            "排名只用于提出候选；OOS 不能反向调参。即使 Full/OOS 超过 B&H，"
            "也必须通过独立留出、滚动窗口和前向观察，才可考虑 Paper Trading。"
        ),
        "",
        "状态：**RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED**。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
