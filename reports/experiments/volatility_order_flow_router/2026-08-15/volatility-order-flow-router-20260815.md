# volatility-order-flow-router-20260815

Causal prior-day BTC volatility routing between the frozen state strategy and
development-selected BTC order-flow sleeves.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.

## Search Result

- Raw route grid: `10080`.
- Development-eligible raw routes: `6149`.
- Development risk-eligible configurations: `2017`.
- Best reused-confirmation coverage: `5/7`.
- Strict base-and-stress 7/7 configurations: `0`.

No development-selected causal volatility route reached +15% in all seven complete 2026 months under both base and stress costs.
Partial `2026-08` is excluded from strict counts.

## Development Selection

`reported_imbalance_follow-window-126-long_short-threshold-1p25-ema-4-hold-6-cooldown-0-confirm-1`; volatility `3d / 60d / q0.25`; calm flow/state `50%/50%`; volatile flow/state `0%/100%`; leverage `5.00x`; monthly loss/profit locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 19.51% | 15.64% |
| 2026-02 | 42.88% | -22.83% |
| 2026-03 | 66.14% | 60.71% |
| 2026-04 | 21.78% | 19.37% |
| 2026-05 | -33.07% | -40.11% |
| 2026-06 | 27.44% | 21.53% |
| 2026-07 | -22.49% | -20.70% |
| 2026-08 | 1.84% | -2.35% |

## Best Confirmation Diagnostic

`reported_imbalance_follow-window-126-long_short-threshold-1p25-ema-4-hold-6-cooldown-0-confirm-1`; volatility `3d / 60d / q0.25`; calm flow/state `50%/50%`; volatile flow/state `0%/100%`; leverage `5.00x`; monthly loss/profit locks `25%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 19.51% | 15.64% |
| 2026-02 | 42.88% | 37.61% |
| 2026-03 | 66.14% | 60.71% |
| 2026-04 | 21.78% | 19.37% |
| 2026-05 | -33.07% | -40.11% |
| 2026-06 | 27.44% | 21.53% |
| 2026-07 | -22.49% | -28.63% |
| 2026-08 | 1.84% | -1.60% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- Order-flow archives provide only two complete development years.
- The routing hypothesis was specified after observing prior 2026 failures.
- Allocation turnover is charged on weight changes; component trading costs remain embedded.
- Drawdown is daily-close only; liquidation and borrowing costs are not modeled.
