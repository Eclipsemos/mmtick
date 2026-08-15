# adaptive-factor-portfolio-20260815-095232-940458

Research-only causal monthly-rotated factor portfolio.

Decision: `rejected_after_confirmation`.
Universe candidates: `2,988`; discovery eligible: `152`; frozen sleeves including lead-lag: `41`.

Selected: `adaptive-60d-top-3-calmar-equal-leverage-3-loss-0p10-anchor-0p25`.

| Split | Return | Daily-close max DD | Positive months | 25% months | Rebalance costs |
|---|---:|---:|---:|---:|---:|
| selection | 487.84% | -34.74% | 54.17% | 25.00% | 17300.88 |
| confirmation | -1.44% | -23.53% | 50.00% | 0.00% | 2171.70 |
| stress confirmation | -1.76% | -17.24% | 50.00% | 0.00% | 4535.20 |

## Confirmation monthly returns

| Month | Return |
|---|---:|
| 2026-01 | -10.47% |
| 2026-02 | 10.11% |
| 2026-03 | 11.23% |
| 2026-04 | 14.06% |
| 2026-05 | -13.91% |
| 2026-06 | 6.34% |
| 2026-07 | -11.75% |
| 2026-08 | -2.46% |

## Confirmation allocations

| Month | Sleeves | Turnover | Cost |
|---|---|---:|---:|
| 2026-01 | btc_perp-momentum-1440m-60-0p1-long_short 25%, event-eth_perp-to-btc_perp-reversal-15d-threshold-2p5-hold-8x4h-none-long_short 25%, event-eth_perp-to-btc_perp-reversal-60d-threshold-2p5-hold-8x4h-none-long_only 25%, lead_lag 25% | 0.00x | 0.00 |
| 2026-02 | btc_perp-donchian-240m-42-12-long_short 25%, event-btc_perp-to-btc_perp-reversal-60d-threshold-2p5-hold-8x4h-none-long_only 25%, event-eth_perp-to-btc_perp-reversal-60d-threshold-2p5-hold-8x4h-none-long_only 25%, lead_lag 25% | 3.00x | 188.02 |
| 2026-03 | event-btc_perp-to-btc_perp-continuation-15d-threshold-2-hold-4x4h-none-long_short 25%, event-btc_perp-to-btc_perp-continuation-30d-threshold-1p5-hold-4x4h-none-long_short 25%, event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-hold-12x4h-underreaction-long_short 25%, lead_lag 25% | 4.50x | 310.55 |
| 2026-04 | eth_perp-240m-long_short-ret_4_z-ret_16_z-sub-threshold-1 25%, event-btc_perp-to-btc_perp-continuation-15d-threshold-2-hold-4x4h-none-long_short 25%, event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-hold-12x4h-underreaction-long_short 25%, lead_lag 25% | 1.50x | 115.15 |
| 2026-05 | eth_perp-240m-long_short-ret_4_z-ret_16_z-sub-threshold-1 25%, event-btc_perp-to-btc_perp-continuation-60d-threshold-1p5-hold-4x4h-none-long_short 25%, event-eth_perp-to-btc_perp-continuation-60d-threshold-1p5-hold-12x4h-underreaction-long_short 25%, lead_lag 25% | 1.50x | 131.33 |
| 2026-06 | event-btc_perp-to-btc_perp-reversal-60d-threshold-2p5-hold-8x4h-none-long_only 25%, event-eth_perp-to-btc_perp-reversal-60d-threshold-2p5-hold-8x4h-none-long_only 25%, event-eth_perp-to-eth_perp-reversal-30d-threshold-2p5-hold-8x4h-none-long_only 25%, lead_lag 25% | 3.00x | 226.12 |
| 2026-07 | btc_perp-donchian-240m-42-12-long_short 25%, btc_perp-donchian-60m-168-48-long_short 25%, eth_perp-momentum-1440m-20-0p05-long_only 25%, lead_lag 25% | 4.50x | 360.68 |
| 2026-08 | eth_perp-ema-240m-24-96-long_only 25%, event-eth_perp-to-eth_perp-continuation-15d-threshold-2p5-hold-12x4h-none-long_only 25%, event-eth_perp-to-eth_perp-continuation-60d-threshold-2p5-hold-12x4h-none-long_only 25%, lead_lag 25% | 3.00x | 212.19 |

The development-selected adaptive portfolio did not pass confirmation monthly return, drawdown, and cost-stress gates.

## Limitations

- 2026 has been viewed in prior studies and is not a fresh independent holdout.
- Joint portfolio drawdown is measured at daily closes, not every component bar.
- Monthly allocation turnover is modeled, but cross-margin liquidation is not.
- Market impact and exchange failure are not modeled.
