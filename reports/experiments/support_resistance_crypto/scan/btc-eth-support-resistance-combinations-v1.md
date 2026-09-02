# BTC/ETH Support/Resistance Combinations

Research-only diagnostic; no live or paper execution integration.

- New configurations: `128` across `breakout_highvol, breakout_mtf, breakout_own, fade_lowvol, fade_mtf, fade_own`.
- D1 states are lagged to the last completed UTC day; costs include funding, 14 bps normal and 30 bps stress.
- Ranking uses 2024–2025 validation only; 2026 is confirmation, not a selection holdout.

## Validation Leaders

| Name | Family | Asset | Validation | Trades | PF | DD | 2026 | 2026 stress |
|---|---|---|---:|---:|---:|---:|---:|---:|
| breakout_mtf_eth_perp_d2.5_h18 | breakout_mtf | eth_perp | 147.87% | 79 | 1.46 | -26.32% | 16.54% | 11.80% |
| breakout_highvol_eth_perp_m0.75_h18 | breakout_highvol | eth_perp | 122.82% | 75 | 1.46 | -24.72% | 18.49% | 14.40% |
| fade_mtf_btc_perp_d2.5_h18 | fade_mtf | btc_perp | 113.49% | 171 | 1.29 | -27.90% | -31.57% | -37.37% |
| fade_mtf_btc_perp_d4_h18 | fade_mtf | btc_perp | 100.90% | 170 | 1.29 | -33.12% | -40.39% | -45.44% |
| breakout_mtf_eth_perp_d2.5_h12 | breakout_mtf | eth_perp | 94.72% | 87 | 1.37 | -25.26% | 10.90% | 6.02% |
| fade_mtf_eth_perp_d1.5_h18 | fade_mtf | eth_perp | 94.31% | 172 | 1.16 | -41.88% | -44.37% | -49.37% |
| breakout_mtf_eth_perp_d2.5_h24 | breakout_mtf | eth_perp | 88.86% | 73 | 1.32 | -31.99% | 10.07% | 6.06% |
| breakout_mtf_eth_perp_d4_h18 | breakout_mtf | eth_perp | 82.69% | 85 | 1.27 | -30.21% | 27.14% | 21.79% |
| fade_own_btc_perp_l1_h18 | fade_own | btc_perp | 81.73% | 162 | 1.25 | -27.55% | -47.15% | -51.57% |
| breakout_mtf_eth_perp_d6_h18 | breakout_mtf | eth_perp | 77.59% | 87 | 1.25 | -30.54% | 27.14% | 21.79% |
| breakout_highvol_eth_perp_m0.75_h12 | breakout_highvol | eth_perp | 75.16% | 81 | 1.35 | -23.78% | 6.05% | 2.37% |
| fade_own_btc_perp_l1_h24 | fade_own | btc_perp | 73.60% | 132 | 1.24 | -46.05% | -30.36% | -34.95% |
| fade_mtf_btc_perp_d4_h6 | fade_mtf | btc_perp | 69.98% | 327 | 1.21 | -29.07% | -34.60% | -45.09% |
| fade_own_btc_perp_l2_h18 | fade_own | btc_perp | 67.37% | 196 | 1.12 | -42.55% | -1.06% | -10.54% |
| breakout_mtf_eth_perp_d4_h12 | breakout_mtf | eth_perp | 64.30% | 94 | 1.25 | -29.45% | 22.42% | 16.87% |
| breakout_mtf_eth_perp_d6_h12 | breakout_mtf | eth_perp | 62.71% | 95 | 1.25 | -30.97% | 22.42% | 16.87% |
| breakout_highvol_eth_perp_m0.75_h24 | breakout_highvol | eth_perp | 60.97% | 70 | 1.22 | -37.88% | 26.66% | 22.87% |
| breakout_highvol_eth_perp_m1_h24 | breakout_highvol | eth_perp | 60.49% | 47 | 1.34 | -29.16% | 48.17% | 45.44% |
| fade_mtf_eth_perp_d1.5_h6 | fade_mtf | eth_perp | 58.90% | 327 | 1.12 | -41.19% | -62.32% | -68.16% |
| breakout_highvol_eth_perp_m1_h18 | breakout_highvol | eth_perp | 54.57% | 52 | 1.41 | -24.86% | 26.67% | 24.09% |

## 50/50 Portfolios

| Portfolio | Validation | Validation stress | Validation DD | 2026 | 2026 stress | 2026 DD |
|---|---:|---:|---:|---:|---:|---:|
| 50_50_fade_btc_perp_l1_e0_h12__breakout_eth_perp_b0_h12 | 58.64% | 20.29% | -22.51% | 33.46% | 22.35% | -14.00% |
| 50_50_fade_btc_perp_l2_e2.5_h12__breakout_eth_perp_b0_h18 | 74.20% | 46.12% | -14.30% | 17.28% | 11.18% | -12.86% |
| 50_50_fade_own_btc_perp_l1_h12__breakout_own_eth_perp_h12 | 6.35% | -10.34% | -16.33% | -11.72% | -16.54% | -12.90% |

A positive validation result is not sufficient for promotion. Review full JSON for stress results, trade counts, funding, and phase stability; candidates remain research-only.

