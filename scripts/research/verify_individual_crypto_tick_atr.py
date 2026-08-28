#!/usr/bin/env python3
"""Verify the frozen BTC/ETH 15-minute Tick ATR grid in separate warehouses."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from mastermind_tick.backtest import ReplayParameters, ReplayResult, run_parameter_grid
from mastermind_tick.config import load_settings
from mastermind_tick.research import research_presets

ASSETS = {
    "btc_perp": ("BTCUSDT", Path("data/btc_tick.db")),
    "eth_perp": ("ETHUSDT", Path("data/eth_tick.db")),
}
PERIODS = {
    "development_2024": ("2024-02-01T00:00:00+00:00", "2024-12-31T23:59:59.999+00:00"),
    "validation_2025": ("2025-01-01T00:00:00+00:00", "2025-12-31T23:59:59.999+00:00"),
    "confirmation_2026": ("2026-01-01T00:00:00+00:00", "2026-08-21T23:59:59.999+00:00"),
    "forward_observation": ("2026-08-22T00:00:00+00:00", None),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--instrument", choices=tuple(ASSETS), required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--all-candidates",
        action="store_true",
        help="run all nine frozen candidates in every split instead of finalists only",
    )
    args = parser.parse_args()

    symbol, default_database = ASSETS[args.instrument]
    database = (args.database or default_database).resolve()
    settings = load_settings(args.config)
    settings = replace(settings, database_path=database)
    instrument = research_presets(settings)[args.instrument].instrument
    parameters = frozen_parameters()

    development = replay(
        settings, instrument, parameters, "development_2024", *PERIODS["development_2024"]
    )
    validation = replay(
        settings, instrument, parameters, "validation_2025", *PERIODS["validation_2025"]
    )
    by_development = keyed(development["results"])
    by_validation = keyed(validation["results"])
    winner_key = select_development_winner(by_development, by_validation)
    finalist_parameters = parameters if args.all_candidates else [parameter_for_key(winner_key)]

    confirmation = replay(
        settings,
        instrument,
        finalist_parameters,
        "confirmation_2026",
        *PERIODS["confirmation_2026"],
    )
    forward = replay(
        settings,
        instrument,
        finalist_parameters,
        "forward_observation",
        *PERIODS["forward_observation"],
    )
    winner_confirmation = keyed(confirmation["results"])[winner_key]
    winner_forward = keyed(forward["results"])[winner_key]
    gates = verification_gates(
        by_development[winner_key], by_validation[winner_key], winner_confirmation
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "instrument_id": args.instrument,
        "symbol": symbol,
        "database": str(database),
        "scope": "independent Tick ATR verification; frozen BTC/ETH portfolio unchanged",
        "protocol": {
            "grid": "ATR periods 14/21/28 x multipliers 2/2.5/3",
            "controls": (
                "15m, long/short, efficiency 8/0.25, reversal confirmation 0.25 ATR, "
                "1.0x exposure, no profit protection or continuation re-entry"
            ),
            "costs": "5 bps fee + 2 bps slippage per fill; historical funding included",
            "winner_inputs": ["development_2024", "validation_2025"],
            "confirmation_used_for_selection": False,
            "forward_used_for_selection": False,
        },
        "winner": {
            "atr_period": winner_key[0],
            "atr_multiplier": winner_key[1],
            "development": asdict(by_development[winner_key]),
            "validation": asdict(by_validation[winner_key]),
            "confirmation": asdict(winner_confirmation),
            "forward_observation": asdict(winner_forward),
            "gates": gates,
        },
        "development": development,
        "validation": validation,
        "confirmation": confirmation,
        "forward_observation": forward,
        "decision": {
            "status": "forward_candidate" if all(gates.values()) else "rejected",
            "approved_for_trading": False,
            "reason": (
                "The development-selected candidate passed all pre-forward tick gates."
                if all(gates.values())
                else "The development-selected candidate failed one or more pre-forward tick gates."
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown(payload), encoding="utf-8")
    print(args.output)
    print(args.output.with_suffix(".md"))


def frozen_parameters() -> list[ReplayParameters]:
    return [
        ReplayParameters(period, multiplier, variant="independent_crypto_tick_atr")
        for period in (14, 21, 28)
        for multiplier in (2.0, 2.5, 3.0)
    ]


def parameter_for_key(key: tuple[int, float]) -> ReplayParameters:
    return ReplayParameters(key[0], key[1], variant="independent_crypto_tick_atr")


def replay(settings, instrument, parameters, label: str, start: str, end: str | None) -> dict:
    print(f"{instrument.symbol}: replaying {label} ({len(parameters)} candidates)", flush=True)
    metadata, results = run_parameter_grid(
        settings,
        instrument,
        parameters,
        start_ms=timestamp_ms(start),
        end_ms=timestamp_ms(end) if end else None,
        direction="long_short",
    )
    return {"metadata": metadata, "results": [asdict(result) for result in results]}


def keyed(results: list[dict] | list[ReplayResult]) -> dict[tuple[int, float], ReplayResult]:
    mapped = {}
    for value in results:
        result = ReplayResult(**value) if isinstance(value, dict) else value
        mapped[(result.atr_period, result.atr_multiplier)] = result
    return mapped


def select_development_winner(
    development: dict[tuple[int, float], ReplayResult],
    validation: dict[tuple[int, float], ReplayResult],
) -> tuple[int, float]:
    return max(
        development,
        key=lambda key: (
            min(development[key].net_return, validation[key].net_return),
            development[key].net_return + validation[key].net_return,
            min(development[key].max_drawdown, validation[key].max_drawdown),
        ),
    )


def verification_gates(
    development: ReplayResult,
    validation: ReplayResult,
    confirmation: ReplayResult,
) -> dict[str, bool]:
    results = (development, validation, confirmation)
    return {
        "all_pre_forward_splits_positive": all(result.net_return > 0 for result in results),
        "drawdown_controlled": all(result.max_drawdown >= -0.25 for result in results),
        "confirmation_trades": confirmation.completed_trades >= 12,
        "confirmation_profit_factor": (
            confirmation.profit_factor is not None and confirmation.profit_factor > 1
        ),
    }


def markdown(payload: dict) -> str:
    winner = payload["winner"]
    lines = [
        f"# {payload['symbol']} 独立 Tick ATR 复核",
        "",
        f"生成时间：`{payload['generated_at']}`",
        "",
        "该复核不读取或修改冻结的 BTC/ETH 组合策略。成交使用 250 ms 聚合 tick，"
        "并计入手续费、滑点和历史资金费。",
        "",
        "## 选择结果",
        "",
        f"开发和验证段选出的参数为 ATR({winner['atr_period']}) x "
        f"{winner['atr_multiplier']:g}。",
        "",
        "| 区间 | 收益 | 最大回撤 | 交易数 | 胜率 | PF | 手续费 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, title in (
        ("development", "2024开发"),
        ("validation", "2025验证"),
        ("confirmation", "2026-08-21前确认"),
        ("forward_observation", "2026-08-22后Forward"),
    ):
        result = winner[label]
        lines.append(
            f"| {title} | {result['net_return']:.2%} | {result['max_drawdown']:.2%} | "
            f"{result['completed_trades']} | {_optional_percent(result['win_rate'])} | "
            f"{_optional_ratio(result['profit_factor'])} | ${result['total_fees']:,.2f} |"
        )
    failed = [name for name, passed in winner["gates"].items() if not passed]
    lines.extend(
        [
            "",
            "## 判定",
            "",
            f"状态：`{payload['decision']['status']}`。",
            "",
            "失败门槛：" + (", ".join(f"`{name}`" for name in failed) or "无") + "。",
            "",
            payload["decision"]["reason"],
            "",
            "Forward 结果不参与参数选择；本报告不批准模拟盘或实盘。",
            "",
        ]
    )
    return "\n".join(lines)


def timestamp_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def _optional_percent(value: float | None) -> str:
    return f"{value:.2%}" if value is not None else "n/a"


def _optional_ratio(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "n/a"


if __name__ == "__main__":
    main()
