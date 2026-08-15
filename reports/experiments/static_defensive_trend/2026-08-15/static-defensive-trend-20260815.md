# static-defensive-trend-20260815

Static single and paired MACD complements selected on frozen-baseline development
loss months.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.

## Search Result

- Conditional single MACD sleeves: `119`.
- Development-selected MACD pairs: `60`.
- Development risk-eligible controls: `762`.
- Best reused-confirmation coverage: `6/7`.
- Strict base-and-stress 7/7 configurations: `0`.

No development-selected static defensive sleeve reached +15% in all seven complete 2026 months under both base and stress costs.
Partial `2026-08` is excluded from strict counts.

## Development Selection

`btc_perp-macd-1440m-16-48-5-long_only-confirm1`; trend/baseline `10%/90%`; leverage `5.00x`; monthly loss/profit locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 21.73% | 16.28% |
| 2026-02 | 28.54% | 27.77% |
| 2026-03 | 21.90% | 20.34% |
| 2026-04 | 15.63% | 15.62% |
| 2026-05 | -14.01% | -17.68% |
| 2026-06 | -6.06% | -8.97% |
| 2026-07 | -2.29% | -5.59% |
| 2026-08 | 2.61% | 1.51% |

## Best Confirmation Diagnostic

`btc_perp-macd-1440m-12-36-14-long_only-confirm1`; trend/baseline `40%/60%`; leverage `8.00x`; monthly loss/profit locks `15%/18%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 25.63% | 23.17% |
| 2026-02 | 29.54% | 27.61% |
| 2026-03 | 46.42% | 43.76% |
| 2026-04 | 29.10% | 26.35% |
| 2026-05 | 18.62% | 16.82% |
| 2026-06 | -25.40% | -18.49% |
| 2026-07 | 19.97% | 17.92% |
| 2026-08 | -1.94% | -4.32% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The static complement study follows observed prior 2026 failures.
- The conditional screen has only one frozen-baseline loss month per development year.
- Static sleeve rebalancing turnover beyond embedded component costs is not modeled.
- Drawdown is daily-close only; liquidation and borrowing costs are not modeled.
