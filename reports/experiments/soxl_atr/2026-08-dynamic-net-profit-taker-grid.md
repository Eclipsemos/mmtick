# SOXLUSDT August 2026 Dynamic Net Profit-Taker Research

## Protocol

- Replay: 2026-08-01 00:00 UTC through 2026-08-31 01:26:36 UTC, using 3,494,602
  aggregated ticks (60,837,230 exchange trades).
- Frozen strategy: ATR(32) x 3, long-only, 15-minute Tick ATR, live-startup alignment.
- Exposure: 2x isolated leverage x 62.5% equity allocation (1.25x target exposure).
- Costs: 5 bps fee and 2 bps slippage per fill; recorded funding included.
- Every candidate used the same replay and the existing conservative next-Tick fill model.

## Formulas

1. **ATR-ratio target:** `net_target = base_pct x current_ATR / entry_ATR`.
2. **ATR-level target:** `net_target = multiplier x current_ATR / entry_price`.

The target is net PnL divided by entry notional. At each tick, projected exit
slippage, both fees, and accrued funding are included before deciding whether the
target is reached. ATR is only the value available at that tick; no future bars are
used.

## Results

| Formula | Parameter | Trades | Wins | Win rate | Net return | Max DD | Profit factor | TP signals |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ATR-ratio | 0.050% | 46 | 39 | 84.78% | +0.33% | -7.73% | 1.021 | 39 |
| ATR-ratio | 0.075% | 46 | 39 | 84.78% | +1.27% | -7.47% | 1.083 | 39 |
| **ATR-ratio** | **0.100%** | **46** | **38** | **82.61%** | **+1.67%** | **-7.26%** | **1.106** | **38** |
| ATR-ratio | 0.125% | 46 | 37 | 80.43% | -0.43% | -9.74% | 0.977 | 37 |
| ATR-ratio | 0.150% | 46 | 37 | 80.43% | +0.42% | -9.43% | 1.023 | 37 |
| ATR-ratio | 0.200% | 46 | 36 | 78.26% | +1.27% | -8.99% | 1.067 | 36 |
| ATR-ratio | 0.300% | 46 | 35 | 76.09% | -0.60% | -10.76% | 0.974 | 35 |
| ATR-level | 0.050 | 46 | 38 | 82.61% | +0.17% | -7.84% | 1.011 | 39 |
| ATR-level | 0.075 | 46 | 38 | 82.61% | +0.53% | -7.79% | 1.035 | 39 |
| ATR-level | 0.100 | 46 | 38 | 82.61% | +1.02% | -7.64% | 1.067 | 39 |
| **ATR-level** | **0.125** | **46** | **38** | **82.61%** | **+1.50%** | **-7.52%** | **1.098** | **39** |
| ATR-level | 0.150 | 46 | 37 | 80.43% | -0.92% | -10.04% | 0.949 | 38 |
| ATR-level | 0.200 | 46 | 37 | 80.43% | -4.40% | -11.82% | 0.797 | 37 |
| ATR-level | 0.300 | 46 | 37 | 80.43% | -2.72% | -11.17% | 0.875 | 37 |
| ATR-level | 0.500 | 46 | 36 | 78.26% | -2.46% | -10.73% | 0.902 | 36 |
| ATR-level | 0.750 | 46 | 34 | 73.91% | -0.65% | -10.75% | 0.978 | 34 |
| ATR-level | 1.000 | 46 | 30 | 65.22% | -5.90% | -11.56% | 0.834 | 30 |

For reference, the previously tested static 0.10% net target returned about
`+1.90%` with `-7.24%` max drawdown on this same August sample. Dynamic scaling
therefore did not improve the static candidate.

## Conclusion

The August-only numerical winner is `0.10% x current_ATR / entry_ATR`, but it is
not a robustly identified edge: the adjacent 0.125% point is negative, and the
best ATR-level point changes the formula and parameter. These discontinuities are
consistent with sample-specific trade-exit selection. Keep the implementation as
research-only, freeze no live setting from this grid, and validate the formula on
untouched September/forward data with the same costs before paper trading.
