# BTC/ETH Reversal + Support/Resistance Overlay

Research-only causal H4 comparison. No paper/live execution integration.

- Data: `{"btc_perp": {"bars_15m": 232836, "bars_h4": 14552, "first": "2020-01-01T00:00:00+00:00", "funding_events": 7277, "last": "2026-08-22T04:00:00+00:00"}, "eth_perp": {"bars_15m": 232836, "bars_h4": 14552, "first": "2020-01-01T00:00:00+00:00", "funding_events": 7277, "last": "2026-08-22T04:00:00+00:00"}}`
- Signal: `closed H4 bar`; fill: `next H4 bar open`.
- Baseline: reverse BTC prior six H4-bar return; the same score drives BTC and ETH.
- Funding is included. Normal cost is 14 bps round trip; stress cost is 30 bps.

## Results

| Asset | Configuration | Period | Normal return | Stress return | Normal DD | Trades |
|---|---|---|---:|---:|---:|---:|
| btc_perp | baseline | development_2022_2025 | -87.97% | -99.21% | -91.16% | 1745 |
| btc_perp | baseline | confirmation_2026 | -55.29% | -70.27% | -56.29% | 254 |
| btc_perp | location_1p0 | development_2022_2025 | -84.82% | -98.01% | -86.57% | 1278 |
| btc_perp | location_1p0 | confirmation_2026 | -35.95% | -52.40% | -35.96% | 185 |
| btc_perp | location_1p5 | development_2022_2025 | -90.81% | -99.65% | -93.02% | 2131 |
| btc_perp | location_1p5 | confirmation_2026 | -46.83% | -67.40% | -47.56% | 305 |
| btc_perp | location_2p0 | development_2022_2025 | -87.20% | -99.54% | -92.55% | 2147 |
| btc_perp | location_2p0 | confirmation_2026 | -44.11% | -66.70% | -44.89% | 323 |
| btc_perp | room_0p75 | development_2022_2025 | -89.94% | -99.59% | -92.34% | 2090 |
| btc_perp | room_0p75 | confirmation_2026 | -57.06% | -74.13% | -57.83% | 316 |
| btc_perp | room_1p0 | development_2022_2025 | -89.43% | -99.71% | -93.99% | 2346 |
| btc_perp | room_1p0 | confirmation_2026 | -58.30% | -77.03% | -58.35% | 372 |
| btc_perp | room_1p5 | development_2022_2025 | -93.74% | -99.67% | -95.03% | 1928 |
| btc_perp | room_1p5 | confirmation_2026 | -57.39% | -75.06% | -57.44% | 334 |
| btc_perp | location_room_1p5_1p0 | development_2022_2025 | -77.08% | -99.01% | -87.67% | 1989 |
| btc_perp | location_room_1p5_1p0 | confirmation_2026 | -41.26% | -63.46% | -41.26% | 296 |
| btc_perp | location_room_2p0_1p0 | development_2022_2025 | -77.86% | -99.39% | -90.96% | 2277 |
| btc_perp | location_room_2p0_1p0 | confirmation_2026 | -47.62% | -70.64% | -47.62% | 361 |
| eth_perp | baseline | development_2022_2025 | -95.39% | -99.72% | -96.39% | 1745 |
| eth_perp | baseline | confirmation_2026 | -69.78% | -79.92% | -71.66% | 254 |
| eth_perp | location_1p0 | development_2022_2025 | -97.52% | -99.66% | -97.63% | 1242 |
| eth_perp | location_1p0 | confirmation_2026 | -31.24% | -49.48% | -32.55% | 192 |
| eth_perp | location_1p5 | development_2022_2025 | -99.18% | -99.97% | -99.34% | 2077 |
| eth_perp | location_1p5 | confirmation_2026 | -57.07% | -73.48% | -57.17% | 300 |
| eth_perp | location_2p0 | development_2022_2025 | -99.04% | -99.97% | -99.24% | 2124 |
| eth_perp | location_2p0 | confirmation_2026 | -60.01% | -75.82% | -60.10% | 313 |
| eth_perp | room_0p75 | development_2022_2025 | -97.34% | -99.91% | -97.94% | 2129 |
| eth_perp | room_0p75 | confirmation_2026 | -72.46% | -83.25% | -74.13% | 309 |
| eth_perp | room_1p0 | development_2022_2025 | -97.66% | -99.94% | -98.00% | 2337 |
| eth_perp | room_1p0 | confirmation_2026 | -66.43% | -81.71% | -67.47% | 378 |
| eth_perp | room_1p5 | development_2022_2025 | -97.74% | -99.89% | -97.85% | 1891 |
| eth_perp | room_1p5 | confirmation_2026 | -62.30% | -76.95% | -63.48% | 306 |
| eth_perp | location_room_1p5_1p0 | development_2022_2025 | -98.83% | -99.95% | -98.97% | 1946 |
| eth_perp | location_room_1p5_1p0 | confirmation_2026 | -44.41% | -65.03% | -44.41% | 289 |
| eth_perp | location_room_2p0_1p0 | development_2022_2025 | -98.81% | -99.97% | -98.94% | 2260 |
| eth_perp | location_room_2p0_1p0 | confirmation_2026 | -54.29% | -73.60% | -54.29% | 342 |

## Interpretation

Configurations are diagnostic candidates, not a parameter-selected strategy. The development period is 2022-2025; 2026 is reported separately as confirmation. The target project's probability models are not used, and no target source is copied into MMTICK.

