# Adaptive BTC/ETH Factor Portfolio

This experiment tests whether causal monthly rotation can avoid the fixed second-sleeve failure.
The universe is selected using 2021-2023 only, then frozen before any configuration is evaluated on
2024-2025. The 2026 interval is used only after lookback, ranking, weighting, leverage, anchor, and
loss-limit settings are fixed.

The replay includes component trading costs and historical funding. Monthly allocation changes
pay 7 bps on gross notional turnover, or 15 bps in the stress replay. Sleeve equity compounds
independently within each month, so there is no free daily rebalancing.

## Result

The experiment is rejected.

- 2,988 factor sleeves were evaluated in 2021-2023.
- 152 passed the discovery gates; family caps produced a frozen universe of 40 sleeves plus the
  existing lead-lag factor.
- 591 of 1,620 rotation configurations passed the 2024-2025 risk gates.
- The selected configuration uses a 60-day Calmar ranking, equal weights across the top three,
  a permanent 25% lead-lag anchor, 3x portfolio leverage, and a 10% monthly loss limit.

| Split | Return | Daily-close max DD | Positive months | 25% months |
|---|---:|---:|---:|---:|
| 2024-2025 selection | +487.84% | -34.74% | 54.17% | 6/24 |
| 2026 reused confirmation | -1.44% | -23.53% | 50.00% | 0/8 |
| 2026 stressed costs | -1.76% | -17.24% | 50.00% | 0/8 |

The failure is not explained by transaction costs. The same configuration was negative before and
after cost stress, and no confirmation month reached 25%. The factors with the strongest trailing
60-day Calmar ratios repeatedly failed to retain leadership in the following month. This rejects
simple trailing-performance rotation; searching more nearby windows would reuse the same failed
hypothesis.

The authoritative artifacts are
[`adaptive-factor-portfolio-20260815-095232-940458.md`](adaptive-factor-portfolio-20260815-095232-940458.md)
and its adjacent JSON file. The JSON includes every 2026 monthly allocation and modeled turnover
cost.
