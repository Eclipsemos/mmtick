#!/usr/bin/env python3
"""Test predeclared filters on the BTC four-SMA trend strategy."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import evaluate_targets, funding_by_bar, wilder_atr_values
from mastermind_tick.sma_trend import (
    aggregate_complete_periods,
    four_sma_targets,
    map_targets_to_source,
)
from mastermind_tick.sma_weekly import simple_moving_average

BASES = (
    ("1h", (25, 50, 100, 200)),
    ("4h", (20, 40, 80, 160)),
    ("4h", (10, 20, 30, 40)),
)


def main() -> None:
    output_dir = Path("reports/experiments/btc_sma_enhancements/2026-09-01")
    output_dir.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding_rates = load_funding("BTCUSDT", bars)
    funding = funding_by_bar(bars, funding_rates)
    periods = split_periods(bars)
    rows = []
    for timeframe, sma_periods in BASES:
        aggregate, ends = aggregate_complete_periods(bars, timeframe)
        base = map_targets_to_source(len(bars), four_sma_targets(aggregate, sma_periods), ends)
        filter_targets = build_filters(bars, funding, timeframe, sma_periods, aggregate, ends, base)
        for name, targets in filter_targets.items():
            metrics = {}
            for split, (start, end) in periods.items():
                result = evaluate_targets(
                    bars, targets, start_ms=start, end_ms=end, funding=funding
                )
                metrics[split] = summary(result)
            rows.append(
                {
                    "id": f"{timeframe}-{'-'.join(map(str, sma_periods))}-{name}",
                    "timeframe": timeframe,
                    "sma_periods": sma_periods,
                    "mechanism": name,
                    "periods": metrics,
                }
            )
    rows.sort(key=lambda row: row["periods"]["oos"]["net_return"], reverse=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "symbol": "BTCUSDT",
            "source": "Binance USD-M completed local 15m bars",
            "base_strategy": "long when four SMAs are strictly ordered; flat on failure",
            "timing": "completed aggregate candle, next 15m open execution",
            "costs": "5 bps fee + 2 bps slippage per fill; historical funding included",
            "selection": "mechanisms are predeclared; OOS is reported, not used for selection",
        },
        "data": {
            "bars": len(bars),
            "funding_events": len(funding_rates),
            "last": iso(bars[-1].end_ms),
        },
        "buy_and_hold": {
            split: benchmark(bars, start, end) for split, (start, end) in periods.items()
        },
        "results": rows,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(output_dir / "README.md")


def build_filters(bars, funding, timeframe, periods, aggregate, ends, base):
    result = {"base": base}
    result["price_above_all_sma"] = map_targets_to_source(
        len(bars), four_sma_targets(aggregate, periods, require_price_confirmation=True), ends
    )
    slow_sma = simple_moving_average(aggregate, periods[-1])
    result["close_above_slowest_sma"] = filtered_aggregate(
        base,
        ends,
        len(bars),
        lambda index: slow_sma[index] is not None and aggregate[index].close > slow_sma[index],
    )
    for quantile in (0.3, 0.5):
        result[f"atr_percentile_{quantile:.1f}"] = volatility_filter(
            base, ends, bars, aggregate, quantile
        )
    result["volume_above_mean"] = volume_filter(base, ends, aggregate)
    for margin in (Decimal("0.001"), Decimal("0.0025"), Decimal("0.005")):
        result[f"sma_spacing_{margin:.2%}"] = spacing_filter(base, ends, aggregate, periods, margin)
    if timeframe == "1h":
        higher, higher_ends = aggregate_complete_periods(bars, "4h")
        higher_targets = map_targets_to_source(
            len(bars), four_sma_targets(higher, (20, 40, 80, 160)), higher_ends
        )
        result["4h_regime"] = combine_targets(base, higher_targets)
    for threshold in (Decimal("0.0001"), Decimal("0.0003")):
        result[f"funding_le_{threshold:.2%}"] = funding_filter(base, bars, funding, threshold)
    return result


def filtered_aggregate(base, ends, source_count, predicate):
    targets = list(base)
    for target_index, source_index in enumerate(ends):
        if base[source_index] == 1 and not predicate(target_index):
            targets[source_index] = 0
    return tuple(targets)


def volatility_filter(base, ends, bars, aggregate, quantile):
    atr = wilder_atr_values(aggregate, 14)
    values = [None if a is None else a / aggregate[i].close for i, a in enumerate(atr)]

    def predicate(index):
        current = values[index]
        history = [value for value in values[max(0, index - 100) : index] if value is not None]
        if current is None or len(history) < 20:
            return False
        history.sort()
        position = min(len(history) - 1, int((len(history) - 1) * quantile))
        return current >= history[position]

    return filtered_aggregate(base, ends, len(bars), predicate)


def volume_filter(base, ends, aggregate):
    means = []
    for index in range(len(aggregate)):
        history = aggregate[max(0, index - 50) : index]
        means.append(
            sum((item.volume for item in history), Decimal("0")) / len(history) if history else None
        )
    return filtered_aggregate(
        base,
        ends,
        len(base),
        lambda index: means[index] is not None and aggregate[index].volume > means[index],
    )


def spacing_filter(base, ends, aggregate, periods, margin):
    series = tuple(simple_moving_average(aggregate, period) for period in periods)

    def predicate(index):
        values = tuple(stream[index] for stream in series)
        return all(value is not None for value in values) and all(
            left > right * (Decimal("1") + margin)
            for left, right in zip(values, values[1:], strict=False)
        )

    return filtered_aggregate(base, ends, len(base), predicate)


def combine_targets(left, right):
    return tuple(
        1 if a == 1 and b == 1 else 0 if a is not None and b is not None else None
        for a, b in zip(left, right, strict=True)
    )


def funding_filter(base, bars, funding, threshold):
    latest = None
    allowed = []
    for events in funding:
        for event in events:
            latest = event.rate
        allowed.append(latest is None or latest <= threshold)
    return tuple(
        1 if target == 1 and allowed[index] else target for index, target in enumerate(base)
    )


def summary(result):
    return {
        "net_return": result.net_return,
        "max_drawdown": result.max_drawdown,
        "completed_trades": result.completed_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_fees": result.total_fees,
        "total_funding": result.total_funding,
    }


def benchmark(bars, start, end):
    selected = [bar for bar in bars if start <= bar.start_ms <= end]
    first = selected[0].close
    curve = [float(bar.close / first) for bar in selected]
    peak, drawdown = curve[0], 0.0
    for value in curve:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return {"net_return": curve[-1] - 1, "max_drawdown": drawdown}


def markdown(payload):
    lines = [
        "# BTC 四 SMA 机制增强研究",
        "",
        f"生成时间：{payload['generated_at']}",
        "",
        "目标：在完整样本和 OOS 中与 BTC 买入持有比较。所有机制只使用信号时刻前已完成数据。",
        "",
        "## OOS 结果",
        "",
        "| 配置 | 机制 | OOS收益 | OOS DD | 交易数 | PF | 完整样本收益 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        oos, full = row["periods"]["oos"], row["periods"]["full"]
        lines.append(
            f"| `{row['timeframe']} {row['sma_periods']}` | {row['mechanism']} | "
            f"{pct(oos['net_return'])} | {pct(oos['max_drawdown'])} | "
            f"{oos['completed_trades']} | {num(oos['profit_factor'])} | {pct(full['net_return'])} |"
        )
    lines += ["", "## B&H 基线", ""]
    for name, value in payload["buy_and_hold"].items():
        lines.append(f"- {name}: {pct(value['net_return'])}, DD {pct(value['max_drawdown'])}")
    lines += [
        "",
        "OOS 正收益不等于已验证 Edge；机制应先在研究/验证区间冻结，再用新数据前向观察。",
        "",
    ]
    return "\n".join(lines)


def iso(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def pct(value):
    return "n/a" if value is None else f"{value:.2%}"


def num(value):
    return "n/a" if value is None else f"{value:.2f}"


if __name__ == "__main__":
    main()
