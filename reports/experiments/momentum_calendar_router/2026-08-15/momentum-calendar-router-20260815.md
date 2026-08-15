# momentum-calendar-router-20260815

Prior-day BTC momentum routing between expanding long-only and long/short calendars.

Decision: `rejected_no_development_selected_strict_solution`. Trading approval: `false`.

- Development route variants: `448`.
- Development-risk-eligible controls: `4731`.
- Development-selected strict result: `false`.
- Confirmation-diagnostic strict configurations: `0`.
- Best reused-confirmation coverage: `6/7`.

The momentum calendar router selected without 2026 data did not reach +15% in all seven complete months under both cost models.
Partial `2026-08` is excluded from strict counts.

## Development Selected

`momentum-calendar-mean-years5-top5-state0.5-mom1-threshold-0.03-lev4-loss0.10-profit0.18`; state/trend `50%/50%`; leverage `4.00x`; locks `10%/18%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 20.71% | 18.89% |
| 2026-02 | 29.55% | 27.00% |
| 2026-03 | 44.73% | 42.25% |
| 2026-04 | 28.87% | 27.71% |
| 2026-05 | -15.25% | -19.44% |
| 2026-06 | 18.03% | 21.68% |
| 2026-07 | 22.38% | 21.10% |
| 2026-08 | -1.49% | -3.36% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- Early validation years have only two or three same-month training observations.
- Daily momentum can react only after a daily close and cannot prevent gap losses.
- Daily-close drawdown omits intraday liquidation and borrowing costs.
