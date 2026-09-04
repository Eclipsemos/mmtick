# BTC SMA10/40 Composite Controls v1

## Status

`RESEARCH_ONLY / FORWARD_OBSERVATION_REQUIRED`. This is the current historical
BTC-versus-B&H research lead. It is not approved for paper or live order submission.

## Rules

- Market: `BTCUSDT`; long-only exposure between `0X`, `1X`, and `1.5X`.
- Base trend: calculate SMA(10) and SMA(40) from completed UTC daily candles.
- Bear state: `close < SMA40` and `SMA10 < SMA40` for two consecutive completed days.
  Enter `0X` at the next available bar open.
- Recovery: one completed non-bear day restores the active state at the next bar open.
- Drawdown control: while active, if the completed daily close is at least 15% below the
  highest completed close in the trailing 90 days, reduce exposure from `1.5X` to `1X`.
- Funding control: from 2020 onward, if the latest settled Funding rate known at execution
  exceeds `0.01%` (`0.0001`), reduce exposure from `1.5X` to `1X`.
- Execution: Binance spot daily bars before 2020; Binance USD-M 15-minute bars from 2020.
  Every signal executes on the next tradable open, never on the signal candle.
- Capital model: 50% spot allocation and 50% isolated USD-M collateral. Futures opening
  leverage is capped at `2X`; observed effective leverage must remain below `3X`.
- Research costs: 10 bps fee plus 5 bps slippage per side, with historical Funding.

## Evidence

The stitched 2017-10 through 2026-09 replay produced 84.32% CAGR versus approximately
42.8% for 1X BTC B&H. It exceeded 1X B&H in the pre-2020, Research, Validation, and OOS
segments. Maximum drawdown was -67.75%, peak observed leverage was 2.258X, and the 90-day
paired bootstrap annualized-excess P05 was +7.82%.

This is not proof of pure timing alpha. Against continuous `1.5X` BTC with identical costs,
Funding, collateral, and controls, the strategy lagged by 128.13 percentage points during
2023-2024 Validation. The large historical drawdown also remains unsuitable for unattended
deployment. Freeze these parameters and collect new observations before any promotion.

## Reproduction

```bash
PYTHONPATH=scripts/research .venv/bin/python \
  scripts/research/research_btc_composite_controls.py
.venv/bin/pytest -q tests/test_btc_composite_controls.py
```

Detailed evidence is in
[`../../reports/experiments/btc_composite_controls/2026-09-03/README.md`](../../reports/experiments/btc_composite_controls/2026-09-03/README.md).
