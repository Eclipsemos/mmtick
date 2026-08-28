#!/usr/bin/env python3
"""Screen ATR strategy families for BTC and ETH independently."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from audit_btc_atr_stability import candidate_grid, evaluate, selection_score
from mastermind_tick.bar_research import ResearchBar, aggregate_bars, funding_by_bar
from mastermind_tick.models import FundingRate

ASSETS = {"btc_perp": "BTCUSDT", "eth_perp": "ETHUSDT"}
SPLITS = {
    "train": (date(2020, 1, 1), date(2023, 12, 31)),
    "validation": (date(2024, 1, 1), date(2024, 12, 31)),
    "confirmation": (date(2025, 1, 1), date(2025, 12, 31)),
    "diagnostic_2026": (date(2026, 1, 1), date(2026, 8, 21)),
    "forward_observation": (date(2026, 8, 22), date(2026, 12, 31)),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/paper.db"))
    parser.add_argument(
        "--instruments",
        default="btc_perp,eth_perp",
        help="comma-separated independent instruments",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments/individual_crypto_atr/2026-08-28"),
    )
    args = parser.parse_args()
    instruments = tuple(value.strip() for value in args.instruments.split(",") if value.strip())
    unknown = set(instruments) - set(ASSETS)
    if not instruments or unknown:
        raise ValueError(f"unsupported instruments: {', '.join(sorted(unknown)) or 'none'}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for instrument_id in instruments:
        payload = research_asset(args.database, instrument_id)
        stem = instrument_id.removesuffix("_perp")
        (args.output_dir / f"{stem}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (args.output_dir / f"{stem}.md").write_text(markdown(payload), encoding="utf-8")
        summaries.append(
            {
                "instrument_id": instrument_id,
                "symbol": payload["symbol"],
                "status": payload["decision"]["status"],
                "passing_families": payload["decision"]["passing_families"],
            }
        )
        print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def research_asset(database: Path, instrument_id: str) -> dict[str, Any]:
    symbol = ASSETS[instrument_id]
    source_bars, funding_rates = load_market(database, instrument_id)
    latest_date = datetime.fromtimestamp(source_bars[-1].end_ms / 1000, UTC).date()
    periods = {
        name: (_day_start(start), min(_day_end(end), source_bars[-1].end_ms))
        for name, (start, end) in SPLITS.items()
        if start <= latest_date
    }
    bars_by_interval = {
        interval: aggregate_bars(source_bars, interval) for interval in (60, 240, 1440)
    }
    funding_by_interval = {
        interval: funding_by_bar(bars, funding_rates)
        for interval, bars in bars_by_interval.items()
    }
    candidates = candidate_grid(bars_by_interval, funding_by_interval)

    development = []
    for index, candidate in enumerate(candidates, start=1):
        train = evaluate(candidate, periods["train"])
        validation = evaluate(candidate, periods["validation"])
        development.append(
            {
                "candidate": candidate,
                "train": train,
                "validation": validation,
                "score": selection_score(train, validation),
            }
        )
        if index % 36 == 0:
            print(f"{symbol}: screened {index}/{len(candidates)} candidates", flush=True)

    winners = []
    for family in sorted({item["candidate"].family for item in development}):
        family_rows = sorted(
            (item for item in development if item["candidate"].family == family),
            key=lambda item: item["score"],
            reverse=True,
        )
        winner = family_rows[0]
        observations = {
            name: evaluate(winner["candidate"], period)
            for name, period in periods.items()
            if name not in {"train", "validation"}
        }
        neighbors = family_rows[:5]
        neighbor_confirmation = [
            evaluate(item["candidate"], periods["confirmation"]) for item in neighbors
        ]
        neighbor_pass_rate = sum(
            result.net_return > 0 and result.max_drawdown >= -0.25
            for result in neighbor_confirmation
        ) / len(neighbor_confirmation)
        base = {
            "train": winner["train"],
            "validation": winner["validation"],
            **observations,
        }
        stress = {
            name: evaluate(winner["candidate"], period, fee_bps=10, slippage_bps=5)
            for name, period in periods.items()
            if name in {"train", "validation", "confirmation"}
        }
        gates = stability_gates(base, stress, neighbor_pass_rate)
        winners.append(
            {
                "id": winner["candidate"].id,
                "family": family,
                "interval_minutes": winner["candidate"].interval_minutes,
                "direction": winner["candidate"].direction,
                "parameters": winner["candidate"].parameters,
                "selection_score": list(winner["score"]),
                "results": {name: summarize(result) for name, result in base.items()},
                "stress": {name: summarize(result) for name, result in stress.items()},
                "development_top_five_confirmation_pass_rate": neighbor_pass_rate,
                "gates": gates,
                "passes": all(gates.values()),
            }
        )

    passing = [item["family"] for item in winners if item["passes"]]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "instrument_id": instrument_id,
        "symbol": symbol,
        "strategy_scope": "independent ATR research; frozen BTC/ETH portfolio unchanged",
        "data": {
            "database": str(database),
            "source_bars_15m": len(source_bars),
            "first_bar": _timestamp(source_bars[0].start_ms),
            "last_bar": _timestamp(source_bars[-1].end_ms),
            "funding_events": len(funding_rates),
        },
        "execution": {
            "stage": "causal closed-bar screen before tick-level finalist verification",
            "base_costs": "5 bps fee + 2 bps slippage per fill",
            "stress_costs": "10 bps fee + 5 bps slippage per fill",
            "funding": "historical Binance funding",
            "exposure": 1.0,
        },
        "periods": {
            name: {"start": _timestamp(start), "end": _timestamp(end)}
            for name, (start, end) in periods.items()
        },
        "candidate_count": len(candidates),
        "selection": {
            "winner_inputs": ["train", "validation"],
            "confirmation_used_for_selection": False,
            "diagnostic_2026_used_for_selection": False,
            "forward_observation_used_for_selection": False,
            "sequential_research_warning": (
                "The archive and ATR family set have been inspected before. Only observations "
                "recorded after this report can become pristine forward evidence."
            ),
        },
        "family_winners": winners,
        "decision": {
            "status": "tick_verification_candidate" if passing else "no_stable_bar_candidate",
            "passing_families": passing,
            "approved_for_trading": False,
            "next_step": (
                "Replay development-selected family winners on the independent 2024-present "
                "250 ms tick warehouses; do not retune on forward observations."
            ),
        },
    }


def load_market(database: Path, instrument_id: str) -> tuple[list[ResearchBar], list[FundingRate]]:
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        bars = [
            ResearchBar(
                start_ms=int(row[0]),
                end_ms=int(row[1]),
                open=Decimal(row[2]),
                high=Decimal(row[3]),
                low=Decimal(row[4]),
                close=Decimal(row[5]),
                volume=Decimal(row[6]),
            )
            for row in connection.execute(
                """
                SELECT start_ms, end_ms, open, high, low, close, volume
                FROM ohlcv_bars
                WHERE instrument_id = ? AND interval_minutes = 15 AND is_closed = 1
                ORDER BY start_ms
                """,
                (instrument_id,),
            )
        ]
        funding = [
            FundingRate(int(row[0]), Decimal(row[1]), Decimal(row[2]))
            for row in connection.execute(
                """
                SELECT timestamp_ms, rate, mark_price FROM funding_rates
                WHERE instrument_id = ? ORDER BY timestamp_ms
                """,
                (instrument_id,),
            )
        ]
    if len(bars) < 10_000:
        raise ValueError(f"{instrument_id} requires at least 10,000 complete 15m bars")
    return bars, funding


def stability_gates(base: dict[str, Any], stress: dict[str, Any], neighbors: float) -> dict[str, bool]:
    required = ("train", "validation", "confirmation")
    confirmation = base["confirmation"]
    positive_month_rate = sum(value > 0 for _, value in confirmation.monthly_returns) / len(
        confirmation.monthly_returns
    )
    return {
        "development_and_confirmation_positive": all(base[name].net_return > 0 for name in required),
        "drawdown_controlled": all(base[name].max_drawdown >= -0.25 for name in required),
        "confirmation_trades": confirmation.completed_trades >= 12,
        "confirmation_positive_months": positive_month_rate >= 0.55,
        "parameter_neighborhood": neighbors >= 0.60,
        "cost_stress": all(
            stress[name].net_return > 0 and stress[name].max_drawdown >= -0.25
            for name in required
        ),
    }


def summarize(result: Any) -> dict[str, Any]:
    payload = asdict(result)
    payload.pop("trades")
    payload["daily_returns"] = [
        {"date": label, "return": value} for label, value in result.daily_returns
    ]
    payload["monthly_returns"] = [
        {"month": label, "return": value} for label, value in result.monthly_returns
    ]
    payload["positive_month_rate"] = (
        sum(value > 0 for _, value in result.monthly_returns) / len(result.monthly_returns)
        if result.monthly_returns
        else 0.0
    )
    return payload


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['symbol']} 独立 ATR 策略初筛",
        "",
        f"生成时间：`{payload['generated_at']}`",
        "",
        "本研究只分析单一品种，不读取或修改冻结的 BTC/ETH 组合策略。当前阶段使用完整的 "
        "15 分钟 bar 做因果初筛；入选者才进入 2024 年至今 250 ms tick 精确回放。",
        "",
        "## 数据与分割",
        "",
        f"- 数据：`{payload['data']['first_bar']}` 至 `{payload['data']['last_bar']}`，"
        f"{payload['data']['source_bars_15m']:,} 根 15 分钟 bar。",
        "- 开发：2020-2023；验证：2024；确认：2025；2026 年仅作诊断。",
        "- `2026-08-22` 以后保留为 forward observation，不参与选择。",
        "- 基准成本：每次成交 5 bps 手续费 + 2 bps 滑点；压力成本为 10 + 5 bps。",
        "",
        "## 开发期选出的各家族代表",
        "",
        "| 家族 | 候选 | 开发 | 验证 | 确认 | 2026诊断 | Forward | 确认DD | 确认PF | 通过 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for winner in payload["family_winners"]:
        results = winner["results"]
        forward = results.get("forward_observation")
        lines.append(
            f"| {winner['family']} | `{winner['id']}` | {results['train']['net_return']:.2%} | "
            f"{results['validation']['net_return']:.2%} | "
            f"{results['confirmation']['net_return']:.2%} | "
            f"{results['diagnostic_2026']['net_return']:.2%} | "
            f"{forward['net_return']:.2%} | "
            f"{results['confirmation']['max_drawdown']:.2%} | "
            f"{_optional_ratio(results['confirmation']['profit_factor'])} | "
            f"{'是' if winner['passes'] else '否'} |"
        )
    lines.extend(
        [
            "",
            "## 判定",
            "",
            f"状态：`{payload['decision']['status']}`。",
            "",
            "通过全部基础门槛的家族："
            + (", ".join(f"`{name}`" for name in payload["decision"]["passing_families"]) or "无")
            + "。",
            "",
            payload["selection"]["sequential_research_warning"],
            "",
            payload["decision"]["next_step"],
            "",
            "本报告不批准模拟盘或实盘。",
            "",
        ]
    )
    return "\n".join(lines)


def _optional_ratio(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "n/a"


def _day_start(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), UTC).timestamp() * 1000)


def _day_end(value: date) -> int:
    return _day_start(value + timedelta(days=1)) - 1


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


if __name__ == "__main__":
    main()
