# fast-multiscale-calendar-router-20260815

Expanding-window calendar-month routing of BTC/ETH MACD sleeves.

Decision: `rejected_no_development_selected_strict_solution`. Trading approval: `false`.

- Walk-forward route variants: `230`.
- Walk-forward-risk-eligible controls: `2231`.
- Development-selected strict result: `false`.
- Confirmation-diagnostic strict configurations: `0`.
- Best reused-confirmation coverage: `4/7`.

The expanding-window calendar router selected without 2026 data did not reach +15% in all seven complete months under both cost models.
Partial `2026-08` is excluded from strict counts.

## Development Selected

`expanding-calendar-target-years3-long_only-top2-state0.75-lev1.5-loss0.10-profit0.20`; state/trend `75%/25%`; leverage `1.50x`; locks `10%/20%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 25.04% | 52.26% |
| 2026-02 | 60.30% | 59.96% |
| 2026-03 | 23.61% | 19.94% |
| 2026-04 | 12.83% | 9.00% |
| 2026-05 | -14.52% | -11.14% |
| 2026-06 | 26.43% | 24.14% |
| 2026-07 | -6.33% | -8.41% |
| 2026-08 | 0.76% | 0.00% |

## Best Confirmation Diagnostic

`expanding-calendar-target-years2-long_short-top1-state0.75-lev1.5-loss0.10-profit0.16`; state/trend `75%/25%`; leverage `1.50x`; locks `10%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 35.26% | 28.32% |
| 2026-02 | 61.97% | 60.38% |
| 2026-03 | 16.79% | 19.61% |
| 2026-04 | 16.01% | 9.73% |
| 2026-05 | -13.94% | -11.79% |
| 2026-06 | 16.82% | 28.68% |
| 2026-07 | -5.57% | -8.49% |
| 2026-08 | 1.36% | 0.45% |

This diagnostic was identified after viewing 2026 and is not selected.

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- Early validation years have only two or three same-month training observations.
- The frozen state strategy itself was selected in earlier research.
- Daily-close drawdown omits intraday liquidation and borrowing costs.
