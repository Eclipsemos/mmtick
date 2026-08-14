# SOXLUSDT Multi-Horizon Volatility-Spread Exploration

Status: **exploratory_post_reveal_no_clean_holdout**
Train/validation-selected weighting: `equal`
Fresh holdout used for selection: **no**

30m and 60m bars are aggregated from closed 15m bars. Each sleeve uses the same next persisted Tick execution model. Portfolio figures are daily-rebalanced diagnostics, not a shared intraday margin simulation.

## Sleeve Results

| Sleeve | Parameters | Train geo/day | Validation geo/day | Confirmation geo/day | Development geo/day | Fresh return | Tick DD |
|---|---|---:|---:|---:|---:|---:|---:|
| 15m | `compression_release/long_short/12-64` | +1.42% | +1.08% | +0.60% | +1.21% | +0.74% | -13.59% |
| 30m | `compression_release/long_short/8-24` | +1.06% | +1.28% | +0.49% | +1.07% | -8.96% | -32.01% |
| 60m | `expansion_breakout/long_short/4-32` | +0.74% | +0.83% | +0.41% | +0.73% | -5.38% | -27.83% |

## Portfolio Scaling

| Scheme | Total exposure | Development geo/day | Development DD | Fresh return |
|---|---:|---:|---:|---:|
| equal | 1.25x | +1.09% | -13.82% | -4.53% |
| equal | 2.00x | +1.66% | -21.58% | -7.27% |
| equal | 3.00x | +2.34% | -31.34% | -10.95% |
| equal | 5.00x | +3.43% | -48.92% | -18.40% |
| equal | 7.50x | +4.32% | -67.53% | -27.83% |
| equal | 10.00x | +4.64% | -82.73% | -37.34% |
| inverse_volatility | 1.25x | +1.09% | -12.79% | -4.10% |
| inverse_volatility | 2.00x | +1.67% | -20.00% | -6.58% |
| inverse_volatility | 3.00x | +2.36% | -29.11% | -9.93% |
| inverse_volatility | 5.00x | +3.49% | -45.62% | -16.69% |
| inverse_volatility | 7.50x | +4.49% | -63.32% | -25.28% |
| inverse_volatility | 10.00x | +5.01% | -78.01% | -33.97% |

## Decision

Use the train/validation-selected risk weighting for comparison; the 3x total exposure cap is diagnostic and has no shared intraday liquidation model.

A positive combination result is not an approval. Parameters must be frozen before the next complete UTC day and then evaluated only on new forward data.
