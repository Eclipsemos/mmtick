#!/usr/bin/env python3
"""Create dependency-free SVG diagnostics for the frozen MACD divergence candidates."""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import UTC, datetime
from pathlib import Path

from research_macd_divergence import load_market, split_boundaries

from mastermind_tick.macd_divergence import (
    DivergenceConfig,
    ExecutionConfig,
    divergence_structures,
    entry_signals,
    indicator_series,
    replay_signals,
    swing_points,
)

WIDTH = 1200
HEIGHT = 620


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("reports/experiments/macd_divergence/2026-08-28/results.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/macd_divergence/2026-08-28/plots"),
    )
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for symbol, payload in source["symbols"].items():
        bars = load_market(symbol)
        selected = payload["selected"]
        structure = DivergenceConfig(**selected["structure"])
        execution = ExecutionConfig(**selected["execution"])
        indicators = indicator_series(bars)
        lows = swing_points(bars, indicators.histogram, structure, "low")
        highs = swing_points(bars, indicators.histogram, structure, "high")
        structures = tuple(
            sorted(
                divergence_structures(lows, indicators.atr, structure.points, "LONG")
                + divergence_structures(highs, indicators.atr, structure.points, "SHORT"),
                key=lambda item: (item.known_at, item.id),
            )
        )
        signals = entry_signals(structures, indicators.histogram)
        start, end = split_boundaries(bars)["oos"]
        replay = replay_signals(
            bars,
            indicators,
            signals,
            execution,
            symbol=symbol,
            timeframe_minutes=15,
            start_index=start,
            end_index=end,
        )
        prefix = args.output_dir / symbol.lower()
        write_curve_csv(prefix.with_name(prefix.name + "-equity-drawdown.csv"), replay)
        (prefix.with_name(prefix.name + "-equity-drawdown.svg")).write_text(
            equity_svg(symbol, replay), encoding="utf-8"
        )
        (prefix.with_name(prefix.name + "-trade-cases.svg")).write_text(
            trade_cases_svg(symbol, bars, indicators, replay.trades), encoding="utf-8"
        )
    print(args.output_dir)


def write_curve_csv(path: Path, replay) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp_ms", "timestamp", "equity", "drawdown"])
        for (timestamp_ms, equity), (_, drawdown) in zip(
            replay.equity_curve, replay.drawdown_curve, strict=True
        ):
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, UTC).isoformat()
            writer.writerow([timestamp_ms, timestamp, f"{equity:.12g}", f"{drawdown:.12g}"])


def equity_svg(symbol: str, replay) -> str:
    left, right, top = 80, WIDTH - 35, 55
    equity = list(replay.equity_curve)
    drawdown = list(replay.drawdown_curve)
    values = [value for _, value in equity] or [replay.initial_equity]
    dd_values = [value * 100 for _, value in drawdown] or [0.0]
    equity_min, equity_max = min(values), max(values)
    if equity_max == equity_min:
        equity_max += 1

    def x(index: int, count: int) -> float:
        return left + (right - left) * index / max(count - 1, 1)

    def ey(value: float) -> float:
        return top + (equity_max - value) / (equity_max - equity_min) * 230

    def dy(value: float) -> float:
        return 330 + (0 - value) / max(abs(min(dd_values)), 1) * 190

    equity_points = " ".join(
        f"{x(i, len(values)):.1f},{ey(value):.1f}" for i, value in enumerate(values)
    )
    dd_points = " ".join(
        f"{x(i, len(dd_values)):.1f},{dy(value):.1f}" for i, value in enumerate(dd_values)
    )
    return svg_document(
        WIDTH,
        HEIGHT,
        f"{symbol} frozen candidate OOS equity and drawdown",
        [
            rect(0, 0, WIDTH, HEIGHT, "#ffffff"),
            text(
                35,
                30,
                f"{symbol} | frozen candidate OOS | trades={replay.total_trades}",
                20,
                "#1f2937",
            ),
            text(left, 48, f"equity ({replay.initial_equity:,.0f} initial)", 13, "#4b5563"),
            line(left, 300, right, 300, "#9ca3af", 1),
            text(left, 320, f"drawdown (min {replay.max_drawdown:.2%})", 13, "#4b5563"),
            polyline(equity_points, "#1565c0", 2),
            polyline(dd_points, "#c62828", 2),
            text(right - 170, 90, f"final {replay.final_equity:,.0f}", 13, "#1565c0"),
            text(right - 170, 350, f"max DD {replay.max_drawdown:.2%}", 13, "#c62828"),
        ],
    )


