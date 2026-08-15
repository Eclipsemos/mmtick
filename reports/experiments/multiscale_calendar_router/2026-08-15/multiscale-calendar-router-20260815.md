# multiscale-calendar-router-20260815

Expanding-window calendar-month routing of BTC/ETH MACD sleeves.

Decision: `rejected_no_development_selected_strict_solution`. Trading approval: `false`.

- Walk-forward route variants: `189`.
- Walk-forward-risk-eligible controls: `2281`.
- Development-selected strict result: `false`.
- Confirmation-diagnostic strict configurations: `0`.
- Best reused-confirmation coverage: `6/7`.

The expanding-window calendar router selected without 2026 data did not reach +15% in all seven complete months under both cost models.
Partial `2026-08` is excluded from strict counts.

## Development Selected

`expanding-calendar-worst-years2-long_only-top1-state0.75-lev1.5-loss0.10-profit0.18`; state/trend `75%/25%`; leverage `1.50x`; locks `10%/18%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 28.07% | 21.89% |
| 2026-02 | 56.60% | 55.58% |
| 2026-03 | 18.57% | 18.68% |
| 2026-04 | 8.60% | 6.94% |
| 2026-05 | -14.60% | -11.53% |
| 2026-06 | 28.98% | 26.81% |
| 2026-07 | -5.93% | -8.23% |
| 2026-08 | 1.05% | 0.23% |

## Best Confirmation Diagnostic

`expanding-calendar-mean-years2-long_only-top5-state0.5-lev3-loss0.10-profit0.16`; state/trend `50%/50%`; leverage `3.00x`; locks `10%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 24.61% | 22.12% |
| 2026-02 | 15.78% | 85.25% |
| 2026-03 | 22.43% | 19.77% |
| 2026-04 | 23.04% | 20.02% |
| 2026-05 | -12.86% | -16.14% |
| 2026-06 | 17.16% | 27.37% |
| 2026-07 | 18.55% | 17.63% |
| 2026-08 | 1.12% | -0.53% |

This diagnostic was identified after viewing 2026 and is not selected.

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- Early validation years have only two or three same-month training observations.
- The frozen state strategy itself was selected in earlier research.
- Daily-close drawdown omits intraday liquidation and borrowing costs.
