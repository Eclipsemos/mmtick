# marginal-factor-portfolio-20260815-120433-669275

Research-only unrestricted marginal-factor portfolio.

Decision: `rejected_after_confirmation`. Trading approval: `false`.
Evaluated `71,664` configurations; `4,140` passed development risk gates.
Selected `eth_perp-60m-long_short-ret_16_z-trend_20_z-add-threshold-0` at `30%` plus the static anchor at outer leverage `1.25x`.

| Split | Return | Max DD | Positive months | 25% months |
|---|---:|---:|---:|---:|
| 2021-2023 discovery | 1193.34% | -33.65% | 52.78% | 6/36 |
| 2024 selection | 113.28% | -32.06% | 66.67% | 4/12 |
| 2025 selection | 24.76% | -34.91% | 58.33% | 2/12 |
| 2026 reused confirmation | 136.42% | -21.60% | 62.50% | 3/8 |
| 2026 stress 10+5 bps | 102.79% | -25.89% | 62.50% | 2/8 |

## 2026 monthly returns

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 31.81% | 23.58% |
| 2026-02 | 49.27% | 47.24% |
| 2026-03 | 9.87% | 7.38% |
| 2026-04 | 7.78% | 5.67% |
| 2026-05 | -16.39% | -18.75% |
| 2026-06 | 34.45% | 38.02% |
| 2026-07 | -9.49% | -11.71% |
| 2026-08 | -0.27% | -0.79% |

The development-selected marginal factor failed monthly coverage, drawdown, or stress gates.

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The search is limited to one marginal sleeve and fixed initial capital allocations.
- Portfolio drawdown is measured at daily closes.
- Borrowing cost, liquidation, market impact, and exchange failure are not modeled.
