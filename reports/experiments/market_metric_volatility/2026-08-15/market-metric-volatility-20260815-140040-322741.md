# market-metric-volatility-20260815-140040-322741

Research-only causal volatility target on the frozen ETH crowding-factor hybrid.

Decision: `research_candidate`. Trading approval: `false`.
Development-eligible configurations: `5` / `1680`.
Selected `daily` rebalance, lookback `10` days, daily volatility target `1.00%`, exposure `1.00x` to `2.00x`.

| Split | Return | Max DD | Positive months | 15% months |
|---|---:|---:|---:|---:|
| 2021-2023 discovery | 914.48% | -34.87% | 61.11% | 12/36 |
| 2024-2025 validation | 136.84% | -31.77% | 62.50% | 5/24 |
| 2026 reused confirmation | 114.60% | -18.12% | 75.00% | 4/8 |
| 2026 stress 10+5 bps | 105.12% | -19.29% | 75.00% | 4/8 |

## 2026 monthly returns

| Month | Base | Stress | Opening exposure |
|---|---:|---:|---:|
| 2026-01 | 18.69% | 17.39% | 1.43x |
| 2026-02 | 26.74% | 26.03% | 1.00x |
| 2026-03 | 8.04% | 7.79% | 2.00x |
| 2026-04 | 16.10% | 16.06% | 1.43x |
| 2026-05 | -6.06% | -6.95% | 2.00x |
| 2026-06 | 40.44% | 39.82% | 2.00x |
| 2026-07 | -14.43% | -15.16% | 1.00x |
| 2026-08 | 0.74% | 0.40% | 1.00x |

The development-selected volatility target met the reused base and stress confirmation gates; fresh forward evidence remains required.

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The ETH crowding hybrid was selected in an earlier study using 2021-2025.
- Volatility uses only prior daily closes; monthly mode holds exposure through the month.
- Exposure changes include 7 bps turnover cost in addition to component trading costs.
- Drawdown is measured at daily closes; borrowing cost and liquidation are not modeled.
