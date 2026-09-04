#!/usr/bin/env python3
"""Document the aligned BTC B&H start-time and drawdown convention."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from research_btc_dynamic_exposure import benchmark
from research_btc_sma_trend import load_market, split_periods

OUTPUT_DIR = Path("reports/experiments/btc_benchmark_alignment/2026-09-02")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    rows = {}
    for split, (start, end) in split_periods(bars).items():
        rows[split] = {
            "legacy_close_only": legacy_benchmark(bars, start, end),
            "aligned_open_intrabar": benchmark(bars, start, end),
        }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "finding": (
            "strategy replay starts at the first selected bar open and audits intrabar lows; "
            "B&H must use the same start and drawdown sampling convention"
        ),
        "results": rows,
        "decision": {
            "authoritative_start": "first selected 15m open",
            "authoritative_drawdown": "intrabar low against prior close-equity peak",
            "legacy_close_only_allowed": False,
        },
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "README.md").write_text(markdown(payload), encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


def legacy_benchmark(bars, start, end):
    selected = [bar for bar in bars if start <= bar.start_ms <= end]
    first_close = selected[0].close
    peak = 1.0
    drawdown = 0.0
    for bar in selected:
        value = float(bar.close / first_close)
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return {
        "net_return": float(selected[-1].close / first_close - 1),
        "max_drawdown": drawdown,
    }


def markdown(payload):
    lines = [
        "# BTC B&H 基准口径审计",
        "",
        "策略从分段第一根15m开盘开始，并以盘中Low审计回撤；B&H现已使用相同口径。",
        "",
        "| 分段 | 旧收益 | 对齐收益 | 差异 | 旧DD | 对齐DD |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for split, result in payload["results"].items():
        old = result["legacy_close_only"]
        new = result["aligned_open_intrabar"]
        lines.append(
            f"| {split} | {pct(old['net_return'])} | {pct(new['net_return'])} | "
            f"{pct(new['net_return'] - old['net_return'])} | "
            f"{pct(old['max_drawdown'])} | {pct(new['max_drawdown'])} |"
        )
    lines += [
        "",
        "统一口径仅小幅改变基准数字，但消除了分段首根K线和盘中回撤的不公平比较。",
        "",
    ]
    return "\n".join(lines)


def pct(value):
    return f"{value:.2%}"


if __name__ == "__main__":
    main()
