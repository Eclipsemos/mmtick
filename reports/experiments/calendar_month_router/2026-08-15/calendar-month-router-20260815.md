# calendar-month-router-20260815

Development-only calendar-month routing of daily BTC/ETH MACD sleeves.

Decision: `rejected_no_development_selected_strict_solution`. Trading approval: `false`.

- Development route variants: `232`.
- Development-risk-eligible controls: `1630`.
- Development-selected strict result: `false`.
- Confirmation-diagnostic strict configurations: `0`.
- Best reused-confirmation coverage: `5/7`.

The calendar router selected without 2026 data did not reach +15% in all seven complete months under both cost models.
Partial `2026-08` is excluded from strict counts.

## Development Selected

`calendar-target-years3-long_short-top1-state0.5-lev2-loss0.25-profit0.16`; state/trend `50%/50%`; leverage `2.00x`; locks `25%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 16.27% | 17.24% |
| 2026-02 | 17.32% | 16.40% |
| 2026-03 | 23.09% | 22.04% |
| 2026-04 | 20.86% | 19.88% |
| 2026-05 | -16.57% | -20.29% |
| 2026-06 | 17.46% | 16.39% |
| 2026-07 | -10.77% | -12.53% |
| 2026-08 | -3.23% | -4.27% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- Each calendar month has only three discovery-year observations.
- The frozen state strategy itself was selected in earlier research.
- Daily-close drawdown omits intraday liquidation and borrowing costs.
