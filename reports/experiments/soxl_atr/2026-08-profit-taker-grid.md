# SOXLUSDT August 2026 Gross Profit Take-Profit Grid

## Protocol

- Replay: 2026-08-01 00:00 UTC through 2026-08-31 01:26:36 UTC
  (2026-08-31 09:26:36 Beijing time).
- Strategy: frozen SOXL ATR(32) x 3, long-only, live-startup alignment.
- Exposure: 2x isolated leverage x 62.5% equity allocation (1.25x target exposure).
- Costs: 5 bps fee and 2 bps slippage per fill; recorded funding included.
- A gross take-profit threshold is a price gain from entry notional. The trigger is
  observed on a Tick and filled on the next Tick.

## Results

| Gross TP | Completed trades | Wins | Win rate | Net return | Max DD | Profit factor | TP signals |
|---:|---:|---:|---:|---:|---:|---:|---:|
| Disabled | 44 | 15 | 34.09% | +5.72% | -17.64% | 1.115 | 0 |
| 0.05% | 47 | 17 | 36.17% | +1.31% | -6.71% | 1.103 | 42 |
| 0.10% | 47 | 24 | 51.06% | **+2.35%** | **-6.66%** | **1.196** | 41 |
| 0.15% | 46 | 37 | 80.43% | -0.22% | -7.82% | 0.986 | 39 |
| 0.20% | 46 | 39 | 84.78% | +1.44% | -7.46% | 1.094 | 39 |
| 0.25% | 46 | 37 | 80.43% | -0.15% | -9.67% | 0.992 | 37 |
| 0.30% | 46 | 36 | 78.26% | +0.61% | -9.33% | 1.032 | 36 |
| 0.35% | 46 | 35 | 76.09% | -2.23% | -10.76% | 0.903 | 35 |
| 0.40% | 46 | 35 | 76.09% | -1.10% | -10.76% | 0.952 | 35 |
| 0.45% | 46 | 34 | 73.91% | -0.78% | -10.76% | 0.967 | 34 |
| 0.50% | 45 | 33 | 73.33% | +0.19% | -10.76% | 1.008 | 33 |
| 0.75% | 45 | 29 | 64.44% | -2.53% | -10.73% | 0.917 | 28 |
| 1.00% | 45 | 26 | 57.78% | -3.31% | -10.73% | 0.903 | 24 |
| 2.00% | 45 | 18 | 40.00% | -6.70% | -12.53% | 0.842 | 15 |

## Interpretation

The best result in this August-only grid is 0.10%, but the neighboring thresholds are
not a stable performance platform: 0.15% is negative, 0.20% is positive, and 0.25%
is again negative. This discontinuity is a parameter-selection warning, not evidence of
a durable edge.

The take-profit reduces drawdown by exiting frequent small favorable moves, but it also
cuts the few large trend winners. All thresholds in this table were selected and judged
on the same August sample, so none is approved for live use. Freeze a candidate (if any)
and evaluate it on a later untouched forward period before changing production settings.
