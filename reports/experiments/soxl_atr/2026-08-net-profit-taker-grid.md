# SOXLUSDT August 2026 Net Profit Take-Profit Grid

## Protocol

- Replay: 2026-08-01 00:00 UTC through 2026-08-31 01:26:36 UTC
  (2026-08-31 09:26:36 Beijing time).
- Strategy: frozen SOXL ATR(32) x 3, long-only, live-startup alignment.
- Exposure: 2x isolated leverage x 62.5% equity allocation (1.25x target exposure).
- Costs: 5 bps fee and 2 bps slippage per fill; recorded funding included.
- Net TP is a target net PnL divided by entry notional. At each Tick it estimates
  the exit fill after slippage, exit fee, entry fee, and accrued Funding; the signal
  is filled on the next Tick.

## Results

| Net TP | Completed trades | Wins | Win rate | Net return | Max DD | Profit factor | TP signals |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.05% | 46 | 39 | 84.78% | +0.40% | -7.73% | 1.026 | 39 |
| 0.075% | 46 | 39 | 84.78% | +1.25% | -7.49% | 1.082 | 39 |
| **0.10%** | 46 | 38 | 82.61% | **+1.90%** | **-7.24%** | **1.120** | 38 |
| 0.125% | 46 | 37 | 80.43% | -0.26% | -9.74% | 0.986 | 37 |
| 0.15% | 46 | 37 | 80.43% | +0.36% | -9.50% | 1.019 | 37 |
| 0.175% | 46 | 36 | 78.26% | +0.19% | -9.41% | 1.010 | 36 |
| 0.20% | 46 | 36 | 78.26% | +1.65% | -9.00% | 1.087 | 36 |
| 0.25% | 46 | 35 | 76.09% | -1.83% | -10.76% | 0.920 | 35 |
| 0.50% | 45 | 29 | 64.44% | -5.44% | -10.76% | 0.819 | 29 |
| 1.00% | 45 | 24 | 53.33% | -5.52% | -11.74% | 0.846 | 22 |
| 2.00% | 45 | 18 | 40.00% | -5.33% | -12.45% | 0.875 | 15 |
| 4.00% | 45 | 16 | 35.56% | -4.35% | -14.32% | 0.901 | 6 |

## Interpretation

The August-only best point is a 0.10% net target, but the threshold response is
discontinuous: 0.125% is negative while 0.20% is positive. This is a warning about
sample-specific selection, not evidence of a robust production threshold.

The net target is materially different from a gross price target. A 0.10% net target
requires enough gross price movement to cover both-sided costs and Funding. The result
still comes from selecting and evaluating on the same August sample; freeze any candidate
and validate it on untouched future data before changing live settings.
