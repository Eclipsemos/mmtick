# market-state-volatility-20260815-153825-882277

Research-only causal market-state exposure with a causal volatility target.

Decision: `research_candidate`. Trading approval: `false`.
State search: `4320` candidates, shortlisted `300`; volatility grid: `36`.
Selected `anchor` / `eth_perp-540-top_retail_spread`, mode `below`, threshold `1.25`, state exposure `0.80x` / `2.00x`.
Volatility target: `20` days, `3.00%` daily, exposure `0.60x`–`1.10x`, `daily`.

| Split | Return | Max DD | Positive months | 15% months |
|---|---:|---:|---:|---:|
| discovery | 2001.56% | -33.87% | 58.33% | 13/36 |
| validation | 140.28% | -27.87% | 58.33% | 6/24 |
| 2021 | 400.54% | -24.46% | 58.33% | 4/12 |
| 2022 | 132.89% | -33.87% | 58.33% | 5/12 |
| 2023 | 80.25% | -21.73% | 58.33% | 4/12 |
| 2024 | 54.90% | -27.87% | 58.33% | 3/12 |
| 2025 | 55.11% | -22.72% | 58.33% | 3/12 |
| 2026 reused confirmation | 182.94% | -28.19% | 75.00% | 4/8 |
| 2026 stress 10+5 bps | 141.52% | -32.50% | 62.50% | 4/8 |

## 2026 Monthly Returns

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 52.87% | 47.32% |
| 2026-02 | 61.28% | 58.83% |
| 2026-03 | 21.00% | 18.44% |
| 2026-04 | 6.46% | 5.36% |
| 2026-05 | -24.29% | -28.01% |
| 2026-06 | 27.10% | 26.85% |
| 2026-07 | -7.73% | -9.19% |
| 2026-08 | 0.34% | -0.26% |

The development-selected overlay met reused base and stress confirmation gates; fresh forward evidence remains required.

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The search scope was revised after prior 2026 diagnostics, so protocol-level selection bias remains even though the numeric ranking uses only 2021-2025.
- The market-state signal uses only the last complete prior UTC-day 4h snapshot.
- Volatility estimates use only returns closed before each exposure day; split prefixes are retained as warmup.
- Turnover is charged once on the combined state-times-volatility exposure.
- Drawdown is measured at daily closes; borrowing cost and liquidation are not modeled.
- This research candidate is not connected to paper or live execution.
