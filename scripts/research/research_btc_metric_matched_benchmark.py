#!/usr/bin/env python3
"""Screen causal BTC futures metrics against a matched continuous-BTC benchmark.

The test is intentionally narrow: a market metric either enables a 1.5X BTC long
exposure or leaves the account flat.  All targets are calculated after a completed
four-hour bar and are filled on the following 15-minute open.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import audit_btc_stitched_strict15m_sma10 as base
from audit_btc_sma11_levered_benchmark import constant_targets

from mastermind_tick.market_metrics import (
    METRIC_FEATURES,
    causal_metric_features,
    load_metric_archives,
    metric_targets,
)
from mastermind_tick.sma_trend import aggregate_complete_periods

OUTPUT = Path("reports/experiments/btc_metric_matched_benchmark/2026-09-03")
METRICS_DIR = Path("data/futures_metrics")
ACTIVE = Decimal("1.5")
WINDOWS = (180, 540, 1080)
THRESHOLDS = (Decimal("1"), Decimal("1.5"), Decimal("2"))
POLARITIES = ("follow", "fade")
DISPLAY_ROWS = 20


@dataclass(frozen=True)
class MetricCandidate:
    window: int
    feature: str
    threshold: Decimal
    polarity: str

    @property
    def id(self) -> str:
        threshold = f"{self.threshold:g}".replace(".", "p")
        return f"btc-{self.feature}-window{self.window}-z{threshold}-{self.polarity}-long-only"


def candidate_library() -> tuple[MetricCandidate, ...]:
    return tuple(
        MetricCandidate(window, feature, threshold, polarity)
        for window in WINDOWS
        for feature in METRIC_FEATURES
        for threshold in THRESHOLDS
        for polarity in POLARITIES
    )


def exposures(signals: tuple[int | None, ...]) -> tuple[Decimal, ...]:
    """Map unavailable and non-long signals to a deliberate flat target."""
    return tuple(ACTIVE if signal == 1 else Decimal("0") for signal in signals)


def metric_target_indices(
    spot_count: int, four_hour_end_indices: tuple[int, ...]
) -> tuple[int, ...]:
    return tuple(spot_count + index for index in four_hour_end_indices)


def periods(last_end: int) -> dict[str, tuple[int, int]]:
    return {
        "research": (base.utc_ms(2021), base.utc_ms(2022, 12, 31, 23, 59, 59, 999000)),
        "validation": (base.utc_ms(2023), base.utc_ms(2024, 12, 31, 23, 59, 59, 999000)),
        "oos": (base.utc_ms(2025), last_end),
        "full": (base.utc_ms(2021), last_end),
    }


def public(result, matched, buy_and_hold) -> dict[str, float | bool]:
    return {
        "strategy_return": result.net_return,
        "matched_1p5x_return": matched.net_return,
        "one_x_buy_and_hold_return": buy_and_hold["net_return"],
        "matched_excess": result.net_return - matched.net_return,
        "one_x_excess": result.net_return - buy_and_hold["net_return"],
        "strategy_drawdown": result.max_drawdown,
        "matched_drawdown": matched.max_drawdown,
        "maximum_intrabar_leverage": result.maximum_observed_futures_leverage,
        "liquidated": result.liquidated,
        "fees": result.total_fees,
        "funding": result.total_funding,
    }


def qualifies(metrics: dict[str, dict[str, float | bool]]) -> bool:
    return all(
        row["matched_excess"] > 0
        and row["maximum_intrabar_leverage"] <= 3
        and not row["liquidated"]
        for row in metrics.values()
    )


def main() -> None:
    spot, futures, _daily, _daily_indices, funding = base.load_hybrid_inputs()
    bars = spot + futures
    four_hour, four_hour_ends = aggregate_complete_periods(futures, "4h")
    metrics = load_metric_archives(METRICS_DIR, "BTCUSDT")
    available = [bar.end_ms for bar in four_hour if bar.start_ms in metrics]
    if not available:
        raise ValueError("no BTC futures metric bars align with the price history")
    bounds = periods(min(bars[-1].end_ms, max(available)))
    target_indices = metric_target_indices(len(spot), four_hour_ends)
    matched_targets = constant_targets(len(bars), ACTIVE)
    matched = {
        name: base.replay(bars, matched_targets, funding, *period)
        for name, period in bounds.items()
    }
    buy_and_hold = {name: base.benchmark(bars, *period) for name, period in bounds.items()}
    feature_sets = {
        window: causal_metric_features(four_hour, metrics, normalization_window=window)
        for window in WINDOWS
    }

    rows = []
    for candidate in candidate_library():
        signals = metric_targets(
            feature_sets[candidate.window][candidate.feature],
            threshold=candidate.threshold,
            polarity=candidate.polarity,
            direction="long_only",
        )
        targets = base.map_targets(len(bars), target_indices, exposures(signals))
        development = {
            name: public(
                base.replay(bars, targets, funding, *bounds[name]),
                matched[name],
                buy_and_hold[name],
            )
            for name in ("research", "validation")
        }
        rows.append(
            {
                "id": candidate.id,
                "candidate": {
                    "window_4h": candidate.window,
                    "feature": candidate.feature,
                    "threshold": float(candidate.threshold),
                    "polarity": candidate.polarity,
                    "direction": "long_only",
                    "active_exposure": float(ACTIVE),
                },
                "development": development,
                "development_min_matched_excess": min(
                    row["matched_excess"] for row in development.values()
                ),
                "targets": targets,
            }
        )
    rows.sort(key=lambda row: row["development_min_matched_excess"], reverse=True)
    qualifying = [row for row in rows if qualifies(row["development"])]
    for row in qualifying:
        row["oos"] = public(
            base.replay(bars, row["targets"], funding, *bounds["oos"]),
            matched["oos"],
            buy_and_hold["oos"],
        )
        row["full"] = public(
            base.replay(bars, row["targets"], funding, *bounds["full"]),
            matched["full"],
            buy_and_hold["full"],
        )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / NOT_PROMOTED",
        "protocol": {
            "signals": "completed Binance futures-metric 4h bars; next 15m open execution",
            "family": (
                "trailing-normalized OI, taker flow, and positioning metrics; "
                "long-only 0X/1.5X exposure"
            ),
            "windows_4h": WINDOWS,
            "thresholds": [str(value) for value in THRESHOLDS],
            "selection": "Research 2021-2022 and Validation 2023-2024 only",
            "oos": "2025 through latest complete metric bar, unread unless development qualifies",
            "benchmark": (
                "continuous 1.5X BTC under identical 50/50 wallets, Funding, costs, and controls"
            ),
            "costs": "10 bps fee + 5 bps slippage per side; historical Funding",
            "hard_cap": "2X futures opening control; observed intrabar effective leverage <=3X",
            "missing_metrics": "flat 0X; missing data is never interpreted as a signal",
        },
        "data": {
            "four_hour_bars": len(four_hour),
            "metric_bars": len(metrics),
            "last_complete_metric_bar": base.iso(bounds["full"][1]),
        },
        "candidate_count": len(rows),
        "development_qualifying_count": len(qualifying),
        "matched_benchmark": {
            name: public(value, value, buy_and_hold[name]) for name, value in matched.items()
        },
        "results": [{key: value for key, value in row.items() if key != "targets"} for row in rows],
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def render(payload: dict) -> str:
    lines = [
        "# BTC Futures-Metric Matched-Benchmark Screen",
        "",
        "BTC 期货市场指标独立于既有 SMA 规则筛选。所有信号仅使用已完成的 4 小时指标，"
        "并在下一根 15 分钟 K 线开盘交易。",
        "",
        "| 配置 | R相对1.5X | V相对1.5X | 开发最差 | R相对1X | V相对1X |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"][:DISPLAY_ROWS]:
        research = row["development"]["research"]
        validation = row["development"]["validation"]
        lines.append(
            f"| `{row['id']}` | {research['matched_excess']:.2%} | "
            f"{validation['matched_excess']:.2%} | {row['development_min_matched_excess']:.2%} | "
            f"{research['one_x_excess']:.2%} | {validation['one_x_excess']:.2%} |"
        )
    lines += [
        "",
        (
            f"Markdown 仅显示按开发期最差表现排序的前 {DISPLAY_ROWS} 个；"
            "完整 144 个配置见 `results.json`。"
        ),
        (
            "开发期合格成员："
            f"{payload['development_qualifying_count']} / {payload['candidate_count']}。"
        ),
        "只有开发期同时超过连续 1.5X BTC、无强平且盘中杠杆不超过 3X 的成员才读取 OOS；"
        "没有合格成员时，2025 之后结果不会被用于反向挑选。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
