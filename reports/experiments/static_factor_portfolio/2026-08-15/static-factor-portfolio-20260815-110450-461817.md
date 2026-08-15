# static-factor-portfolio-20260815-110450-461817

Research-only development-selected static BTC/ETH factor portfolio.

Decision: `rejected_after_confirmation`. Trading approval: `false`.
Selected `4` sleeves at `4.00x` portfolio leverage.

| Sleeve | Weight | Market | Family |
|---|---:|---|---|
| `lead_lag` | 40.00% | eth_perp | lead_lag |
| `event-eth_perp-to-eth_perp-continuation-60d-threshold-2p5-hold-12x4h-none-long_only` | 15.00% | eth_perp | shock_event |
| `event-btc_perp-to-btc_perp-continuation-15d-threshold-2-hold-4x4h-none-long_short` | 30.00% | btc_perp | shock_event |
| `event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-hold-12x4h-underreaction-long_short` | 15.00% | btc_perp | shock_event |

| Split | Return | Daily-close max DD | Positive months | 25% months |
|---|---:|---:|---:|---:|
| 2021-2023 discovery | 1388.74% | -34.22% | 55.56% | 7/36 |
| 2024-2025 validation | 214.45% | -34.56% | 58.33% | 5/24 |
| 2026 reused confirmation | 184.88% | -18.29% | 75.00% | 3/8 |
| 2026 stress 10+5 bps | 154.90% | -20.57% | 62.50% | 3/8 |

## Development-selected size comparison

| Sleeves | Leverage | Discovery | Validation | Confirmation | Max DD | 25% months |
|---:|---:|---:|---:|---:|---:|---:|
| 3 | 4.00x | 1725.14% | 299.65% | 209.55% | -22.34% | 3/8 |
| 4 | 4.00x | 1388.74% | 214.45% | 184.88% | -18.29% | 3/8 |

## 2026 monthly returns

| Month | Base | Stress |
|---|---:|---:|
| 2026-01 | 40.24% | 36.09% |
| 2026-02 | 53.86% | 52.43% |
| 2026-03 | 10.18% | 8.97% |
| 2026-04 | 7.54% | 6.53% |
| 2026-05 | -12.73% | -14.66% |
| 2026-06 | 39.39% | 38.84% |
| 2026-07 | -8.75% | -10.39% |
| 2026-08 | 0.38% | -0.30% |

The development-selected static portfolio did not reach 25% in at least four of eight reused confirmation months while retaining the drawdown and stress gates.

## Limitations

- 2026 has been viewed repeatedly and is confirmation evidence, not a fresh holdout.
- Combination shortlisting uses development monthly endpoints before daily risk checks.
- Portfolio drawdown is measured at daily closes, not synchronized component bars.
- Borrowing cost, cross-margin liquidation, market impact, and exchange failure are not modeled.
