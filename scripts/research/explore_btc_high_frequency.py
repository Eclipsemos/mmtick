#!/usr/bin/env python3
"""Explore minute-level BTC trade-flow strategies with explicit execution costs."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sqlite3
from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from mastermind_tick.high_frequency_research import (
    ExecutionCost,
    HighFrequencyCandidate,
    feature_scores,
    replay_fixed_hold,
    threshold_events,
)

ROOT = Path(__file__).resolve().parents[2]
CACHE_VERSION = "btc-minute-flow-v1"
ZERO = ExecutionCost("zero", Decimal("0"), Decimal("0"))
OPTIMISTIC_MAKER = ExecutionCost("optimistic_maker", Decimal("2"), Decimal("0.5"))
BASE_TAKER = ExecutionCost("base_taker", Decimal("5"), Decimal("2"))
STRESS_TAKER = ExecutionCost("stress_taker", Decimal("10"), Decimal("5"))
DEVELOPMENT_COSTS = (ZERO, OPTIMISTIC_MAKER, BASE_TAKER)
ALL_COSTS = (*DEVELOPMENT_COSTS, STRESS_TAKER)
THRESHOLDS = (1.5, 2.0, 2.5)
FEATURE_NAMES = (
    "reported_flow_follow",
    "reported_flow_revert",
    "tick_flow_follow",
    "tick_flow_revert",
    "tick_price_confirm",
    "tick_absorption_revert",
    "volume_burst_follow",
    "volume_burst_revert",
)


@dataclass
class FlowSeries:
    interval_minutes: int
    timestamps: array
    closes: array
    total_notional: array
    reported_buy: array
    reported_sell: array
    tick_buy: array
    tick_sell: array
    trade_count: array

    @property
    def reported_imbalance(self) -> array:
        return _imbalance(self.reported_buy, self.reported_sell)

    @property
    def tick_rule_imbalance(self) -> array:
        return _imbalance(self.tick_buy, self.tick_sell)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/order_flow_cache/btc-1m-2024-20260811-hft-v1.csv.gz"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/btc_high_frequency/2026-08-18"),
    )
    args = parser.parse_args()
    periods = {
        "train": (_day_start(date(2024, 1, 1)), _day_end(date(2024, 12, 31))),
        "validation": (_day_start(date(2025, 1, 1)), _day_end(date(2025, 12, 31))),
        "confirmation": (_day_start(date(2026, 1, 1)), _day_end(date(2026, 8, 10))),
    }
    one_minute = load_or_build_cache(args.database, args.cache, periods)
    five_minute = resample(one_minute, 5)
    print(
        f"loaded {len(one_minute.timestamps):,} 1m and {len(five_minute.timestamps):,} 5m bars",
        flush=True,
    )
    development_cache = args.cache.with_name("btc-hft-development-v1.json.gz")
    if development_cache.exists():
        print(f"loading development cache {development_cache}", flush=True)
        with gzip.open(development_cache, "rt", encoding="utf-8") as handle:
            development = json.load(handle)
    else:
        development = screen_candidates((one_minute, five_minute), periods)
        with gzip.open(development_cache, "wt", encoding="utf-8", compresslevel=4) as handle:
            json.dump(development, handle, ensure_ascii=False)
        print(f"wrote development cache {development_cache}", flush=True)
    audit = audit_finalists(development, (one_minute, five_minute), periods)
    payload = build_payload(one_minute, five_minute, periods, development, audit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload["decision"], ensure_ascii=False), flush=True)
    print(args.output_dir / "README.md", flush=True)


def load_or_build_cache(
    database: Path,
    cache: Path,
    periods: dict[str, tuple[int, int]],
) -> FlowSeries:
    if cache.exists():
        print(f"loading minute cache {cache}", flush=True)
        return read_cache(cache)
    cache.parent.mkdir(parents=True, exist_ok=True)
    start_ms = periods["train"][0]
    end_ms = periods["confirmation"][1]
    series = aggregate_minutes(database, start_ms, end_ms)
    write_cache(cache, series)
    return series


def aggregate_minutes(database: Path, start_ms: int, end_ms: int) -> FlowSeries:
    uri = f"file:{database.resolve()}?mode=ro"
    series = _empty_series(1)
    query = """
        SELECT
            (timestamp_ms / 60000) * 60000 AS bucket,
            MAX(timestamp_ms) AS last_timestamp_ms,
            CAST(price AS REAL) AS close,
            SUM(CAST(notional AS REAL)) AS total_notional,
            SUM(CASE WHEN buyer_is_maker = 0 THEN CAST(notional AS REAL) ELSE 0 END),
            SUM(CASE WHEN buyer_is_maker = 1 THEN CAST(notional AS REAL) ELSE 0 END),
            SUM(
                CASE WHEN CAST(price AS REAL) > CAST(open_price AS REAL)
                THEN CAST(notional AS REAL) ELSE 0 END
            ),
            SUM(
                CASE WHEN CAST(price AS REAL) < CAST(open_price AS REAL)
                THEN CAST(notional AS REAL) ELSE 0 END
            ),
            COUNT(*)
        FROM agg_trades
        WHERE instrument_id = 'btc_perp' AND timestamp_ms >= ? AND timestamp_ms <= ?
        GROUP BY bucket
        ORDER BY bucket
    """
    chunks = tuple(_month_chunks(start_ms, end_ms))
    with sqlite3.connect(uri, uri=True) as connection:
        for index, (chunk_start, chunk_end) in enumerate(chunks, 1):
            for row in connection.execute(query, (chunk_start, chunk_end)):
                series.timestamps.append(int(row[0]))
                series.closes.append(float(row[2]))
                series.total_notional.append(float(row[3] or 0))
                series.reported_buy.append(float(row[4] or 0))
                series.reported_sell.append(float(row[5] or 0))
                series.tick_buy.append(float(row[6] or 0))
                series.tick_sell.append(float(row[7] or 0))
                series.trade_count.append(int(row[8]))
            print(f"aggregated minute flow {index}/{len(chunks)}", flush=True)
    if len(series.timestamps) < 1_000_000:
        raise ValueError("BTC high-frequency study requires at least one million minute bars")
    return series


def write_cache(path: Path, series: FlowSeries) -> None:
    print(f"writing minute cache {path}", flush=True)
    with gzip.open(path, "wt", newline="", encoding="utf-8", compresslevel=4) as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                CACHE_VERSION,
                "timestamp_ms",
                "close",
                "total_notional",
                "reported_buy",
                "reported_sell",
                "tick_buy",
                "tick_sell",
                "trade_count",
            ]
        )
        writer.writerows(
            zip(
                series.timestamps,
                series.closes,
                series.total_notional,
                series.reported_buy,
                series.reported_sell,
                series.tick_buy,
                series.tick_sell,
                series.trade_count,
                strict=True,
            )
        )


def read_cache(path: Path) -> FlowSeries:
    series = _empty_series(1)
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if not header or header[0] != CACHE_VERSION:
            raise ValueError(f"unsupported minute cache version in {path}")
        for row in reader:
            series.timestamps.append(int(row[0]))
            series.closes.append(float(row[1]))
            series.total_notional.append(float(row[2]))
            series.reported_buy.append(float(row[3]))
            series.reported_sell.append(float(row[4]))
            series.tick_buy.append(float(row[5]))
            series.tick_sell.append(float(row[6]))
            series.trade_count.append(int(row[7]))
    return series


def resample(source: FlowSeries, interval_minutes: int) -> FlowSeries:
    if interval_minutes <= source.interval_minutes or interval_minutes % source.interval_minutes:
        raise ValueError("resample interval must be a larger multiple of source interval")
    result = _empty_series(interval_minutes)
    expected = interval_minutes // source.interval_minutes
    interval_ms = interval_minutes * 60_000
    index = 0
    while index < len(source.timestamps):
        bucket = source.timestamps[index] // interval_ms * interval_ms
        end = index
        while end < len(source.timestamps) and source.timestamps[end] < bucket + interval_ms:
            end += 1
        if (
            end - index == expected
            and source.timestamps[index] == bucket
            and source.timestamps[end - 1] == bucket + interval_ms - 60_000
        ):
            result.timestamps.append(bucket)
            result.closes.append(source.closes[end - 1])
            result.total_notional.append(sum(source.total_notional[index:end]))
            result.reported_buy.append(sum(source.reported_buy[index:end]))
            result.reported_sell.append(sum(source.reported_sell[index:end]))
            result.tick_buy.append(sum(source.tick_buy[index:end]))
            result.tick_sell.append(sum(source.tick_sell[index:end]))
            result.trade_count.append(sum(source.trade_count[index:end]))
        index = end
    return result


def screen_candidates(
    series_items: tuple[FlowSeries, ...], periods: dict[str, tuple[int, int]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for series in series_items:
        reported = series.reported_imbalance
        tick_rule = series.tick_rule_imbalance
        windows = (30, 120, 360) if series.interval_minutes == 1 else (8, 24, 72)
        holds = (1, 3, 5, 10) if series.interval_minutes == 1 else (1, 2, 3, 6)
        for window in windows:
            print(f"building {series.interval_minutes}m features window={window}", flush=True)
            features = feature_scores(
                series.closes, reported, tick_rule, series.total_notional, window
            )
            for feature_name in FEATURE_NAMES:
                scores = features[feature_name]
                for threshold in THRESHOLDS:
                    events = threshold_events(scores, threshold)
                    for hold_bars in holds:
                        candidate = HighFrequencyCandidate(
                            feature_name,
                            series.interval_minutes,
                            window,
                            threshold,
                            hold_bars,
                        )
                        results = {
                            cost.name: {
                                split: replay_fixed_hold(
                                    series.timestamps,
                                    series.closes,
                                    events,
                                    hold_bars=hold_bars,
                                    start_ms=periods[split][0],
                                    end_ms=periods[split][1],
                                    cost=cost,
                                ).as_dict()
                                for split in ("train", "validation")
                            }
                            for cost in DEVELOPMENT_COSTS
                        }
                        rows.append({"candidate": candidate.as_dict(), "development": results})
            print(
                f"screened {len(rows):,} candidates through {series.interval_minutes}m/{window}",
                flush=True,
            )
    return rows


def audit_finalists(
    development: list[dict[str, Any]],
    series_items: tuple[FlowSeries, ...],
    periods: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    rankings = {
        cost.name: sorted(
            development, key=lambda row: development_score(row, cost.name), reverse=True
        )
        for cost in DEVELOPMENT_COSTS
    }
    eligible = {
        cost.name: [row for row in development if development_eligible(row, cost.name)]
        for cost in DEVELOPMENT_COSTS
    }
    finalist_ids = {
        row["candidate"]["id"]
        for cost in DEVELOPMENT_COSTS
        for row in sorted(
            eligible[cost.name] or rankings[cost.name],
            key=lambda item: development_score(item, cost.name),
            reverse=True,
        )[:5]
    }
    finalist_ids.update(
        sorted(
            eligible[cost.name] or rankings[cost.name],
            key=lambda item: development_score(item, cost.name),
            reverse=True,
        )[0]["candidate"]["id"]
        for cost in DEVELOPMENT_COSTS
    )
    by_interval = {series.interval_minutes: series for series in series_items}
    audited: list[dict[str, Any]] = []
    feature_cache: dict[tuple[int, int], dict[str, array]] = {}
    for row in development:
        candidate_data = row["candidate"]
        if candidate_data["id"] not in finalist_ids:
            continue
        candidate = HighFrequencyCandidate(
            feature=candidate_data["feature"],
            interval_minutes=candidate_data["interval_minutes"],
            normalization_window=candidate_data["normalization_window"],
            threshold=candidate_data["threshold"],
            hold_bars=candidate_data["hold_bars"],
        )
        series = by_interval[candidate.interval_minutes]
        key = (candidate.interval_minutes, candidate.normalization_window)
        if key not in feature_cache:
            feature_cache[key] = feature_scores(
                series.closes,
                series.reported_imbalance,
                series.tick_rule_imbalance,
                series.total_notional,
                candidate.normalization_window,
            )
        events = threshold_events(feature_cache[key][candidate.feature], candidate.threshold)
        confirmation = {
            cost.name: replay_fixed_hold(
                series.timestamps,
                series.closes,
                events,
                hold_bars=candidate.hold_bars,
                start_ms=periods["confirmation"][0],
                end_ms=periods["confirmation"][1],
                cost=cost,
            ).as_dict()
            for cost in ALL_COSTS
        }
        audited.append({**row, "confirmation": confirmation})
    selected = {
        cost.name: sorted(
            eligible[cost.name] or rankings[cost.name],
            key=lambda row: development_score(row, cost.name),
            reverse=True,
        )[0]["candidate"]["id"]
        for cost in DEVELOPMENT_COSTS
    }
    return {
        "eligible_counts": {name: len(rows) for name, rows in eligible.items()},
        "selected_ids": selected,
        "top_development": {
            cost.name: [
                {**row, "score": list(development_score(row, cost.name))}
                for row in rankings[cost.name][:10]
            ]
            for cost in DEVELOPMENT_COSTS
        },
        "finalists": sorted(audited, key=lambda row: row["candidate"]["id"]),
    }


def development_eligible(row: dict[str, Any], cost_name: str) -> bool:
    results = row["development"][cost_name]
    return all(
        result["net_return"] > 0
        and result["max_drawdown"] >= -0.25
        and result["completed_trades"] >= 100
        and not result["bankrupt"]
        for result in (results["train"], results["validation"])
    )


def development_score(row: dict[str, Any], cost_name: str) -> tuple[float, ...]:
    train = row["development"][cost_name]["train"]
    validation = row["development"][cost_name]["validation"]
    active = min(train["completed_trades"], validation["completed_trades"]) >= 100
    return (
        float(active),
        min(train["net_return"], validation["net_return"]),
        train["net_return"] + validation["net_return"],
        min(train["profit_factor"] or 0, validation["profit_factor"] or 0),
        min(train["max_drawdown"], validation["max_drawdown"]),
        min(train["completed_trades"], validation["completed_trades"]),
    )


def build_payload(
    one_minute: FlowSeries,
    five_minute: FlowSeries,
    periods: dict[str, tuple[int, int]],
    development: list[dict[str, Any]],
    audit: dict[str, Any],
) -> dict[str, Any]:
    selected_results = {}
    finalists = {row["candidate"]["id"]: row for row in audit["finalists"]}
    for cost_name, candidate_id in audit["selected_ids"].items():
        selected_results[cost_name] = finalists[candidate_id]
    base = selected_results["base_taker"]
    base_confirmation = base["confirmation"]["base_taker"]
    stress_confirmation = base["confirmation"]["stress_taker"]
    approved = (
        audit["eligible_counts"]["base_taker"] > 0
        and base_confirmation["net_return"] > 0
        and stress_confirmation["net_return"] > 0
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "id": "btc-high-frequency-20260818",
        "strategy_class": "minute-level trade-flow research; not latency HFT",
        "data": {
            "instrument": "BTCUSDT perpetual",
            "one_minute_bars": len(one_minute.timestamps),
            "five_minute_bars": len(five_minute.timestamps),
            "first": datetime.fromtimestamp(one_minute.timestamps[0] / 1000, UTC).isoformat(),
            "last": datetime.fromtimestamp(one_minute.timestamps[-1] / 1000, UTC).isoformat(),
            "reported_notional_coverage": _coverage(one_minute, "reported"),
            "tick_rule_notional_coverage": _coverage(one_minute, "tick_rule"),
            "has_historical_bid_ask": False,
            "has_order_book_depth": False,
        },
        "periods": {
            name: {
                "start": datetime.fromtimestamp(start / 1000, UTC).isoformat(),
                "end": datetime.fromtimestamp(end / 1000, UTC).isoformat(),
            }
            for name, (start, end) in periods.items()
        },
        "execution": {
            "signal": "closed 1m or 5m bar",
            "fill": "next bar close with configured slippage",
            "position": "non-overlapping fixed 1x notional",
            "funding": "not charged; holds are at most 50 minutes and funding is not the signal",
            "costs": [
                {
                    "name": cost.name,
                    "fee_bps_per_fill": float(cost.fee_bps),
                    "slippage_bps_per_fill": float(cost.slippage_bps),
                }
                for cost in ALL_COSTS
            ],
        },
        "search": {
            "candidate_count": len(development),
            "features": list(FEATURE_NAMES),
            "thresholds": list(THRESHOLDS),
            "selection_uses_confirmation": False,
            "eligible_counts": audit["eligible_counts"],
        },
        "selected": selected_results,
        "top_development": audit["top_development"],
        "finalists": audit["finalists"],
        "decision": {
            "status": "research_candidate" if approved else "rejected",
            "paper_approved": False,
            "live_approved": False,
            "reason": (
                "A development-eligible taker-cost candidate survived base and stress confirmation."
                if approved
                else "No development-robust strategy survived both base and stress taker costs."
            ),
        },
        "limitations": [
            "2026 is reused confirmation evidence, not a fresh holdout.",
            "No historical bid/ask, queue position, depth, cancel latency, or maker fill "
            "probability.",
            "Funding is not charged in this sub-hour replay; longer-hold results must add it.",
            "Tick-rule direction is a 250ms bucket proxy and is not exchange-reported "
            "aggressor side.",
            "The bare-price SQLite aggregate uses the trade at the minute's maximum timestamp; "
            "ties are arbitrary.",
            "Fixed slippage cannot reproduce spread widening, market impact, or adverse selection.",
        ],
    }


def markdown(payload: dict[str, Any]) -> str:
    selected = payload["selected"]
    lines = [
        "# BTC 分钟级高频策略探索",
        "",
        f"Decision: `{payload['decision']['status']}`. Paper/live approval: `false/false`.",
        "",
        "本实验使用真实 BTCUSDT 永续 aggTrades 聚合 1m/5m 成交流，但没有历史盘口、",
        "队列位置或订单延迟，因此属于分钟级微观结构研究，不是可证明的低延迟 HFT。",
        "",
        "## 数据与协议",
        "",
        f"- 1m bars: `{payload['data']['one_minute_bars']:,}`；5m bars: "
        f"`{payload['data']['five_minute_bars']:,}`。",
        f"- 实际买卖方字段覆盖名义金额：`{payload['data']['reported_notional_coverage']:.2%}`；"
        f"tick-rule代理覆盖：`{payload['data']['tick_rule_notional_coverage']:.2%}`。",
        f"- 网格候选：`{payload['search']['candidate_count']}`。2024训练、2025验证，"
        "2026-01-01至08-10仅复用确认。",
        "- 信号在闭合bar计算，最早下一根bar收盘成交；固定1x、不重叠持仓。",
        "- 成本：零成本；乐观maker 2+0.5 bps；基础taker 5+2 bps；压力taker 10+5 bps，"
        "均为每次fill。",
        "",
        "## 开发期筛选",
        "",
        "| 成本 | 2024/2025均盈利候选 | 入选参数 | 2024 | 2025 | 交易数(2024/2025) |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for cost_name in ("zero", "optimistic_maker", "base_taker"):
        row = selected[cost_name]
        train = row["development"][cost_name]["train"]
        validation = row["development"][cost_name]["validation"]
        lines.append(
            f"| {cost_name} | {payload['search']['eligible_counts'][cost_name]} | "
            f"`{row['candidate']['id']}` | {train['net_return']:.2%} | "
            f"{validation['net_return']:.2%} | "
            f"{train['completed_trades']:,}/{validation['completed_trades']:,} |"
        )
    lines.extend(
        [
            "",
            "## 复用确认成本敏感性",
            "",
            "下表分别冻结各成本口径在2024/2025选出的候选，再查看2026；不是用2026重新选参。",
            "",
            "| 开发选择口径 | 候选 | 零成本 | 乐观maker | 基础taker | 压力taker | "
            "DD(base) | 单fill盈亏平衡成本 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for selection_cost in ("zero", "optimistic_maker", "base_taker"):
        row = selected[selection_cost]
        confirmation = row["confirmation"]
        base = confirmation["base_taker"]
        break_even = confirmation["zero"]["approximate_break_even_bps_per_fill"]
        break_even_text = "n/a" if break_even is None else f"{break_even:.2f} bps"
        lines.append(
            f"| {selection_cost} | `{row['candidate']['id']}` | "
            f"{confirmation['zero']['net_return']:.2%} | "
            f"{confirmation['optimistic_maker']['net_return']:.2%} | "
            f"{base['net_return']:.2%} | {confirmation['stress_taker']['net_return']:.2%} | "
            f"{base['max_drawdown']:.2%} | {break_even_text} |"
        )
    base_selected = selected["base_taker"]
    monthly = base_selected["confirmation"]["base_taker"]["monthly_returns"]
    lines.extend(
        [
            "",
            "## 基础taker候选月收益",
            "",
            f"Candidate: `{base_selected['candidate']['id']}`.",
            "",
            "| 月份 | 收益 |",
            "|---|---:|",
        ]
    )
    lines.extend(f"| {row['label']} | {row['return']:.2%} |" for row in monthly)
    lines.extend(
        [
            "",
            "## 解释",
            "",
            "高频策略的核心问题不是能否找到零成本预测，而是每次交易的毛优势能否覆盖两次",
            "成交成本。`单fill盈亏平衡成本`应与maker/taker实际总成本直接比较。即使乐观maker",
            "回放为正，没有盘口和队列数据也无法证明会成交，更无法量化被动成交后的逆向选择。",
            "",
            f"最终结论：{payload['decision']['reason']} 本实验不批准模拟盘或实盘。",
            "",
            "## 限制",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in payload["limitations"])
    lines.extend(["", "机器可读结果见 [`results.json`](results.json)。", ""])
    return "\n".join(lines)


def _empty_series(interval_minutes: int) -> FlowSeries:
    return FlowSeries(
        interval_minutes,
        array("q"),
        array("d"),
        array("d"),
        array("d"),
        array("d"),
        array("d"),
        array("d"),
        array("q"),
    )


def _imbalance(buy: array, sell: array) -> array:
    return array(
        "d",
        (
            (
                (buy_value - sell_value) / (buy_value + sell_value)
                if buy_value + sell_value > 0
                else math.nan
            )
            for buy_value, sell_value in zip(buy, sell, strict=True)
        ),
    )


def _coverage(series: FlowSeries, source: str) -> float:
    classified = (
        sum(series.reported_buy) + sum(series.reported_sell)
        if source == "reported"
        else sum(series.tick_buy) + sum(series.tick_sell)
    )
    total = sum(series.total_notional)
    return classified / total if total else 0.0


def _month_chunks(start_ms: int, end_ms: int) -> Iterable[tuple[int, int]]:
    current = datetime.fromtimestamp(start_ms / 1000, UTC)
    current = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while int(current.timestamp() * 1000) <= end_ms:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield (
            max(start_ms, int(current.timestamp() * 1000)),
            min(end_ms, int(next_month.timestamp() * 1000) - 1),
        )
        current = next_month


def _day_start(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


if __name__ == "__main__":
    main()
