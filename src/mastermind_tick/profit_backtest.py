"""Compare baseline, fixed ATR take profit, and ATR profit protection."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mastermind_tick.backtest import ReplayParameters, run_parameter_grid
from mastermind_tick.config import InstrumentSettings, Settings, load_settings

DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")


def policies(settings: Settings) -> list[ReplayParameters]:
    period = settings.strategy.atr_period
    multiplier = settings.strategy.atr_multiplier
    return [
        ReplayParameters(period, multiplier, variant="baseline"),
        ReplayParameters(
            period,
            multiplier,
            variant="fixed_6atr",
            fixed_take_profit_atr=6,
        ),
        ReplayParameters(
            period,
            multiplier,
            variant="protect_2atr_trail_2_5atr",
            profit_activation_atr=2,
            profit_trailing_atr=2.5,
        ),
    ]


def shared_cutoff(settings: Settings, instruments: list[InstrumentSettings]) -> int:
    database_uri = f"file:{settings.database_path}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        cutoffs = []
        for instrument in instruments:
            row = connection.execute(
                "SELECT MAX(timestamp_ms) FROM agg_trades WHERE instrument_id = ?",
                (instrument.id,),
            ).fetchone()
            if row is None or row[0] is None:
                raise ValueError(f"no aggTrade data for {instrument.id}")
            cutoffs.append(int(row[0]))
    return min(cutoffs)


def data_gap_summary(
    settings: Settings,
    instrument: InstrumentSettings,
    start_ms: int,
    end_ms: int,
) -> dict[str, int]:
    database_uri = f"file:{settings.database_path}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        if instrument.paper_model == "futures":
            row = connection.execute(
                """
                WITH ordered AS (
                    SELECT first_trade_id, last_trade_id,
                           LAG(last_trade_id) OVER (
                               ORDER BY timestamp_ms, received_at_ms, event_id
                           ) AS previous_last
                    FROM agg_trades
                    WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
                )
                SELECT COUNT(*) AS gap_count,
                       COALESCE(SUM(first_trade_id - previous_last - 1), 0) AS missing_ids
                FROM ordered
                WHERE previous_last IS NOT NULL AND first_trade_id > previous_last + 1
                """,
                (instrument.id, start_ms, end_ms),
            ).fetchone()
        else:
            row = connection.execute(
                """
                WITH ordered AS (
                    SELECT aggregate_trade_id,
                           LAG(aggregate_trade_id) OVER (
                               ORDER BY timestamp_ms, received_at_ms, event_id
                           ) AS previous_id
                    FROM agg_trades
                    WHERE instrument_id = ? AND timestamp_ms BETWEEN ? AND ?
                      AND aggregate_trade_id IS NOT NULL
                )
                SELECT COUNT(*) AS gap_count,
                       COALESCE(SUM(aggregate_trade_id - previous_id - 1), 0) AS missing_ids
                FROM ordered
                WHERE previous_id IS NOT NULL AND aggregate_trade_id > previous_id + 1
                """,
                (instrument.id, start_ms, end_ms),
            ).fetchone()
    return {"gap_count": int(row[0]), "missing_trade_ids": int(row[1])}


def build_profit_report(payload: dict[str, Any]) -> str:
    lines = [
        "# ATR Profit Exit Comparison",
        "",
        f"Generated: {payload['generated_at']}",
        f"Shared data cutoff: {_display_time(payload['cutoff_ms'])}",
        "",
        "All variants use ATR(21) x 4 entries and base trailing stop. Orders fill on the next "
        "stored Tick with configured Taker fees, slippage, leverage and funding.",
        "",
        "- `baseline`: current strategy, no additional take profit.",
        "- `fixed_6atr`: exit after a favorable move of 6 x entry ATR.",
        "- `protect_2atr_trail_2_5atr`: after a 2 x entry ATR favorable move, activate a "
        "one-way 2.5 x current ATR profit stop.",
        "",
        "Profit exits flatten the position and do not reverse it. Re-entry still requires the "
        "production strategy's normal signal rules.",
        "",
    ]
    for run in payload["runs"]:
        metadata = run["metadata"]
        gaps = run["data_gaps"]
        lines.extend(
            [
                f"## {metadata['symbol']} ({metadata['paper_model']})",
                "",
                (
                    f"Range: {_display_time(metadata['start_ms'])} to "
                    f"{_display_time(metadata['end_ms'])}; "
                    f"{metadata['tick_count']:,} stored ticks / "
                    f"{metadata['raw_trade_count']:,} underlying trades."
                ),
                "",
                (
                    f"Data continuity: {gaps['gap_count']:,} trade-ID gaps, "
                    f"{gaps['missing_trade_ids']:,} missing trade IDs. "
                    f"Execution: {metadata['leverage']}x leverage, "
                    f"{metadata['target_exposure']:.2f}x target exposure, "
                    f"{metadata['fee_bps']:.1f} bps fee + "
                    f"{metadata['slippage_bps']:.1f} bps slippage per fill."
                ),
                "",
                (
                    "| Variant | Net return | Net PnL | Final equity | Trades | Win rate | "
                    "Profit factor | Max DD | Fees | Funding | Profit exits | End |"
                ),
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for item in run["results"]:
            win_rate = "--" if item["win_rate"] is None else f"{item['win_rate']:.2%}"
            profit_factor = (
                "--" if item["profit_factor"] is None else f"{item['profit_factor']:.2f}"
            )
            lines.append(
                f"| `{item['variant']}` | {item['net_return']:.2%} | "
                f"{item['net_profit']:,.2f} | {item['final_equity']:,.2f} | "
                f"{item['completed_trades']} | {win_rate} | {profit_factor} | "
                f"{item['max_drawdown']:.2%} | {item['total_fees']:,.2f} | "
                f"{item['total_funding']:,.2f} | {item['profit_exit_signals']} | "
                f"{item['ending_position']} |"
            )
        winner = max(run["results"], key=lambda item: item["net_profit"])
        lines.extend(
            [
                "",
                f"Highest net profit in this sample: `{winner['variant']}` at "
                f"{winner['net_profit']:,.2f} ({winner['net_return']:.2%}).",
                "",
            ]
        )

    lines.extend(
        [
            "## Combined Accounts",
            "",
            "Each instrument starts with an independent 100,000 USDT account.",
            "",
            "| Variant | Combined net PnL | Combined final equity |",
            "|---|---:|---:|",
        ]
    )
    for item in payload["combined"]:
        lines.append(
            f"| `{item['variant']}` | {item['net_profit']:,.2f} | "
            f"{item['final_equity']:,.2f} |"
        )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "The sample spans only a few days and is not an out-of-sample validation. Open "
            "positions are marked to the final Tick, so net PnL includes unrealized PnL. "
            "Futures records are 250 ms buckets; intrabucket price paths are unavailable. "
            "Trade-ID gaps identify warehouse outages and can change simulated signals.",
            "",
        ]
    )
    return "\n".join(lines)


def _display_time(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, DISPLAY_TIMEZONE).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare ATR profit exit variants")
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--instrument", action="append", dest="instruments")
    parser.add_argument("--output-dir", default="reports/profit_exits")
    parser.add_argument("--cutoff-ms", type=int)
    args = parser.parse_args()

    settings = load_settings(args.config)
    selected_ids = set(args.instruments or [item.id for item in settings.instruments])
    instruments = [item for item in settings.instruments if item.id in selected_ids]
    missing = selected_ids - {item.id for item in instruments}
    if missing:
        raise ValueError(f"unknown instruments: {', '.join(sorted(missing))}")
    cutoff_ms = args.cutoff_ms or shared_cutoff(settings, instruments)

    runs = []
    for instrument in instruments:
        print(f"Replaying profit exits for {instrument.id}...", flush=True)
        metadata, results = run_parameter_grid(
            settings,
            instrument,
            policies(settings),
            end_ms=cutoff_ms,
        )
        result_values = [asdict(item) for item in results]
        runs.append(
            {
                "metadata": metadata,
                "data_gaps": data_gap_summary(
                    settings,
                    instrument,
                    metadata["start_ms"],
                    metadata["end_ms"],
                ),
                "results": result_values,
            }
        )
        for item in result_values:
            print(
                f"  {item['variant']}: {item['net_profit']:,.2f} "
                f"({item['net_return']:.2%})",
                flush=True,
            )

    combined = []
    for variant in (item.variant for item in policies(settings)):
        selected = [
            item
            for run in runs
            for item in run["results"]
            if item["variant"] == variant
        ]
        combined.append(
            {
                "variant": variant,
                "net_profit": sum(item["net_profit"] for item in selected),
                "final_equity": sum(item["final_equity"] for item in selected),
            }
        )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cutoff_ms": cutoff_ms,
        "runs": runs,
        "combined": combined,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"atr_profit_exit_comparison_{stamp}.json"
    markdown_path = output_dir / f"atr_profit_exit_comparison_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    markdown_path.write_text(build_profit_report(payload))
    print(json_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
