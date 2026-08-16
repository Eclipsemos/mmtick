# market-state-volatility-20260815-150643-323241

Research-only causal market-state exposure with a causal volatility target.

Decision: `rejected_after_confirmation`. Trading approval: `false`.
State search: `4320` candidates, shortlisted `100`; volatility grid: `216`.
Selected `anchor` / `btc_perp-540-top_account_crowding`, mode `below`, threshold `1.25`, state exposure `0.70x` / `2.00x`.
Volatility target: `20` days, `2.50%` daily, exposure `0.70x`–`1.10x`, `daily`.

| Split | Return | Max DD | Positive months | 15% months |
|---|---:|---:|---:|---:|
| discovery | 2552.48% | -34.63% | 61.11% | 16/36 |
| validation | 139.91% | -27.82% | 62.50% | 4/24 |
| 2021 | 424.70% | -34.63% | 58.33% | 6/12 |
| 2022 | 144.34% | -32.54% | 58.33% | 5/12 |
| 2023 | 106.84% | -17.76% | 66.67% | 5/12 |
| 2024 | 58.58% | -27.82% | 58.33% | 2/12 |
| 2025 | 51.28% | -13.50% | 66.67% | 2/12 |
| 2026 reused confirmation | 54.45% | -20.12% | 75.00% | 3/8 |
| 2026 stress 10+5 bps | 34.93% | -22.79% | 62.50% | 2/8 |

## 2026 Monthly Returns

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 4.43% | 0.07% |
| 2026-02 | 22.66% | 21.71% |
| 2026-03 | 3.87% | 1.73% |
| 2026-04 | 16.02% | 14.67% |
| 2026-05 | -13.26% | -15.99% |
| 2026-06 | 23.38% | 23.26% |
| 2026-07 | -6.78% | -8.07% |
| 2026-08 | 0.29% | -0.23% |

The development-selected overlay failed base or stress confirmation gates.

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The market-state signal uses only the last complete prior UTC-day 4h snapshot.
- Volatility estimates use only returns closed before each exposure day; split prefixes are retained as warmup.
- Drawdown is measured at daily closes; borrowing cost and liquidation are not modeled.
- This research candidate is not connected to paper or live execution.
