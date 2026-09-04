#!/usr/bin/env python3
"""Cross-provider signal sanity audit for the frozen BTC SMA11/40 candidate.

This intentionally models Coinbase spot only.  It validates that the frozen
daily trend rule is not an artifact of Binance closes; it is not a substitute
for the strict Binance perpetual execution, funding, and collateral audit.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from audit_btc_stitched_strict15m_sma10 import build_targets
from research_btc_collateral_architecture import years_between

from mastermind_tick.bar_research import ResearchBar

API_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
CACHE = Path("data/history_btc/BTC-USD-Coinbase-1d.csv")
OUTPUT = Path("reports/experiments/btc_sma11_coinbase_sanity/2026-09-03")
START = datetime(2017, 10, 1, tzinfo=UTC)
FEE_AND_SLIPPAGE = Decimal("0.0015")


@dataclass(frozen=True)
class Result:
    net_return: Decimal
    max_drawdown: Decimal
    rebalances: int


def main() -> None:
    bars = load_coinbase_bars()
    targets = build_targets(
        bars,
        fast_period=11,
        slow_period=40,
        enter_bear_days=2,
        active=Decimal("1.5"),
    )
    endpoint = bars[-1].end_ms
    periods = {
        "research": (utc_ms(2020, 1, 1), utc_ms(2022, 12, 31, 23, 59, 59, 999000)),
        "validation": (utc_ms(2023, 1, 1), utc_ms(2024, 12, 31, 23, 59, 59, 999000)),
        "oos": (utc_ms(2025, 1, 1), endpoint),
        "full": (utc_ms(2017, 10, 1), endpoint),
    }
    results = {}
    for name, bounds in periods.items():
        strategy = replay(bars, targets, *bounds)
        baseline = buy_and_hold(bars, *bounds)
        results[name] = {
            "strategy_return": float(strategy.net_return),
            "benchmark_return": float(baseline.net_return),
            "excess": float(strategy.net_return - baseline.net_return),
            "strategy_cagr": (1 + float(strategy.net_return)) ** (1 / years_between(*bounds)) - 1,
            "strategy_drawdown": float(strategy.max_drawdown),
            "benchmark_drawdown": float(baseline.max_drawdown),
            "rebalances": strategy.rebalances,
        }
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "SIGNAL_SANITY_CHECK_ONLY",
        "protocol": {
            "provider": "Coinbase BTC-USD daily candles",
            "signal": "frozen completed daily SMA11/40, bear after 2 days, recover after 1 day",
            "execution": "following completed daily candle, next daily open",
            "exposure": "1.5x active, 0x bear; simplified spot-margin proxy",
            "costs": "15 bps per changed notional",
            "not_modeled": (
                "perpetual funding, isolated collateral, intraday 15m execution, and liquidation"
            ),
        },
        "data": {"first": iso(bars[0].start_ms), "last": iso(bars[-1].end_ms), "bars": len(bars)},
        "results": results,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def load_coinbase_bars() -> list[ResearchBar]:
    cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = fetch_rows(START, cutoff)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "open", "high", "low", "close", "volume"))
        writer.writerows(rows)
    bars = [
        ResearchBar(
            utc_ms(value.year, value.month, value.day),
            utc_ms(value.year, value.month, value.day, 23, 59, 59, 999000),
            Decimal(open_),
            Decimal(high),
            Decimal(low),
            Decimal(close),
            Decimal(volume),
        )
        for value, open_, high, low, close, volume in rows
    ]
    expected = timedelta(days=1)
    if any(
        right.start_ms - left.start_ms != int(expected.total_seconds() * 1000)
        for left, right in zip(bars, bars[1:], strict=False)
    ):
        raise ValueError("Coinbase daily history has missing calendar days")
    return bars


def fetch_rows(start: datetime, end: datetime) -> list[tuple[datetime, str, str, str, str, str]]:
    rows: dict[datetime, tuple[datetime, str, str, str, str, str]] = {}
    cursor = start
    while cursor < end:
        next_cursor = min(cursor + timedelta(days=250), end)
        query = urlencode(
            {
                "granularity": "86400",
                "start": cursor.isoformat().replace("+00:00", "Z"),
                "end": next_cursor.isoformat().replace("+00:00", "Z"),
            }
        )
        request = Request(f"{API_URL}?{query}", headers={"User-Agent": "mmtick-research/1.0"})
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public API endpoint
            candles = json.load(response)
        for timestamp, low, high, open_, close, volume in candles:
            value = datetime.fromtimestamp(timestamp, UTC)
            if start <= value < end:
                rows[value] = (value, str(open_), str(high), str(low), str(close), str(volume))
        cursor = next_cursor
    return [rows[value] for value in sorted(rows)]


def replay(bars: list[ResearchBar], targets, start_ms: int, end_ms: int) -> Result:
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.start_ms <= end_ms]
    if len(selected) < 2:
        raise ValueError("need at least two daily bars")
    equity = Decimal("1")
    quantity = Decimal("0")
    active_target = Decimal("0")
    peak = equity
    drawdown = Decimal("0")
    rebalances = 0
    for index in selected:
        bar = bars[index]
        desired = (
            Decimal(targets[index - 1])
            if index and targets[index - 1] is not None
            else active_target
        )
        equity = equity + quantity * (bar.open - bars[index - 1].close) if index else equity
        if desired != active_target:
            quantity, equity = rebalance(quantity, equity, bar.open, desired)
            active_target = desired
            rebalances += 1
        close_equity = equity + quantity * (bar.close - bar.open)
        low_equity = equity + quantity * (bar.low - bar.open)
        peak = max(peak, close_equity)
        drawdown = min(drawdown, low_equity / peak - Decimal("1"))
        equity = close_equity
    return Result(equity - Decimal("1"), drawdown, rebalances)


def rebalance(
    quantity: Decimal, equity: Decimal, price: Decimal, target: Decimal
) -> tuple[Decimal, Decimal]:
    proposed = target * equity / price
    for _ in range(8):
        cost = abs(proposed - quantity) * price * FEE_AND_SLIPPAGE
        proposed = target * (equity - cost) / price
    cost = abs(proposed - quantity) * price * FEE_AND_SLIPPAGE
    return proposed, equity - cost


def buy_and_hold(bars: list[ResearchBar], start_ms: int, end_ms: int) -> Result:
    selected = [bar for bar in bars if start_ms <= bar.start_ms <= end_ms]
    quantity = Decimal("1") / selected[0].open
    peak = Decimal("1")
    drawdown = Decimal("0")
    for bar in selected:
        close_equity = quantity * bar.close
        peak = max(peak, close_equity)
        drawdown = min(drawdown, quantity * bar.low / peak - Decimal("1"))
    return Result(quantity * selected[-1].close - Decimal("1"), drawdown, 1)


def render(payload) -> str:
    lines = [
        "# BTC SMA11/40 Coinbase Signal Sanity Audit",
        "",
        "固定 Binance 候选参数，独立 Coinbase 日线只用于复核趋势信号；本报告不是永续合约回放。",
        "",
        "| 区间 | 策略 | B&H | 超额 | CAGR | 策略DD | B&H DD | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["results"].items():
        lines.append(
            f"| {name} | {row['strategy_return']:.2%} | {row['benchmark_return']:.2%} | "
            f"{row['excess']:.2%} | {row['strategy_cagr']:.2%} | {row['strategy_drawdown']:.2%} | "
            f"{row['benchmark_drawdown']:.2%} | {row['rebalances']} |"
        )
    lines += [
        "",
        "成本为每次变更名义金额 15bps；未包含 Funding、逐15分钟滑点、隔离抵押或强平，"
        "故它不能升级主候选状态。若独立源也在留出期超过 B&H，"
        "只能支持信号机制不是单一交易所收盘价伪影。",
        "",
    ]
    return "\n".join(lines)


def utc_ms(
    year: int,
    month: int = 1,
    day: int = 1,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    microsecond: int = 0,
) -> int:
    return int(
        datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC).timestamp() * 1000
    )


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
