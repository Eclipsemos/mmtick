# SOXLUSDT Volatility-Spread 5% Daily Feasibility

Parameter hash: `ca22aa999501b91fdcb3ad4adbec0a0afb6cbebda61765cf3fd9f7158eb32e2a`
Parameter search: **no**
Decision: **target_not_supported**

A 5% geometric daily return requires equity to grow by `4.32x` in 30 days and `80.73x` in 90 days.

The bootstrap resamples only complete development UTC days through August 10. August 11-13 remains a disclosed diagnostic and is never resampled.

## Exposure Results

| Exposure | Dev geo/day | Tick DD | Dev >=5% days | 90d P(geo>=5%) | 90d median geo/day | 90d P(DD<=-50%) | 90d P(DD<=-80%) | Fresh return |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.25x | +1.21% | -13.59% | 12 / 85 | +0.00%-+0.00% | +1.22% | +0.00% | +0.00% | +0.74% |
| 2.00x | +1.82% | -20.95% | 14 / 85 | +0.00%-+0.04% | +1.85% | +0.00% | +0.00% | +1.10% |
| 3.00x | +2.54% | -30.13% | 15 / 85 | +0.39%-+2.23% | +2.58% | +0.16% | +0.00% | +1.48% |
| 5.00x | +3.68% | -48.30% | 21 / 85 | +17.88%-+23.90% | +3.74% | +10.78% | +0.01% | +1.93% |
| 7.50x | +4.64% | -69.58% | 23 / 85 | +44.33%-+44.78% | +4.73% | +54.02% | +1.40% | +1.92% |
| 10.00x | +5.13% | -89.45% | 25 / 85 | +51.71%-+54.06% | +5.25% | +93.27% | +12.99% | +1.30% |

## Interpretation

No tested exposure reaches 5% development geometric daily return while remaining inside the diagnostic 30% Tick-path drawdown guard.

The 30% Tick drawdown limit is a diagnostic guard, not a promise of safety. Liquidation is not modeled; daily-close bootstrap drawdowns understate intraday risk.

These probabilities describe resampled versions of a short historical path. They are not probabilities of future profit under a changing market regime.
