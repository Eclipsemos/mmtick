#!/usr/bin/env python3
"""Compare SOXL ATR stop-exit lock repair policies over all local history."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from mastermind_tick.backtest import ReplayParameters, run_parameter_grid
from mastermind_tick.config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/settings.toml")
    parser.add_argument("--instrument", default="soxl_perp")
    parser.add_argument(
        "--output",
        default="reports/experiments/soxl_atr/soxl-stop-lock-repairs-full-history.json",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    instrument = next(item for item in settings.instruments if item.id == args.instrument)
    period = settings.strategy.atr_period
    multiplier = settings.strategy.atr_multiplier
    parameters = [
        ReplayParameters(period, multiplier, variant="baseline", stop_exit_policy="baseline"),
        ReplayParameters(
            period,
            multiplier,
            variant="bypass_action_lock",
            stop_exit_policy="bypass_action_lock",
        ),
        ReplayParameters(
            period,
            multiplier,
            variant="latch_next_bar",
            stop_exit_policy="latch_next_bar",
        ),
    ]

    progress_bucket = -1

    def progress(value: float) -> None:
        nonlocal progress_bucket
        bucket = min(10, int(value * 10))
        if bucket > progress_bucket:
            progress_bucket = bucket
            print(f"progress={value:.0%}", flush=True)

    metadata, results = run_parameter_grid(
        settings,
        instrument,
        parameters,
        direction="long_only",
        live_startup=True,
        progress_callback=progress,
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "metadata": metadata,
        "results": [asdict(result) for result in results],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# SOXLUSDT ATR Stop-Exit Lock Repair Backtest",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Replay: `{datetime.fromtimestamp(metadata['start_ms'] / 1000, UTC).isoformat()}` to "
        f"`{datetime.fromtimestamp(metadata['end_ms'] / 1000, UTC).isoformat()}`",
        f"- Data: {metadata['tick_count']:,} stored ticks / "
        f"{metadata['raw_trade_count']:,} underlying trades; "
        f"{metadata['warmup_bars']} warmup bars.",
        "- Frozen strategy: ATR(32) x 3, long-only, live-startup, 2x leverage x 62.5% allocation.",
        "- Costs: 5 bps fee and 2 bps slippage per fill; Funding included.",
        "",
        "## Policies",
        "",
        "- `baseline`: current one-action-per-bar lock; a stop crossing during the lock "
        "is discarded.",
        "- `bypass_action_lock`: a reduce-only ATR stop exit may signal even on the entry "
        "bar; fill remains next Tick.",
        "- `latch_next_bar`: a stop crossing during the lock is remembered and signaled on "
        "the first eligible Tick of the next bar.",
        "",
        "## Results",
        "",
        "| Policy | Net PnL | Return | Delta vs baseline | Max DD | Trades | Wins | Win rate | "
        "Profit factor | Fees | Funding | All signals | Profit/latched exits |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    baseline_return = results[0].net_return
    for result in results:
        win_rate = "--" if result.win_rate is None else f"{result.win_rate:.2%}"
        profit_factor = "--" if result.profit_factor is None else f"{result.profit_factor:.3f}"
        lines.append(
            f"| {result.variant} | ${result.net_profit:,.2f} | {result.net_return:.2%} | "
            f"{result.net_return - baseline_return:+.2%} | {result.max_drawdown:.2%} | "
            f"{result.completed_trades} | {result.winning_trades} | {win_rate} | {profit_factor} | "
            f"${result.total_fees:,.2f} | ${result.total_funding:,.2f} | "
            f"{result.signals} | {result.profit_exit_signals} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is a full local-history replay, not an out-of-sample guarantee. The two "
            "repair policies change only stop-exit timing; all candidates use the same ticks, "
            "warmup, costs, Funding, and next-Tick execution model. The latch policy is "
            "intentionally distinct from bypass: it waits for the next bar after a locked "
            "crossing.",
            "",
        ]
    )
    report = output.with_suffix(".md")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(report)
    print(output)


if __name__ == "__main__":
    main()
