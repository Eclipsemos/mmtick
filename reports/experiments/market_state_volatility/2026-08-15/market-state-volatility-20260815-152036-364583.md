# market-state-volatility-20260815-152036-364583

Research-only causal market-state exposure with a causal volatility target.

Decision: `research_candidate`. Trading approval: `false`.
State search: `4320` candidates, shortlisted `300`; volatility grid: `36`.
Selected `anchor` / `eth_perp-540-top_retail_spread`, mode `below`, threshold `1.25`, state exposure `0.80x` / `2.00x`.
Volatility target: `20` days, `3.00%` daily, exposure `0.60x`–`1.10x`, `daily`.

| Split | Return | Max DD | Positive months | 15% months |
|---|---:|---:|---:|---:|
| discovery | 2002.49% | -33.87% | 58.33% | 13/36 |
| validation | 140.40% | -27.87% | 58.33% | 6/24 |
| 2021 | 400.54% | -24.46% | 58.33% | 4/12 |
| 2022 | 132.89% | -33.87% | 58.33% | 5/12 |
| 2023 | 80.33% | -21.73% | 58.33% | 4/12 |
| 2024 | 54.93% | -27.87% | 58.33% | 3/12 |
| 2025 | 55.16% | -22.72% | 58.33% | 3/12 |
| 2026 reused confirmation | 182.83% | -28.19% | 75.00% | 4/8 |
| 2026 stress 10+5 bps | 141.33% | -32.49% | 62.50% | 4/8 |

## 2026 Monthly Returns

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 52.91% | 47.40% |
| 2026-02 | 61.17% | 58.60% |
| 2026-03 | 21.01% | 18.47% |
| 2026-04 | 6.46% | 5.36% |
| 2026-05 | -24.29% | -28.00% |
| 2026-06 | 27.09% | 26.82% |
| 2026-07 | -7.73% | -9.19% |
| 2026-08 | 0.34% | -0.26% |

The development-selected overlay met reused base and stress confirmation gates; fresh forward evidence remains required.

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The market-state signal uses only the last complete prior UTC-day 4h snapshot.
- Volatility estimates use only returns closed before each exposure day; split prefixes are retained as warmup.
- Drawdown is measured at daily closes; borrowing cost and liquidation are not modeled.
- This research candidate is not connected to paper or live execution.
