# walkforward-volatility-guard-20260815

Walk-forward volatility-guarded daily MACD search with no 2026 parameter selection.

Decision: `rejected_no_walkforward_strict_solution`. Trading approval: `false`.

- Raw development shortlist: `120`.
- Development-risk-eligible controls: `278`.
- Best reused-confirmation coverage: `4/7`.
- Strict base-and-stress 7/7 configurations: `0`.

No configuration selected without 2026 data reached +15% in all seven complete 2026 months under both cost models.
Partial `2026-08` is excluded.

## Best Confirmation

`eth_perp-macd-1440m-5-15-9-long_short-confirm1`; volatility `10d/60d/q0.50`; calm/volatile trend `50%/25%`; leverage `1.00x`; locks `15%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 35.67% | 32.33% |
| 2026-02 | 47.42% | 47.32% |
| 2026-03 | 16.25% | 16.67% |
| 2026-04 | 5.38% | 4.58% |
| 2026-05 | -15.08% | -16.98% |
| 2026-06 | 16.46% | 16.89% |
| 2026-07 | -1.89% | -3.10% |
| 2026-08 | -2.32% | -2.73% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The frozen state strategy itself was selected in earlier research.
- Daily-close drawdown omits intraday liquidation and borrowing costs.
