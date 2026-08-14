# SOXLUSDT State-Filtered Multi-Horizon Volatility-Spread Exploration

Status: **exploratory_post_reveal_no_clean_holdout**
Train/validation-selected weighting: `equal`
Fresh holdout used for selection: **no**

30m and 60m bars are aggregated from closed 15m bars. Each sleeve uses the same next persisted Tick execution model. Portfolio figures are daily-rebalanced diagnostics, not a shared intraday margin simulation.

## Sleeve Results

| Sleeve | Parameters | Train geo/day | Validation geo/day | Confirmation geo/day | Development geo/day | Fresh return | Tick DD |
|---|---|---:|---:|---:|---:|---:|---:|
| 15m | `compression_release/long_short/12-64` | +1.42% | +1.08% | +0.60% | +1.21% | +0.74% | -13.59% |
| 30m | `compression_release/long_short/8-24` | +1.06% | +1.28% | +0.49% | +1.07% | -8.96% | -32.01% |
| 60m | `compression_release/long_short/8-24` | +0.67% | +0.80% | +0.39% | +0.69% | -0.22% | -17.63% |

## Portfolio Scaling

| Scheme | Total exposure | Development geo/day | Development DD | Fresh return |
|---|---:|---:|---:|---:|
| equal | 1.25x | +1.05% | -9.15% | -2.80% |
| equal | 2.00x | +1.61% | -14.34% | -4.55% |
| equal | 3.00x | +2.27% | -20.97% | -6.95% |
| equal | 5.00x | +3.36% | -34.58% | -11.99% |
| equal | 7.50x | +4.33% | -50.40% | -18.67% |
| equal | 10.00x | +4.92% | -64.33% | -25.73% |
| inverse_volatility | 1.25x | +1.01% | -7.66% | -2.08% |
| inverse_volatility | 2.00x | +1.55% | -12.10% | -3.37% |
| inverse_volatility | 3.00x | +2.20% | -17.83% | -5.16% |
| inverse_volatility | 5.00x | +3.29% | -28.65% | -8.90% |
| inverse_volatility | 7.50x | +4.30% | -41.01% | -13.88% |
| inverse_volatility | 10.00x | +4.98% | -52.40% | -19.17% |

## Decision

Use the train/validation-selected risk weighting for comparison; the 3x total exposure cap is diagnostic and has no shared intraday liquidation model.

A positive combination result is not an approval. Parameters must be frozen before the next complete UTC day and then evaluated only on new forward data.