def trade_cases_svg(symbol: str, bars, indicators, trades) -> str:
    rng = random.Random(20260828)
    winners = [trade for trade in trades if trade.pnl > 0]
    losers = [trade for trade in trades if trade.pnl < 0]
    selected = rng.sample(winners, min(10, len(winners))) + rng.sample(losers, min(10, len(losers)))
    columns, panel_w, panel_h = 4, 300, 235
    rows = max((len(selected) + columns - 1) // columns, 1)
    width, height = columns * panel_w, rows * panel_h + 45
    elements = [
        rect(0, 0, width, height, "#ffffff"),
        text(
            16,
            28,
            f"{symbol} | fixed-seed OOS trade cases (green win / red loss)",
            18,
            "#1f2937",
        ),
    ]
    for case, trade in enumerate(selected):
        col, row = case % columns, case // columns
        elements.extend(
            case_panel(bars, indicators, trade, col * panel_w, row * panel_h + 45, panel_w, panel_h)
        )
    return svg_document(width, height, f"{symbol} trade cases", elements)


def case_panel(bars, indicators, trade, x0, y0, width, height):
    indices = list(trade.point_indices) + [
        next((i for i, bar in enumerate(bars) if bar.start_ms == trade.entry_at_ms), 0),
        next((i for i, bar in enumerate(bars) if bar.end_ms == trade.exit_at_ms), 0),
    ]
    start, end = max(min(indices) - 12, 0), min(max(indices) + 10, len(bars) - 1)
    window = bars[start : end + 1]
    highs = [float(bar.high) for bar in window] + [trade.stop_price, trade.take_profit]
    lows = [float(bar.low) for bar in window] + [trade.stop_price, trade.take_profit]
    pmin, pmax = min(lows), max(highs)
    span = max(pmax - pmin, 1e-12)
    chart_left, chart_right = x0 + 28, x0 + width - 10
    chart_top, chart_bottom = y0 + 28, y0 + 144
    hist_top, hist_bottom = y0 + 158, y0 + height - 18
    hist_mid = (hist_top + hist_bottom) / 2
    hist_values = [
        abs(float(value)) for value in indicators.histogram[start : end + 1] if value is not None
    ] + [abs(value) for value in trade.histograms]
    hist_span = max(max(hist_values, default=0.0), 1e-9)

    def px(i):
        return chart_left + (chart_right - chart_left) * i / max(len(window) - 1, 1)

    def py(v):
        return chart_bottom - (v - pmin) / span * (chart_bottom - chart_top)

    def hy(v):
        return hist_mid - float(v) / hist_span * (hist_bottom - hist_mid) * 0.9

    elements = [
        rect(x0 + 1, y0 + 1, width - 2, height - 2, "#f8fafc", "#cbd5e1"),
        text(
            x0 + 8,
            y0 + 18,
            f"{trade.direction} {trade.exit_reason} {trade.r_multiple:+.2f}R",
            12,
            "#166534" if trade.pnl > 0 else "#b91c1c",
        ),
    ]
    elements.append(line(chart_left, hist_mid, chart_right, hist_mid, "#94a3b8", 1))
    for i, bar in enumerate(window):
        xx = px(i)
        open_, close = float(bar.open), float(bar.close)
        high, low = float(bar.high), float(bar.low)
        color = "#16803c" if close >= open_ else "#c62828"
        elements.append(line(xx, py(low), xx, py(high), color, 1))
        body_top, body_bottom = min(py(open_), py(close)), max(py(open_), py(close))
        elements.append(rect(xx - 2.5, body_top, 5, max(body_bottom - body_top, 1), color))
        hist = indicators.histogram[start + i]
        if hist is not None:
            bar_height = abs(hy(hist) - hist_mid)
            elements.append(
                rect(
                    xx - 2.5,
                    hy(hist) if hist > 0 else hist_mid,
                    5,
                    bar_height,
                    "#607d8b",
                )
            )
    levels = (
        ("SL", trade.stop_price, "#b91c1c"),
        ("TP", trade.take_profit, "#166534"),
        ("Entry", trade.entry_price, "#1d4ed8"),
    )
    for label, value, color in levels:
        elements.extend(
            [
                line(chart_left, py(value), chart_right, py(value), color, 1),
                text(chart_right - 35, py(value) - 2, label, 9, color),
            ]
        )
    for point_index, price in zip(trade.point_indices, trade.prices, strict=True):
        if start <= point_index <= end:
            point_number = trade.point_indices.index(point_index) + 1
            point_x = px(point_index - start)
            hist_value = trade.histograms[point_number - 1]
            elements.extend(
                [
                    circle(point_x, py(price), 3, "#f59e0b"),
                    text(point_x + 4, py(price) - 4, f"P{point_number}", 9, "#b45309"),
                    circle(point_x, hy(hist_value), 3, "#7c3aed"),
                    text(point_x + 4, hy(hist_value) - 4, f"H{point_number}", 9, "#6d28d9"),
                ]
            )
    return elements


def svg_document(width, height, title, elements):
    body = "".join(elements)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}"><title>{escape(title)}</title>{body}</svg>\n'
    )


def rect(x, y, width, height, fill, stroke="none"):
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
        f'fill="{fill}" stroke="{stroke}"/>'
    )


def line(x1, y1, x2, y2, stroke, width):
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{stroke}" stroke-width="{width}"/>'
    )


def polyline(points, stroke, width):
    return f'<polyline points="{points}" fill="none" stroke="{stroke}" stroke-width="{width}"/>'


def text(x, y, value, size, fill):
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="sans-serif" font-size="{size}" '
        f'fill="{fill}">{escape(value)}</text>'
    )


def circle(cx, cy, radius, fill):
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius}" fill="{fill}"/>'


def escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    main()
