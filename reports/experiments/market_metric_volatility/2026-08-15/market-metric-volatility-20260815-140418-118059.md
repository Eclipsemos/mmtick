# market-metric-volatility-20260815-140418-118059

Research-only causal volatility target on the frozen ETH crowding-factor hybrid.

Decision: `rejected_after_confirmation`. Trading approval: `false`.
Development-eligible configurations: `149` / `1680`.
Non-selective confirmation diagnostic: `0` / `149` met gates.
Selected `daily` rebalance, lookback `60` days, daily volatility target `2.00%`, exposure `0.50x` to `2.00x`.

| Split | Return | Max DD | Positive months | 15% months |
|---|---:|---:|---:|---:|
| 2021-2023 discovery | 809.26% | -33.53% | 61.11% | 10/36 |
| 2024-2025 validation | 136.23% | -27.80% | 54.17% | 5/24 |
| 2026 reused confirmation | 78.70% | -21.16% | 75.00% | 3/8 |
| 2026 stress 10+5 bps | 58.76% | -23.44% | 62.50% | 3/8 |

## 2026 monthly returns

| Month | Base | Stress | Mean exposure |
|---|---:|---:|---:|
| 2026-01 | 23.55% | 19.20% | 1.66x |
| 2026-02 | 16.93% | 15.14% | 0.92x |
| 2026-03 | 5.74% | 4.81% | 0.81x |
| 2026-04 | 9.38% | 8.33% | 1.34x |
| 2026-05 | -15.98% | -18.19% | 1.73x |
| 2026-06 | 32.05% | 31.49% | 0.96x |
| 2026-07 | -4.15% | -5.18% | 0.89x |
| 2026-08 | 0.58% | -0.11% | 1.63x |

The development-selected volatility target failed base or stress monthly coverage, return, or drawdown gates.

## Limitations

- 2026 is reused confirmation evidence and is not a fresh holdout.
- The ETH crowding hybrid was selected in an earlier study using 2021-2025.
- Volatility uses only prior daily closes; monthly mode holds exposure through the month.
- Exposure changes include 7 bps turnover cost in addition to component trading costs.
- Drawdown is measured at daily closes; borrowing cost and liquidation are not modeled.
