#!/usr/bin/env python3
"""Statistical audit for the frozen BTC macro and volatility-scaled 3x candidate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from audit_btc_macro_gated_3x import (
    BLOCK_DAYS,
    BOOTSTRAP_SAMPLES,
    INITIAL_EQUITY,
    WINDOWS,
    assert_reconstruction,
    conclusion,
    evaluate_windows,
    markdown,
    run_bootstraps,
    summarize_windows,
    tail_concentration,
)
from research_btc_block_bootstrap import paired_daily_log_returns
from research_btc_collateral_architecture import replay_segregated
from research_btc_dynamic_exposure import benchmark
from research_btc_macro_vol_scaled_3x import build_candidates
from research_btc_sma_trend import load_funding, load_market, split_periods

from mastermind_tick.bar_research import funding_by_bar

OUTPUT_DIR = Path("reports/experiments/btc_macro_vol_scaled_3x_audit/2026-09-02")
FROZEN_ID = "4h-24-48-96-192-macro1200-vol360-target1.2-max3x"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bars = load_market("BTCUSDT")
    funding = funding_by_bar(bars, load_funding("BTCUSDT", bars))
    selected = next(
        candidate for candidate in build_candidates(bars, funding) if candidate["id"] == FROZEN_ID
    )
    targets = selected["targets"]
    full_start, full_end = split_periods(bars)["full"]
    full_result = replay_segregated(
        bars,
        targets,
        funding,
        full_start,
        full_end,
        spot_cap=Decimal("0"),
        maintenance_rate=Decimal("0.02"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        record_equity=True,
        enforce_effective_leverage_cap=True,
    )
    baseline = benchmark(bars, full_start, full_end)
    strategy_logs, benchmark_logs = paired_daily_log_returns(
        bars,
        full_result.equity_curve,
        INITIAL_EQUITY,
        start_ms=full_start,
    )
    assert_reconstruction(
        full_result.net_return,
        baseline["net_return"],
        strategy_logs,
        benchmark_logs,
    )
    rolling = {}
    for label, days in WINDOWS:
        rows = evaluate_windows(
            bars,
            funding,
            targets,
            days,
            enforce_effective_leverage_cap=True,
        )
        rolling[label] = {"summary": summarize_windows(rows), "rows": rows}
        print(f"completed rolling {label}: {len(rows)}", flush=True)
    bootstrap = run_bootstraps(strategy_logs, benchmark_logs)
    tail = tail_concentration(strategy_logs, benchmark_logs)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "id": FROZEN_ID,
            "volatility_lookback": selected["volatility_lookback"],
            "target_volatility": selected["target_volatility"],
            "maximum_order_leverage": "3",
        },
        "protocol": {
            "rolling_windows": {label: days for label, days in WINDOWS},
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "bootstrap_block_days": BLOCK_DAYS,
            "selection": "none; the development-selected candidate is frozen",
            "costs": "10 bps fee + 5 bps slippage; full futures funding",
            "leverage_control": (
                "at every 15m open, reduce the futures sleeve back to 3x of futures-wallet "
                "equity after mark-to-open losses; reductions incur stress costs"
            ),
        },
        "historical": {
            "strategy_return": full_result.net_return,
            "strategy_max_drawdown": full_result.max_drawdown,
            "benchmark_return": baseline["net_return"],
            "benchmark_max_drawdown": baseline["max_drawdown"],
            "daily_observations": len(strategy_logs),
        },
        "rolling": rolling,
        "bootstrap": bootstrap,
        "tail_concentration": tail,
        "conclusion": conclusion(rolling, bootstrap, tail),
    }
    (OUTPUT_DIR / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    report = markdown(payload).replace(
        "# BTC 3X 宏观门槛候选统计审计",
        "# BTC 宏观门槛 + 波动率缩放 3X 统计审计",
        1,
    )
    (OUTPUT_DIR / "README.md").write_text(report, encoding="utf-8")
    print(OUTPUT_DIR / "README.md")


if __name__ == "__main__":
    main()
