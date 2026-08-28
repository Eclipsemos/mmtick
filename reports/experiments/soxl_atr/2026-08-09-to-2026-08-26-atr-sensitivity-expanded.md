# SOXLUSDT Expanded ATR Sensitivity

This extends the previous scan to 88 long-only configurations for the same
`2026-08-09 00:00 UTC` through `2026-08-26` interval. Periods are `7, 10, 14, 21,
28, 32, 42, 56`; multipliers are `0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5,
3.0, 3.5, 4.0`. All other live parameters and costs are unchanged.

## Top Configurations

| ATR | Return | Max DD | Trades | Win rate | PF |
|---|---:|---:|---:|---:|---:|
| 7 × 2.0 | +6.59% | -11.49% | 32 | 40.6% | 1.36 |
| 7 × 1.75 | +3.04% | -12.86% | 48 | 39.6% | 1.09 |
| 7 × 2.5 | +2.39% | -9.85% | 30 | 33.3% | 1.15 |
| 10 × 2.5 | -0.15% | -13.16% | 30 | 40.0% | 0.99 |
| 10 × 1.5 | -2.67% | -13.61% | 70 | 34.3% | 0.93 |
| 10 × 2.0 | -2.77% | -13.77% | 37 | 32.4% | 0.89 |
| 21 × 2.5 | -3.18% | -21.64% | 26 | 57.7% | 0.87 |
| 32 × 3.0 current | -5.23% | -13.87% | 25 | 28.0% | 0.70 |

## Short ATR Findings

`ATR(7) x 1.0` was strongly negative at `-41.68%`, with 182 completed trades,
30.8% win rate, PF `0.51`, and `-43.74%` maximum drawdown. The smaller multiplier
caused excessive stop crossings and turnover. `ATR(7) x 0.5` was even worse at
`-54.85%`.

The same ATR(7) period changed sign across nearby multipliers: `7 x 1.75` made
`+3.04%`, `7 x 2.0` made `+6.59%`, and `7 x 2.5` made `+2.39%`, while `7 x 3.0`
lost `-16.79%`. This is a sharp parameter island, not evidence of a robust short
ATR strategy.

## Decision

The best result is positive only in this short, same-period sample and is surrounded
by materially negative neighbors. Do not switch production to `7 x 2.0`; keep the
frozen `32 x 3.0` configuration while accumulating independent forward data. The
complete 88-row machine-readable scan is in
`2026-08-09-to-2026-08-26-atr-sensitivity-expanded.json`.
