# SOXLUSDT Cross-Asset BTC State-Filter Exploration

Status: **exploratory_post_reveal_no_clean_holdout**
Selected filter: `low_vol_1.5`
5% daily target: **not achieved**

The frozen SOXL volatility-spread signal is unchanged. The filter uses only the latest closed BTC 15m bar at the SOXL signal close; it controls whether the SOXL entry is allowed.

| Path | Train geo/day | Validation geo/day | Confirmation geo/day | Development geo/day | 8/11 overlap | 15m close DD |
|---|---:|---:|---:|---:|---:|---:|
| BTC filter | +1.18% | +0.79% | +1.15% | +1.03% | -1.66% | -16.11% |
| Fixed baseline | +1.42% | +1.08% | +0.60% | +1.21% | -1.66% | -11.11% |

The search tested 16 BTC state filters; 5 passed train/validation and 2 of 5 finalists were positive in confirmation.

BTC data ends on August 11, so the only post-selection overlap shown is August 11. This result is not a production recommendation and requires a fresh post-August-13 holdout.
