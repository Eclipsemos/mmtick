#!/usr/bin/env python3
"""Screen conservative Binance BTC spot/perpetual cash-and-carry rules.

The model buys BTC spot with half of account equity and shorts the same BTC quantity
in USD-M perpetuals. It never borrows spot, keeps gross notional near 1X, and only
opens after a completed 4h bar reports positive basis and settled Funding.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from research_btc_sma_trend import load_funding, load_market

from mastermind_tick.bar_research import ResearchBar, funding_by_bar

SPOT_DIR = Path("data/history_btc_spot")
OUTPUT = Path("reports/experiments/btc_cash_carry/2026-09-03")
FEE_BPS = Decimal("10")
SLIPPAGE_BPS = Decimal("5")
COST_RATE = (FEE_BPS + SLIPPAGE_BPS) / Decimal("10000")
PAIR_NOTIONAL_SHARE = Decimal("0.5")
BASIS_THRESHOLDS = (Decimal("0"), Decimal("0.0005"), Decimal("0.001"), Decimal("0.002"))
FUNDING_THRESHOLDS = (Decimal("0"), Decimal("0.000025"), Decimal("0.00005"), Decimal("0.0001"))
DISPLAY_ROWS = 16


@dataclass(frozen=True)
class PairBar:
    start_ms: int
    end_ms: int
    spot_open: Decimal
    spot_high: Decimal
    spot_low: Decimal
    spot_close: Decimal
    perp_open: Decimal
    perp_high: Decimal
    perp_low: Decimal
    perp_close: Decimal


@dataclass(frozen=True)
class CarryCandidate:
    basis_threshold: Decimal
    funding_threshold: Decimal

    @property
    def id(self) -> str:
        basis = bps_label(self.basis_threshold)
        funding = bps_label(self.funding_threshold)
        return f"cash-carry-basis-ge-{basis}bps-funding-ge-{funding}bps"


def candidate_library() -> tuple[CarryCandidate, ...]:
    return tuple(
        CarryCandidate(basis, funding)
        for basis in BASIS_THRESHOLDS
        for funding in FUNDING_THRESHOLDS
    )


def bps_label(value: Decimal) -> str:
    rendered = format((value * Decimal("10000")).normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered.replace(".", "p") or "0"


def load_spot_bars(directory: Path = SPOT_DIR) -> list[ResearchBar]:
    bars: list[ResearchBar] = []
    for path in sorted(directory.glob("BTCUSDT-15m-*.zip")):
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"expected one spot CSV in {path}")
            with archive.open(members[0]) as handle:
                reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8"))
                for row in reader:
                    if not row or not row[0].isdigit():
                        continue
                    bars.append(
                        ResearchBar(
                            start_ms=normalize_archive_timestamp(row[0]),
                            end_ms=normalize_archive_timestamp(row[6]),
                            open=Decimal(row[1]),
                            high=Decimal(row[2]),
                            low=Decimal(row[3]),
                            close=Decimal(row[4]),
                            volume=Decimal(row[5]),
                        )
                    )
    bars.sort(key=lambda bar: bar.start_ms)
    if len({bar.start_ms for bar in bars}) != len(bars):
        raise ValueError("duplicate spot 15m bars")
    return bars


def normalize_archive_timestamp(value: str) -> int:
    """Binance spot archives changed from millisecond to microsecond epochs in 2025."""
    timestamp = int(value)
    return timestamp // 1000 if timestamp >= 10_000_000_000_000 else timestamp


def align_bars(spot: list[ResearchBar], perp: list[ResearchBar]) -> list[PairBar]:
    spot_by_start = {bar.start_ms: bar for bar in spot}
    result = []
    for future in perp:
        cash = spot_by_start.get(future.start_ms)
        if cash is None or cash.end_ms != future.end_ms:
            continue
        result.append(
            PairBar(
                future.start_ms,
                future.end_ms,
                cash.open,
                cash.high,
                cash.low,
                cash.close,
                future.open,
                future.high,
                future.low,
                future.close,
            )
        )
    return result


def gap_count(bars: list[PairBar]) -> int:
    if not bars:
        raise ValueError("no aligned spot/perpetual bars")
    return sum(
        right.start_ms - left.start_ms != 900_000
        for left, right in zip(bars, bars[1:], strict=False)
    )


def four_hour_signal_indices(bars: list[PairBar]) -> tuple[int, ...]:
    indices = []
    index = 0
    while index < len(bars):
        bucket = bars[index].start_ms // 14_400_000 * 14_400_000
        end = index
        while end < len(bars) and bars[end].start_ms < bucket + 14_400_000:
            end += 1
        if end - index == 16 and bars[index].start_ms == bucket:
            indices.append(end - 1)
        index = end
    return tuple(indices)


def carry_targets(
    bars: list[PairBar],
    funding: list[list],
    candidate: CarryCandidate,
) -> tuple[Decimal | None, ...]:
    """Emit a target after each completed 4h bar using only settled Funding."""
    if len(bars) != len(funding):
        raise ValueError("funding and pair bars must align")
    targets: list[Decimal | None] = [None] * len(bars)
    last_funding = None
    cursor = 0
    for end_index in four_hour_signal_indices(bars):
        while cursor <= end_index:
            if funding[cursor]:
                last_funding = funding[cursor][-1].rate
            cursor += 1
        basis = bars[end_index].perp_close / bars[end_index].spot_close - Decimal("1")
        targets[end_index] = Decimal(
            "1"
            if last_funding is not None
            and basis >= candidate.basis_threshold
            and last_funding >= candidate.funding_threshold
            else "0"
        )
    return tuple(targets)


def split_periods(last_end: int) -> dict[str, tuple[int, int]]:
    return {
        "research": (utc_ms(2020), utc_ms(2022, 12, 31, 23, 59, 59, 999000)),
        "validation": (utc_ms(2023), utc_ms(2024, 12, 31, 23, 59, 59, 999000)),
        "oos": (utc_ms(2025), last_end),
        "full": (utc_ms(2020), last_end),
    }


def carry_replay(
    bars: list[PairBar],
    targets: tuple[Decimal | None, ...],
    funding: list[list],
    start_ms: int,
    end_ms: int,
) -> dict[str, float | bool | int]:
    selected = [index for index, bar in enumerate(bars) if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        raise ValueError("requested period has no pair bars")
    pending = Decimal("0")
    for prior in range(selected[0] - 1, -1, -1):
        if targets[prior] is not None:
            pending = Decimal(targets[prior])
            break
    cash = Decimal("100000")
    quantity = Decimal("0")
    entry_perp = Decimal("0")
    funding_pnl = Decimal("0")
    peak = cash
    max_drawdown = Decimal("0")
    max_gross_leverage = Decimal("0")
    rebalances = 0
    fees = Decimal("0")
    forced_gap_flats = 0
    previous_bar = None

    def equity(spot_price: Decimal, perp_price: Decimal) -> Decimal:
        return cash + quantity * spot_price - quantity * (perp_price - entry_perp) + funding_pnl

    for index in selected:
        bar = bars[index]
        if previous_bar is not None and bar.start_ms - previous_bar.start_ms != 900_000:
            if quantity:
                closing_cost = (
                    quantity * (previous_bar.spot_close + previous_bar.perp_close) * COST_RATE
                )
                cash = equity(previous_bar.spot_close, previous_bar.perp_close) - closing_cost
                fees += (
                    quantity
                    * (previous_bar.spot_close + previous_bar.perp_close)
                    * FEE_BPS
                    / Decimal("10000")
                )
                quantity = Decimal("0")
                entry_perp = Decimal("0")
                funding_pnl = Decimal("0")
                rebalances += 1
                forced_gap_flats += 1
            pending = Decimal("0")
        active = Decimal("1") if quantity else Decimal("0")
        if pending != active:
            current_equity = equity(bar.spot_open, bar.perp_open)
            if pending:
                quantity = PAIR_NOTIONAL_SHARE * current_equity / bar.spot_open
                opening_cost = quantity * (bar.spot_open + bar.perp_open) * COST_RATE
                cash -= quantity * bar.spot_open + opening_cost
                fees += quantity * (bar.spot_open + bar.perp_open) * FEE_BPS / Decimal("10000")
                entry_perp = bar.perp_open
                funding_pnl = Decimal("0")
            else:
                closing_cost = quantity * (bar.spot_open + bar.perp_open) * COST_RATE
                cash = equity(bar.spot_open, bar.perp_open) - closing_cost
                fees += quantity * (bar.spot_open + bar.perp_open) * FEE_BPS / Decimal("10000")
                quantity = Decimal("0")
                entry_perp = Decimal("0")
                funding_pnl = Decimal("0")
            rebalances += 1
        for event in funding[index]:
            funding_pnl += quantity * event.mark_price * event.rate
        close_equity = equity(bar.spot_close, bar.perp_close)
        worst_equity = equity(bar.spot_low, bar.perp_high)
        if quantity and worst_equity > 0:
            max_gross_leverage = max(
                max_gross_leverage,
                quantity * (bar.spot_low + bar.perp_high) / worst_equity,
            )
        peak = max(peak, close_equity)
        max_drawdown = min(max_drawdown, close_equity / peak - Decimal("1"))
        if worst_equity <= 0:
            return {
                "net_return": -1.0,
                "max_drawdown": -1.0,
                "maximum_gross_leverage": float("inf"),
                "liquidated": True,
                "fees": float(fees),
                "rebalances": rebalances,
                "forced_gap_flats": forced_gap_flats,
            }
        max_drawdown = min(max_drawdown, worst_equity / peak - Decimal("1"))
        if targets[index] is not None:
            pending = Decimal(targets[index])
        previous_bar = bar
    final = bars[selected[-1]]
    if quantity:
        closing_cost = quantity * (final.spot_close + final.perp_close) * COST_RATE
        cash = equity(final.spot_close, final.perp_close) - closing_cost
        fees += quantity * (final.spot_close + final.perp_close) * FEE_BPS / Decimal("10000")
    return {
        "net_return": float(cash / Decimal("100000") - Decimal("1")),
        "max_drawdown": float(max_drawdown),
        "maximum_gross_leverage": float(max_gross_leverage),
        "liquidated": False,
        "fees": float(fees),
        "rebalances": rebalances,
        "forced_gap_flats": forced_gap_flats,
    }


def buy_and_hold(bars: list[PairBar], start_ms: int, end_ms: int) -> dict[str, float]:
    selected = [bar for bar in bars if start_ms <= bar.start_ms <= end_ms]
    if not selected:
        raise ValueError("requested period has no BTC benchmark bars")
    first = selected[0].spot_close
    peak = first
    drawdown = Decimal("0")
    for bar in selected:
        peak = max(peak, bar.spot_close)
        drawdown = min(drawdown, bar.spot_low / peak - Decimal("1"))
    return {
        "net_return": float(selected[-1].spot_close / first - Decimal("1")),
        "max_drawdown": float(drawdown),
    }


def public(result: dict, benchmark: dict) -> dict:
    return {
        **result,
        "benchmark_return": benchmark["net_return"],
        "benchmark_drawdown": benchmark["max_drawdown"],
        "excess": result["net_return"] - benchmark["net_return"],
    }


def qualifies(metrics: dict[str, dict]) -> bool:
    return all(
        row["excess"] > 0 and row["maximum_gross_leverage"] <= 3 and not row["liquidated"]
        for row in metrics.values()
    )


def main() -> None:
    spot = load_spot_bars()
    futures = load_market("BTCUSDT")
    pairs = align_bars(spot, futures)
    futures_by_start = {bar.start_ms: index for index, bar in enumerate(futures)}
    future_funding = funding_by_bar(futures, load_funding("BTCUSDT", futures))
    funding = [future_funding[futures_by_start[bar.start_ms]] for bar in pairs]
    bounds = split_periods(pairs[-1].end_ms)
    benchmarks = {name: buy_and_hold(pairs, *period) for name, period in bounds.items()}
    rows = []
    for candidate in candidate_library():
        targets = carry_targets(pairs, funding, candidate)
        development = {
            name: public(carry_replay(pairs, targets, funding, *bounds[name]), benchmarks[name])
            for name in ("research", "validation")
        }
        rows.append(
            {
                "id": candidate.id,
                "candidate": {
                    "basis_threshold": float(candidate.basis_threshold),
                    "funding_threshold": float(candidate.funding_threshold),
                    "pair_notional_share": float(PAIR_NOTIONAL_SHARE),
                },
                "development": development,
                "development_min_excess": min(row["excess"] for row in development.values()),
                "targets": targets,
            }
        )
    rows.sort(key=lambda row: row["development_min_excess"], reverse=True)
    qualifying = [row for row in rows if qualifies(row["development"])]
    for row in qualifying:
        row["oos"] = public(
            carry_replay(pairs, row["targets"], funding, *bounds["oos"]), benchmarks["oos"]
        )
        row["full"] = public(
            carry_replay(pairs, row["targets"], funding, *bounds["full"]), benchmarks["full"]
        )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY / NOT_PROMOTED",
        "protocol": {
            "structure": "cash-only spot long plus equal-BTC perpetual short; no spot borrowing",
            "signal": "completed 4h BTC perp/spot basis and last settled Funding",
            "execution": "next 15m open; both legs pay 10 bps fee + 5 bps slippage per side",
            "funding": "historical Funding paid to the perpetual short",
            "selection": "Research 2020-2022 and Validation 2023-2024 only",
            "oos": "2025 through last common spot/perpetual 15m bar; unread unless qualified",
            "leverage": "pair uses 50% equity spot notional; gross intrabar leverage audited <=3X",
        },
        "data": {
            "pair_bars": len(pairs),
            "futures_data_gaps": gap_count(pairs),
            "first": iso(pairs[0].start_ms),
            "last": iso(pairs[-1].end_ms),
            "funding_events": sum(len(events) for events in funding),
        },
        "candidate_count": len(rows),
        "development_qualifying_count": len(qualifying),
        "benchmarks": benchmarks,
        "results": [{key: value for key, value in row.items() if key != "targets"} for row in rows],
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "README.md").write_text(render(payload), encoding="utf-8")
    print(OUTPUT / "README.md")


def render(payload: dict) -> str:
    lines = [
        "# BTC Spot-Perpetual Cash-and-Carry Screen",
        "",
        (
            "自有现金买入 BTC 现货并等数量做空永续，不借币；以已完成 4h basis 与已结算 Funding "
            "决定下一根 15m 开盘是否持有。"
        ),
        "",
        "| 配置 | R超额 | V超额 | 开发最差 | R DD | V DD |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in payload["results"][:DISPLAY_ROWS]:
        research = row["development"]["research"]
        validation = row["development"]["validation"]
        lines.append(
            f"| `{row['id']}` | {research['excess']:.2%} | {validation['excess']:.2%} | "
            f"{row['development_min_excess']:.2%} | {research['max_drawdown']:.2%} | "
            f"{validation['max_drawdown']:.2%} |"
        )
    lines += [
        "",
        (
            "开发期合格成员："
            f"{payload['development_qualifying_count']} / {payload['candidate_count']}。"
        ),
        "只有开发期同时超过 BTC 现货 B&H、无破产且盘中总名义杠杆不超过 3X 的成员才读取 OOS。",
        "",
    ]
    return "\n".join(lines)


def utc_ms(year, month=1, day=1, hour=0, minute=0, second=0, microsecond=0) -> int:
    return int(
        datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC).timestamp() * 1000
    )


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
