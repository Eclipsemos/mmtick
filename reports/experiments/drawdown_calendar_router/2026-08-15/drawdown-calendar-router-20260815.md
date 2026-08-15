# drawdown-calendar-router-20260815

Causal monthly-drawdown recovery from long-only to long/short calendar maps.

Decision: `rejected_no_development_selected_strict_solution`. Trading approval: `false`.

- Development route variants: `207`.
- Development-risk-eligible controls: `2985`.
- Development-selected strict result: `false`.
- Confirmation-diagnostic strict configurations: `0`.
- Best reused-confirmation coverage: `6/7`.

The drawdown-recovery calendar router selected without 2026 parameters did not reach +15% in all seven complete months under both cost models.
Partial `2026-08` is excluded from strict counts.

## Development Selected

`drawdown-calendar-mean-years3-top5-state0.5-trigger0.05-lev4-loss0.20-profit0.16`; state/trend `50%/50%`; leverage `4.00x`; locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 17.65% | 15.88% |
| 2026-02 | 20.41% | 18.36% |
| 2026-03 | 45.61% | 43.16% |
| 2026-04 | 21.76% | 20.71% |
| 2026-05 | -24.91% | -29.38% |
| 2026-06 | 17.56% | 16.40% |
| 2026-07 | 17.63% | 16.35% |
| 2026-08 | -1.12% | -2.93% |

## Limitations

- The recovery mechanism was proposed after viewing reused 2026 confirmation.
- Early validation years have only two or three same-month training observations.
- Recovery begins one daily close after the loss trigger and cannot erase prior loss.
- Daily-close drawdown omits intraday liquidation and borrowing costs.
