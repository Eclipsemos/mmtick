# drawdown-recovery-trend-20260815

Causal monthly-drawdown recovery using development-selected single and paired MACD
sleeves around the frozen monthly-robust ensemble.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.

## Search Result

- Conditional single MACD sleeves: `119`.
- Development-selected MACD pairs: `60`.
- Development risk-eligible controls: `25772`.
- Best reused-confirmation coverage: `5/7`.
- Strict base-and-stress 7/7 configurations: `0`.

No development-selected causal recovery sleeve reached +15% in all seven complete 2026 months under both base and stress costs.
Partial `2026-08` is excluded from strict counts.

## Development Selection

`eth_perp-macd-1440m-5-15-5-long_short-confirm3`; trigger `-2.5%`; trend/baseline `100%/0%`; leverage `8.00x`; monthly loss/profit locks `25%/18%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 37.99% | -46.82% |
| 2026-02 | 52.09% | 46.92% |
| 2026-03 | 31.29% | 28.51% |
| 2026-04 | 24.79% | 21.91% |
| 2026-05 | -36.94% | -26.67% |
| 2026-06 | -5.03% | -11.44% |
| 2026-07 | -5.69% | -11.23% |
| 2026-08 | 5.92% | 4.16% |

## Best Confirmation Diagnostic

`eth_perp-macd-1440m-8-24-5-long_short-confirm1`; trigger `-1.0%`; trend/baseline `25%/75%`; leverage `6.00x`; monthly loss/profit locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 32.80% | 35.94% |
| 2026-02 | 38.38% | 37.15% |
| 2026-03 | 23.43% | 21.46% |
| 2026-04 | 18.25% | 16.20% |
| 2026-05 | -10.83% | -21.88% |
| 2026-06 | 21.25% | 17.71% |
| 2026-07 | -4.73% | -2.19% |
| 2026-08 | 4.42% | 2.88% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The recovery hypothesis was specified after observing prior 2026 failures.
- Only MACD sleeves positive in all frozen-baseline development loss months are used.
- Allocation switches and monthly exposure changes pay explicit turnover costs.
- Drawdown is daily-close only; liquidation and borrowing costs are not modeled.
