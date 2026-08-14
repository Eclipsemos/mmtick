# SOXLUSDT Volatility-Spread Risk-Budget Exploration

Status: **exploratory_post_reveal_no_clean_holdout**
Selected profile: `threshold_1.5_0.5_1.25`
5% daily target: **not achieved**

The base breakout parameters were fixed before this experiment. The only searched control is a bounded 0.5x-2.0x entry multiplier derived from the closed signal bar's volatility spread strength.

| Path | Train geo/day | Validation geo/day | Confirmation geo/day | Development geo/day | Revealed 8/11-13 | 15m close DD |
|---|---:|---:|---:|---:|---:|---:|
| Risk budget | +1.14% | +0.61% | +0.17% | +0.84% | +0.39% | -8.80% |
| Fixed 1.0x | +1.42% | +1.08% | +0.60% | +1.21% | +0.74% | -11.11% |

Selection screened 22 profiles, with 22 passing train/validation and 8 of 10 finalists positive in confirmation.

This is not a production recommendation. August 11-13 was already revealed and is diagnostic only; any profile retained for monitoring needs a fresh post-August-13 forward window.
