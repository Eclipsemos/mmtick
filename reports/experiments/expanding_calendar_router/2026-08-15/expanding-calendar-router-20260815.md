# expanding-calendar-router-20260815

Expanding-window calendar-month routing of daily BTC/ETH MACD sleeves.

Decision: `rejected_no_development_selected_strict_solution`. Trading approval: `false`.

- Walk-forward route variants: `271`.
- Walk-forward-risk-eligible controls: `3819`.
- Development-selected strict result: `false`.
- Confirmation-diagnostic strict configurations: `0`.
- Best reused-confirmation coverage: `6/7`.

The expanding-window calendar router selected without 2026 data did not reach +15% in all seven complete months under both cost models.
Partial `2026-08` is excluded from strict counts.

## Development Selected

`expanding-calendar-mean-years3-long_only-top3-state0.5-lev4-loss0.20-profit0.18`; state/trend `50%/50%`; leverage `4.00x`; locks `20%/18%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 20.41% | 26.90% |
| 2026-02 | 20.41% | 18.36% |
| 2026-03 | 45.41% | 42.95% |
| 2026-04 | 23.07% | 20.85% |
| 2026-05 | -25.45% | -20.94% |
| 2026-06 | 19.25% | 21.92% |
| 2026-07 | 19.33% | 18.01% |
| 2026-08 | -0.92% | -2.73% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- Early validation years have only two or three same-month training observations.
- The frozen state strategy itself was selected in earlier research.
- Daily-close drawdown omits intraday liquidation and borrowing costs.
