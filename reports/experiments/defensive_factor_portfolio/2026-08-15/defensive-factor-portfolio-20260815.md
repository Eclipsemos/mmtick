# defensive-factor-portfolio-20260815

Development-selected defensive factor search for the strict monthly target.

Decision: `rejected_no_strict_monthly_solution`. Trading approval: `false`.

## Search Result

- Factor candidates: `2988`.
- Conditional defensive factors: `78`.
- Development risk-eligible configurations: `4`.
- Best reused-confirmation coverage: `4/7`.
- Strict base-and-stress 7/7 configurations: `0`.

No development-selected defensive factor configuration reached +15% in all seven complete 2026 months under both base and stress costs.
Partial `2026-08` is excluded from all strict counts.

## Development-selected Configuration

`event-eth_perp-to-btc_perp-reversal-15d-threshold-2-hold-8x4h-none-long_short`; state/factor `50%/50%`; leverage `1.50x`; monthly loss/profit locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 30.96% | 25.14% |
| 2026-02 | 46.79% | 45.62% |
| 2026-03 | 18.95% | 17.81% |
| 2026-04 | 9.79% | 7.92% |
| 2026-05 | -24.73% | -28.28% |
| 2026-06 | 16.01% | 17.81% |
| 2026-07 | -10.08% | -12.03% |

## Best Reused-Confirmation Diagnostic

`event-eth_perp-to-btc_perp-reversal-15d-threshold-2-hold-8x4h-none-long_short`; state/factor `50%/50%`; leverage `1.50x`; monthly loss/profit locks `20%/16%`.

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 30.96% | 25.14% |
| 2026-02 | 46.79% | 45.62% |
| 2026-03 | 18.95% | 17.81% |
| 2026-04 | 9.79% | 7.92% |
| 2026-05 | -24.73% | -28.28% |
| 2026-06 | 16.01% | 17.81% |
| 2026-07 | -10.08% | -12.03% |

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The frozen market-state baseline was selected in prior overlapping research.
- Conditional factor screening uses only 2021-2025 baseline loss months.
- Monthly locks react after a daily close and pay turnover on the next exposure change.
- Drawdown is measured at daily closes; liquidation and borrowing costs are not modeled.
