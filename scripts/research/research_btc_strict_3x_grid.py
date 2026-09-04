#!/usr/bin/env python3
"""Screen causal BTC exposure schedules with an explicit 3x open leverage cap.

This is a validation companion to the earlier dynamic-exposure scans.  It uses the
separate-wallet replay so that losses cannot silently create free leverage, and reports
the remaining intrabar leverage excursion separately.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_collateral_architecture import (
    BASE_MAINTENANCE_RATE,
    STRESS_MAINTENANCE_RATE,
    annualized_return,
    replay_segregated,
    years_between,
)
from research_btc_dynamic_exposure import benchmark
from research_btc_funding_aware_exposure import funding_aware_targets
from research_btc_sma_trend import load_funding, load_market, split_periods
from research_btc_three_state_exposure import three_state_targets

from mastermind_tick.bar_research import funding_by_bar
from mastermind_tick.sma_trend import aggregate_complete_periods, map_targets_to_source

OUTPUT_DIR = Path("reports/experiments/btc_strict_3x_grid/2026-09-02")
PERIODS = (
    (16, 32, 64, 128),
    (20, 40, 80, 160),
    (24, 48, 96, 192),
    (25, 50, 100, 200),
    (26, 52, 104, 208),
    (28, 56, 112, 224),
)
BEAR_EXPOSURES = tuple(Decimal(value) for value in ("0", "0.25", "0.5"))
BULL_EXPOSURES = tuple(Decimal(value) for value in ("1.25", "1.5", "1.75", "2", "2.25", "2.5"))
FUNDING_THRESHOLD = Decimal("0.0001")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    splits = split_periods(bars)
    benchmarks = {name: benchmark(bars, *bounds) for name, bounds in splits.items()}
    aggregate, ends = aggregate_complete_periods(bars, "4h")
    rows = []
    for periods in PERIODS:
        for bear in BEAR_EXPOSURES:
            for bull in BULL_EXPOSURES:
                regime = three_state_targets(aggregate, periods, bear, bull)
                mapped = map_targets_to_source(len(bars), regime, ends)
                targets = funding_aware_targets(mapped, funding, bull, FUNDING_THRESHOLD)
                metrics = {}
                for name, (start, end) in splits.items():
                    base = replay_segregated(
                        bars,
                        targets,
                        funding,
                        start,
                        end,
                        spot_cap=Decimal("0"),
                        maintenance_rate=BASE_MAINTENANCE_RATE,
                        enforce_effective_leverage_cap=True,
                    )
                    stress = replay_segregated(
                        bars,
                        targets,
                        funding,
                        start,
                        end,
                        spot_cap=Decimal("0"),
                        maintenance_rate=STRESS_MAINTENANCE_RATE,
                        fee_bps=Decimal("10"),
                        slippage_bps=Decimal("5"),
                        enforce_effective_leverage_cap=True,
                    )
                    metrics[name] = {
                        "base": public_result(base),
                        "stress": public_result(stress),
                        "stress_excess": stress.net_return - benchmarks[name]["net_return"],
                    }
                rows.append(
                    {
                        "id": (
                            f"4h-{'-'.join(map(str, periods))}-bear{bear}x-bull{bull}x-"
                            f"funding-le-{FUNDING_THRESHOLD}"
                        ),
                        "periods": periods,
                        "bear_exposure": str(bear),
                        "bull_exposure": str(bull),
                        "metrics": metrics,
                    }
                )

    for row in rows:
        m = row["metrics"]
        row["development_score"] = min(
            m["research"]["stress_excess"], m["validation"]["stress_excess"]
        )
        # The user constraint is a hard *effective* leverage limit.  An opening
        # cap alone is insufficient because an adverse 15m low can reduce
        # collateral and create an intrabar excursion above 3x.  Reject any
        # candidate that exceeds the cap in any aggregate split, including the
        # full path, under the stressed cost model.
        row["intrabar_cap_passed"] = all(
            split["stress"]["maximum_intrabar_leverage"] <= 3.0 + 1e-9 for split in m.values()
        )
        row["qualifies"] = (
            not m["full"]["stress"]["liquidated"]
            and m["full"]["stress"]["net_return"] > benchmarks["full"]["net_return"]
            and m["oos"]["stress"]["net_return"] >= benchmarks["oos"]["net_return"]
            and m["research"]["stress"]["net_return"] > benchmarks["research"]["net_return"]
            and m["validation"]["stress"]["net_return"] > benchmarks["validation"]["net_return"]
            and row["intrabar_cap_passed"]
        )
    rows.sort(key=lambda row: row["development_score"], reverse=True)
    qualifying = [row for row in rows if row["qualifies"]]
    qualifying.sort(
        key=lambda row: (
            row["metrics"]["oos"]["stress_excess"],
            row["metrics"]["full"]["stress"]["net_return"],
        ),
        reverse=True,
    )
    full = qualifying[0] if qualifying else rows[0]
    years = years_between(splits["full"][0], bars[-1].end_ms)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "protocol": {
            "signal": "completed 4h four-SMA state; next 15m open",
            "funding": "last known funding above 0.01% reduces bull target to 1x",
            "costs": "stress 10 bps fee + 5 bps slippage on changed notional",
            "leverage": (
                "futures notional actively reduced to <=3x futures-wallet equity at every 15m open"
            ),
            "intrabar_note": (
                "OHLC lows are measured for leverage excursions; no future intrabar "
                "trade is assumed"
            ),
            "selection": "development score uses only research and validation; OOS is not selected",
            "hard_cap": (
                "candidate must remain <=3x both at every controlled 15m open and on the "
                "stressed intrabar-low effective-leverage audit"
            ),
        },
        "data": {"bars": len(bars), "last": iso(bars[-1].end_ms)},
        "benchmarks": benchmarks,
        "candidate_count": len(rows),
        "qualifying_count": len(qualifying),
        "evaluation_years": years,
        "selected_for_followup": {
            "id": full["id"],
            "full_stress_cagr": annualized_return(
                full["metrics"]["full"]["stress"]["net_return"], years
            ),
            "full_benchmark_cagr": annualized_return(benchmarks["full"]["net_return"], years),
            "oos_stress_excess": full["metrics"]["oos"]["stress_excess"],
        },
        "qualifying": qualifying,
        "ranking": rows,
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def public_result(result) -> dict:
    return {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
        "rebalances": result.rebalances,
        "liquidated": result.liquidated,
        "maximum_open_leverage": result.maximum_controlled_open_futures_leverage,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
    }


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value: float) -> str:
    return f"{value:.2%}"


def markdown(payload: dict) -> str:
    lines = [
        "# BTC 严格 3X 动态暴露候选筛选",
        "",
        (
            "所有候选使用已完成 4h SMA 状态和下一根 15m 开盘；每个开盘主动把合约"
            "名义压回 futures 钱包权益的 3X 以内。"
        ),
        "",
        "## 通过开发期、OOS 与全样本条件的候选",
        "",
        (
            "| 配置 | Research压力超额 | Validation压力超额 | OOS压力超额 | "
            "全样本压力CAGR | B&H压力CAGR | OOS DD | 盘中最高杠杆 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["qualifying"][:30]:
        m = row["metrics"]
        full_years = payload["evaluation_years"]
        lines.append(
            f"| `{row['id']}` | {pct(m['research']['stress_excess'])} | "
            f"{pct(m['validation']['stress_excess'])} | {pct(m['oos']['stress_excess'])} | "
            f"{pct(annualized_return(m['full']['stress']['net_return'], full_years))} | "
            f"{pct(annualized_return(payload['benchmarks']['full']['net_return'], full_years))} | "
            f"{pct(m['oos']['stress']['max_drawdown'])} | "
            f"{m['full']['stress']['maximum_intrabar_leverage']:.2f}X |"
        )
    if not payload["qualifying"]:
        lines.append("| 无 | - | - | - | - | - | - | - |")
    lines += [
        "",
        "## 选择协议与限制",
        "",
        "- 选择排序只使用 Research 与 Validation，OOS 不参与选参。",
        (
            "- `maximum_open_leverage` 是执行时硬上限；盘中价格路径可能使有效杠杆"
            "暂时高于 3X；此类候选已从 `qualifies` 中排除。"
        ),
        "- 合格候选必须在所有 aggregate split 的压力回放中盘中有效杠杆均不超过 3X。",
        "- 通过筛选不等于统计显著性或实盘批准；需要冻结后前向观察。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
