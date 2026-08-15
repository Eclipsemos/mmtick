# fast-trend-complement-20260815

Fast closed-bar trend complement search for the strict monthly target.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.

## Search Result

- Trend candidates: `224`.
- Development shortlist: `60`.
- Development risk-eligible configurations: `59`.
- Best reused-confirmation coverage: `4/7`.
- Strict base-and-stress 7/7 configurations: `0`.

No development-selected fast-trend complement reached +15% in all seven complete 2026 months under both base and stress costs.
Partial `2026-08` is excluded from strict counts.

## Development-selected Configuration

`eth_perp-fast-momentum-1440m-lookback3-threshold0p04-confirm1-long_short`; state/factor `25%/75%`; leverage `1.00x`; monthly loss/profit locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 9.52% | 7.08% |
| 2026-02 | 33.37% | 33.04% |
| 2026-03 | 7.78% | 5.49% |
| 2026-04 | -3.66% | -5.00% |
| 2026-05 | -15.37% | -17.36% |
| 2026-06 | 20.86% | 19.67% |
| 2026-07 | -5.09% | -6.07% |

## Best Reused-Confirmation Diagnostic

`eth_perp-fast-momentum-1440m-lookback10-threshold0-confirm2-long_short`; state/factor `75%/25%`; leverage `1.00x`; monthly loss/profit locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 38.69% | 33.92% |
| 2026-02 | 49.74% | 49.41% |
| 2026-03 | 18.31% | 16.01% |
| 2026-04 | 6.05% | 4.87% |
| 2026-05 | -22.62% | -21.96% |
| 2026-06 | 24.76% | 23.07% |
| 2026-07 | -8.24% | -9.72% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The fast trend grid was introduced after observing prior 2026 failures.
- The frozen market-state baseline was selected in prior overlapping research.
- Monthly locks react after daily closes and incur explicit exposure turnover costs.
- Drawdown is daily-close only; liquidation and borrowing costs are not modeled.
